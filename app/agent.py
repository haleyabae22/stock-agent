# app/agent.py
# ---------------------------------------------------------------
# Agentic runner — full pipeline:
#
#   1. Load favorites from watchlist DB       → display table
#   2. Run Finviz screener                    → criteria-filtered picks
#   3. Promote new picks to DB                → display table
#   4. Fetch price history for ALL tickers
#   5. Calculate SMA8 / SMA20 signals         → display signal table
#   6. Paper-trade every BUY signal           → simulator
#   7. Display bought stocks + portfolio P&L
# ---------------------------------------------------------------

from datetime import date

from app.fetcher   import fetch_stock
from app.storage   import save_data
from app.watchlist import get_tickers, list_all, add_ticker
from app.screener  import get_screener_tickers
from app.signals   import calculate_smas, get_signal, signal_summary
from app.simulator import PaperTrader

# ── Shared PaperTrader instance ──────────────────────────────────
# Module-level so it persists across scheduler calls in the same
# process. Replace with DB-backed state for cross-restart persistence.
trader = PaperTrader(starting_cash=10_000)

# ── ANSI colour helpers ──────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
RED     = "\033[31m"
WHITE   = "\033[97m"
MAGENTA = "\033[35m"

WIDTH = 72


def _hr(char: str = "─") -> str:
    return char * WIDTH


def _banner(title: str) -> None:
    print()
    print(f"  {BOLD}{CYAN}{_hr('═')}{RESET}")
    print(f"  {BOLD}{CYAN}  {title}{RESET}")
    print(f"  {BOLD}{CYAN}{_hr('═')}{RESET}")


def _section(title: str, color: str = WHITE) -> None:
    print()
    print(f"  {BOLD}{color}{title}{RESET}")
    print(f"  {_hr()}")


# ── Table renderers ──────────────────────────────────────────────

def _table_watchlist(tickers: list, label: str, color: str) -> None:
    """Simple A-Z ticker table with a status label."""
    if not tickers:
        print(f"  {DIM}None.{RESET}")
        return
    print(f"  {BOLD}{'#':<5}{'Ticker':<14}{'Status'}{RESET}")
    print(f"  {_hr()}")
    for i, t in enumerate(sorted(tickers), 1):
        print(f"  {i:<5}{BOLD}{WHITE}{t:<14}{RESET}{color}{label}{RESET}")
    print(f"  {_hr()}")
    print(f"  {DIM}{len(tickers)} ticker(s){RESET}")


def _table_signals(rows: list) -> None:
    """
    Signal scan table — one row per ticker.
    rows: list of signal_summary dicts with an added 'fetch_ok' bool.
    """
    if not rows:
        print(f"  {DIM}No data.{RESET}")
        return

    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Close':>8}"
        f"{'SMA8':>9}{'SMA20':>9}{'Gap%':>7}  {'Signal'}{RESET}"
    )
    print(f"  {_hr()}")

    for i, r in enumerate(sorted(rows, key=lambda x: x["ticker"]), 1):
        if not r.get("fetch_ok"):
            print(f"  {i:<5}{BOLD}{WHITE}{r['ticker']:<10}{RESET}{RED}fetch error{RESET}")
            continue

        sig   = r.get("signal", "HOLD")
        close = r.get("close",   0.0)
        sma8  = r.get("sma8",    0.0)
        sma20 = r.get("sma20",   0.0)
        gap   = r.get("gap_pct", 0.0)

        if sig == "BUY":
            sig_str = f"{GREEN}{BOLD}▲ BUY{RESET}"
        elif sig == "SELL":
            sig_str = f"{RED}{BOLD}▼ SELL{RESET}"
        else:
            sig_str = f"{DIM}  HOLD{RESET}"

        gap_col = GREEN if gap >= 0 else RED
        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{r['ticker']:<10}{RESET}"
            f"${close:>7.2f}"
            f"${sma8:>8.2f}"
            f"${sma20:>8.2f}"
            f"  {gap_col}{gap:>+6.2f}%{RESET}"
            f"  {sig_str}"
        )

    buys = sum(1 for r in rows if r.get("signal") == "BUY")
    print(f"  {_hr()}")
    print(
        f"  {DIM}{len(rows)} scanned  {RESET}"
        f"{GREEN}{buys} BUY signal(s){RESET}"
    )


def _table_bought(buy_log: list) -> None:
    """Table of BUY trades executed in the current run (today only)."""
    if not buy_log:
        print(f"  {DIM}No buys executed this run.{RESET}")
        return

    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Date':<13}"
        f"{'Price':>8}{'Shares':>10}{'Cost':>10}{RESET}"
    )
    print(f"  {_hr()}")
    total_cost = 0.0
    for i, e in enumerate(buy_log, 1):
        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{e['ticker']:<10}{RESET}"
            f"{str(e['date']):<13}"
            f"${e['entry_price']:>7.2f}"
            f"{e['shares']:>10.4f}"
            f"{GREEN}${e['cost']:>9.2f}{RESET}"
        )
        total_cost += e["cost"]
    print(f"  {_hr()}")
    print(
        f"  {BOLD}{'TOTAL':<5}{'':<10}{'':<13}{'':>8}{'':>10}"
        f"{GREEN}${total_cost:>9.2f}{RESET}"
    )
    print(f"  {DIM}{len(buy_log)} buy(s){RESET}")


def _table_portfolio(positions: dict, price_map: dict, as_of: date) -> None:
    """All open positions with unrealized P&L."""
    if not positions:
        print(f"  {DIM}No open positions yet.{RESET}")
        return

    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Bought':<12}"
        f"{'Entry $':>8}{'Now $':>8}{'Shares':>10}"
        f"{'P&L $':>10}{'P&L %':>8}{'Days':>6}{RESET}"
    )
    print(f"  {_hr()}")

    total_pnl = 0.0
    for i, (ticker, pos) in enumerate(sorted(positions.items()), 1):
        price   = price_map.get(ticker, pos.entry_price)
        pnl     = pos.unrealized_pnl(price)
        pnl_pct = pos.unrealized_pnl_pct(price)
        days    = pos.days_held(as_of)
        col     = GREEN if pnl >= 0 else RED
        total_pnl += pnl

        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{ticker:<10}{RESET}"
            f"{str(pos.entry_date):<12}"
            f"${pos.entry_price:>7.2f}"
            f"${price:>7.2f}"
            f"{pos.shares:>10.4f}"
            f"  {col}{pnl:>+8.2f}{RESET}"
            f"  {col}{pnl_pct:>+6.1f}%{RESET}"
            f"  {days:>3}d"
        )

    col = GREEN if total_pnl >= 0 else RED
    print(f"  {_hr()}")
    print(
        f"  {BOLD}{'TOTAL':<5}{'':>56}"
        f"  {col}{total_pnl:>+8.2f}{RESET}"
    )
    print(f"  {DIM}{len(positions)} position(s){RESET}")


# ── Main entry point ─────────────────────────────────────────────

def run_agent() -> None:
    today = date.today()

    _banner(f"STOCK AGENT  ·  {today.isoformat()}")

    # ── 1. Load existing favorites ───────────────────────────────
    _section("① EXISTING FAVORITES", YELLOW)
    watchlist_rows = list_all()
    watchlist      = [r["ticker"] for r in watchlist_rows if r["active"]]
    watchlist_set  = set(watchlist)
    _table_watchlist(watchlist, "Favorite  ★", YELLOW)

    # ── 2. Run Finviz screener ───────────────────────────────────
    _section(
        "② FINVIZ SCREENER  "
        "(RSI<60 · Price>SMA50 · Price>SMA200 · Beta>1 · ATR>1 · Vol>500K)",
        GREEN,
    )
    print(f"  {DIM}Running screener …{RESET}")
    screener_picks = get_screener_tickers()
    new_picks      = sorted(t for t in screener_picks if t not in watchlist_set)

    if new_picks:
        _table_watchlist(new_picks, "New pick  →  Promoting", GREEN)
    else:
        print(f"  {DIM}No new picks — every match is already a favorite.{RESET}")

    # Promote new picks to the watchlist DB
    promoted = []
    for ticker in new_picks:
        try:
            add_ticker(ticker, notes="finviz screener")
            promoted.append(ticker)
            watchlist_set.add(ticker)
        except Exception as exc:
            print(f"  {RED}✗ Could not promote {ticker}: {exc}{RESET}")

    if promoted:
        print(
            f"\n  {GREEN}✓ Promoted {len(promoted)} ticker(s): "
            f"{', '.join(promoted)}{RESET}"
        )

    # ── 3. Fetch price history + calculate SMA signals ──────────
    all_tickers   = sorted(watchlist_set)
    signal_rows   = []
    price_map     = {}   # ticker → latest close (for portfolio valuation)
    fetch_results = []

    _section(f"③ SIGNAL SCAN  ({len(all_tickers)} ticker(s)  ·  SMA8 / SMA20 crossover)", CYAN)
    print(f"  {DIM}Fetching 30-day price history …{RESET}\n")

    for ticker in all_tickers:
        print(f"  {DIM}  fetching {ticker} …{RESET}", end="\r")
        try:
            df = fetch_stock(ticker)

            if df is None or df.empty:
                signal_rows.append({"ticker": ticker, "fetch_ok": False})
                fetch_results.append({"ticker": ticker, "ok": False, "error": "No data"})
                continue

            save_data(df)

            df_sig  = calculate_smas(df)
            summary = signal_summary(ticker, df_sig)
            summary["fetch_ok"] = True
            signal_rows.append(summary)

            price_map[ticker] = float(df["close"].iloc[-1])
            fetch_results.append({"ticker": ticker, "ok": True, "error": ""})

        except Exception as exc:
            signal_rows.append({"ticker": ticker, "fetch_ok": False})
            fetch_results.append({"ticker": ticker, "ok": False, "error": str(exc)})

    print(" " * 50, end="\r")   # clear rolling status line
    _table_signals(signal_rows)

    # ── 4. Execute paper trades on BUY signals ───────────────────
    _section("④ PAPER TRADES  (executes on SMA8 crosses above SMA20)", MAGENTA)
    buy_signals = [r for r in signal_rows if r.get("signal") == "BUY" and r.get("fetch_ok")]

    if buy_signals:
        print(
            f"  {GREEN}{len(buy_signals)} BUY signal(s) — "
            f"executing paper trades …{RESET}\n"
        )
        for r in buy_signals:
            trader.handle_signal(
                ticker        = r["ticker"],
                signal        = "BUY",
                current_price = r["close"],
                as_of         = today,
            )
    else:
        print(f"  {DIM}No BUY signals today — no paper trades executed.{RESET}")

    # ── 5. Stocks bought this run ────────────────────────────────
    _section("⑤ BOUGHT THIS RUN", GREEN)
    todays_buys = [e for e in trader._buy_log if e["date"] == today]
    _table_bought(todays_buys)

    # ── 6. Full open portfolio ───────────────────────────────────
    _section("⑥ OPEN PORTFOLIO  (all positions since agent started)", CYAN)
    _table_portfolio(trader.open_positions(), price_map, as_of=today)

    # ── 7. One-line portfolio summary ────────────────────────────
    port_val  = trader.portfolio_value(price_map)
    total_pnl = trader.total_pnl(price_map)
    pnl_pct   = trader.total_pnl_pct(price_map)
    pnl_col   = GREEN if total_pnl >= 0 else RED

    print()
    print(f"  {_hr()}")
    print(
        f"  {BOLD}Cash {WHITE}${trader.cash:>9,.2f}{RESET}  "
        f"{BOLD}Holdings {WHITE}${port_val - trader.cash:>9,.2f}{RESET}  "
        f"{BOLD}Portfolio {WHITE}${port_val:>9,.2f}{RESET}  "
        f"{BOLD}P&L {pnl_col}{total_pnl:>+9.2f}  ({pnl_pct:>+.1f}%){RESET}"
    )
    print(f"  {_hr()}")

    # ── 8. Fetch summary ─────────────────────────────────────────
    _section("⑦ FETCH SUMMARY", WHITE)
    ok  = sum(1 for r in fetch_results if r["ok"])
    err = len(fetch_results) - ok
    print(
        f"  {GREEN}✓ {ok} fetched{RESET}  "
        f"{RED}✗ {err} failed{RESET}  "
        f"{DIM}{len(fetch_results)} total{RESET}"
    )
    for r in fetch_results:
        if not r["ok"]:
            print(f"    {RED}✗ {r['ticker']}: {r['error']}{RESET}")

    print()
    print(f"  {BOLD}{DIM}Run complete — {today.isoformat()}{RESET}")
    print(f"  {_hr('═')}\n")


if __name__ == "__main__":
    run_agent()