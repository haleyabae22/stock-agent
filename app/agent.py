# app/agent.py
# ─────────────────────────────────────────────────────────────────
# Three entry points:
#
#   run_agent_intraday(report_mode)
#       Called every 10-minute candle by scheduler.py.
#       report_mode = "crossover"    → fires the moment SMA8 > SMA20
#                                      (uses the CURRENT live candle price)
#       report_mode = "candle_close" → fires only when the bar has CLOSED
#                                      (uses the completed candle's close)
#
#   run_agent_eod()
#       Called once at 1:01 PM PT.  Runs the full daily pipeline:
#       screener → promote → fetch daily bars → EOD signals → portfolio.
#
#   run_agent()   ← kept for manual / test runs (alias for eod)
#
# Candle-close logic
# ──────────────────
# yfinance "10m" interval returns bars whose timestamp is the BAR OPEN.
# A bar that opened at 09:00 closes at 09:10.
# We fire the scheduler 30 s before each bar close (at :09:30, :19:30…).
# When the job runs, the MOST RECENT completed bar is the previous one.
#
#   Example: scheduler fires at 09:19:30
#     → yfinance returns bars up to the 09:10 open (09:10–09:20 bar)
#     → that bar closed at 09:20, which is "now" from the market's view
#     → we use df.iloc[-1] (the last complete bar) — correct candle close
#
# For "crossover" mode the scheduler still fires at the same cadence but
# we additionally check the LIVE (incomplete) bar price via a spot quote.
# ─────────────────────────────────────────────────────────────────

import pytz
from datetime import date, datetime

import yfinance as yf

from app.fetcher   import fetch_stock, fetch_intraday
from app.storage   import save_data
from app.watchlist import list_all, add_ticker
from app.screener  import get_screener_tickers
from app.signals   import calculate_smas, get_signal, signal_summary
from app.simulator import PaperTrader

# pandas as pd
from .fetcher import pd

# ── Shared state ─────────────────────────────────────────────────
trader = PaperTrader(starting_cash=10_000)
PT     = pytz.timezone("America/Los_Angeles")

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
BLUE    = "\033[34m"

WIDTH = 72


def _hr(char: str = "─") -> str:
    return char * WIDTH


def _banner(title: str, color: str = CYAN) -> None:
    print()
    print(f"  {BOLD}{color}{_hr('═')}{RESET}")
    print(f"  {BOLD}{color}  {title}{RESET}")
    print(f"  {BOLD}{color}{_hr('═')}{RESET}")


def _section(title: str, color: str = WHITE) -> None:
    print()
    print(f"  {BOLD}{color}{title}{RESET}")
    print(f"  {_hr()}")


# ── Table helpers ────────────────────────────────────────────────

def _table_watchlist(tickers: list, label: str, color: str) -> None:
    if not tickers:
        print(f"  {DIM}None.{RESET}")
        return
    print(f"  {BOLD}{'#':<5}{'Ticker':<14}{'Status'}{RESET}")
    print(f"  {_hr()}")
    for i, t in enumerate(sorted(tickers), 1):
        print(f"  {i:<5}{BOLD}{WHITE}{t:<14}{RESET}{color}{label}{RESET}")
    print(f"  {_hr()}")
    print(f"  {DIM}{len(tickers)} ticker(s){RESET}")


def _table_signals(rows: list, candle_label: str = "") -> None:
    if not rows:
        print(f"  {DIM}No data.{RESET}")
        return

    header_extra = f"  Candle: {candle_label}" if candle_label else ""
    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Close':>8}"
        f"{'SMA8':>9}{'SMA20':>9}{'Gap%':>7}  {'Signal'}"
        f"{header_extra}{RESET}"
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
    print(f"  {DIM}{len(rows)} scanned  {RESET}{GREEN}{buys} BUY signal(s){RESET}")


def _table_bought(buy_log: list) -> None:
    if not buy_log:
        print(f"  {DIM}No buys executed this run.{RESET}")
        return
    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Time':<16}"
        f"{'Price':>8}{'Shares':>10}{'Cost':>10}{RESET}"
    )
    print(f"  {_hr()}")
    total_cost = 0.0
    for i, e in enumerate(buy_log, 1):
        ts = e.get("timestamp", e.get("date", ""))
        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{e['ticker']:<10}{RESET}"
            f"{str(ts):<16}"
            f"${e['entry_price']:>7.2f}"
            f"{e['shares']:>10.4f}"
            f"{GREEN}${e['cost']:>9.2f}{RESET}"
        )
        total_cost += e["cost"]
    print(f"  {_hr()}")
    print(f"  {BOLD}{'TOTAL':<5}{'':<10}{'':<16}{'':>8}{'':>10}"
          f"{GREEN}${total_cost:>9.2f}{RESET}")
    print(f"  {DIM}{len(buy_log)} buy(s){RESET}")


def _table_portfolio(positions: dict, price_map: dict, as_of: date) -> None:
    if not positions:
        print(f"  {DIM}No open positions yet.{RESET}")
        return
    print(
        f"  {BOLD}{'#':<5}{'Ticker':<10}{'Bought':<16}"
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
            f"{str(pos.entry_date):<16}"
            f"${pos.entry_price:>7.2f}"
            f"${price:>7.2f}"
            f"{pos.shares:>10.4f}"
            f"  {col}{pnl:>+8.2f}{RESET}"
            f"  {col}{pnl_pct:>+6.1f}%{RESET}"
            f"  {days:>3}d"
        )
    col = GREEN if total_pnl >= 0 else RED
    print(f"  {_hr()}")
    print(f"  {BOLD}{'TOTAL':<5}{'':>60}  {col}{total_pnl:>+8.2f}{RESET}")
    print(f"  {DIM}{len(positions)} position(s){RESET}")


# ── Live spot price (for crossover mode) ─────────────────────────

def _get_live_price(ticker: str) -> float | None:
    """
    Fetch the most recent trade price using yfinance fast_info.
    Returns None on failure. Used only in 'crossover' mode.
    """
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.last_price)
    except Exception:
        return None


# ── Candle-close price extractor ─────────────────────────────────

def _candle_close_price(df: pd.DataFrame) -> float:
    """
    Return the close price of the most recently COMPLETED 10-min candle.

    The scheduler fires 30 s before bar close, so df.iloc[-1] is the
    bar that is just finishing — its close price is confirmed.
    """
    return float(df["close"].iloc[-1])


def _current_candle_label(df: pd.DataFrame) -> str:
    """Return a human-readable label for the current candle e.g. '09:10 PT'."""
    if "candle" in df.columns:
        return str(df["candle"].iloc[-1])
    return datetime.now(PT).strftime("%H:%M PT")


# ─────────────────────────────────────────────────────────────────
# INTRADAY AGENT  (called every 10-minute candle)
# ─────────────────────────────────────────────────────────────────

def run_agent_intraday(report_mode: str = "candle_close") -> None:
    """
    Intraday scan on 10-minute candles.

    report_mode options
    ───────────────────
    "candle_close"
        Uses the close price of the most recently completed 10-min bar.
        Signal is only reported once the bar has fully closed — cleaner,
        no mid-bar noise.  This is the DEFAULT.

    "crossover"
        Detects SMA crossovers using LIVE mid-candle price from yfinance
        fast_info.  Fires the moment the cross is detected — faster alert
        but can trigger on wicks that retrace before bar close.
    """
    now_pt = datetime.now(PT)
    mode_label = (
        f"{GREEN}CANDLE CLOSE{RESET}" if report_mode == "candle_close"
        else f"{YELLOW}CROSSOVER (live){RESET}"
    )

    _banner(
        f"INTRADAY SCAN  ·  {now_pt.strftime('%H:%M PT')}  ·  "
        f"mode={report_mode.upper()}",
        color=BLUE,
    )

    # ── Load watchlist ───────────────────────────────────────────
    watchlist_rows = list_all()
    watchlist_set  = {r["ticker"] for r in watchlist_rows if r["active"]}
    all_tickers    = sorted(watchlist_set)

    _section(
        f"① INTRADAY SIGNAL SCAN  ({len(all_tickers)} tickers  ·  "
        f"10-min SMA8/SMA20  ·  {mode_label})",
        CYAN,
    )
    print(f"  {DIM}Fetching 10-min candles …{RESET}\n")

    signal_rows   = []
    price_map     = {}
    fetch_results = []
    candle_label  = ""

    for ticker in all_tickers:
        print(f"  {DIM}  {ticker} …{RESET}", end="\r")
        try:
            df = fetch_intraday(ticker)

            if df is None or df.empty:
                signal_rows.append({"ticker": ticker, "fetch_ok": False})
                fetch_results.append({"ticker": ticker, "ok": False, "error": "No data"})
                continue

            # Capture candle label from first successful fetch
            if not candle_label:
                candle_label = _current_candle_label(df)

            # ── Choose price based on report mode ────────────────
            if report_mode == "crossover":
                # Use live tick price — fires the moment cross is detected
                live = _get_live_price(ticker)
                if live is None:
                    live = _candle_close_price(df)   # fallback
                price_used = live
                # Inject live price as the last row's close for SMA calc
                df_eval = df.copy()
                df_eval.loc[df_eval.index[-1], "close"] = live
            else:
                # candle_close: use the confirmed completed bar
                price_used = _candle_close_price(df)
                df_eval = df

            # ── Calculate SMAs and get signal ────────────────────
            df_sig  = calculate_smas(df_eval)
            summary = signal_summary(ticker, df_sig)
            summary["fetch_ok"] = True
            summary["close"]    = round(price_used, 4)   # override with mode price

            signal_rows.append(summary)
            price_map[ticker] = price_used
            fetch_results.append({"ticker": ticker, "ok": True, "error": ""})

        except Exception as exc:
            signal_rows.append({"ticker": ticker, "fetch_ok": False})
            fetch_results.append({"ticker": ticker, "ok": False, "error": str(exc)})

    print(" " * 50, end="\r")
    _table_signals(signal_rows, candle_label=candle_label)

    # ── Paper trade BUY signals ──────────────────────────────────
    _section(
        f"② PAPER TRADES  ({report_mode.upper()}  ·  "
        f"SMA8 crosses above SMA20)",
        MAGENTA,
    )

    buy_signals = [
        r for r in signal_rows
        if r.get("signal") == "BUY" and r.get("fetch_ok")
    ]

    if buy_signals:
        print(f"  {GREEN}{len(buy_signals)} BUY signal(s) detected …{RESET}\n")
        for r in buy_signals:
            result = trader.handle_signal(
                ticker        = r["ticker"],
                signal        = "BUY",
                current_price = r["close"],
                as_of         = now_pt.date(),
            )
            # Stamp the buy log entry with an intraday timestamp
            if result == "BOUGHT" and trader._buy_log:
                trader._buy_log[-1]["timestamp"] = now_pt.strftime("%H:%M PT")
    else:
        print(f"  {DIM}No BUY signals this candle.{RESET}")

    # ── Bought this candle ───────────────────────────────────────
    _section("③ BOUGHT THIS CANDLE", GREEN)
    candle_buys = [
        e for e in trader._buy_log
        if e.get("timestamp", "").endswith("PT")
        and e.get("date") == now_pt.date()
    ]
    _table_bought(candle_buys)

    # ── Portfolio snapshot ───────────────────────────────────────
    _section("④ OPEN PORTFOLIO", CYAN)
    _table_portfolio(trader.open_positions(), price_map, as_of=now_pt.date())

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
        f"{BOLD}P&L {pnl_col}{total_pnl:>+9.2f} ({pnl_pct:>+.1f}%){RESET}"
    )
    print(f"  {_hr()}")

    # ── Fetch summary ────────────────────────────────────────────
    ok  = sum(1 for r in fetch_results if r["ok"])
    err = len(fetch_results) - ok
    print(f"\n  {GREEN}✓ {ok} fetched{RESET}  {RED}✗ {err} failed{RESET}  "
          f"{DIM}{len(fetch_results)} total{RESET}")
    for r in fetch_results:
        if not r["ok"]:
            print(f"    {RED}✗ {r['ticker']}: {r['error']}{RESET}")

    print(f"\n  {BOLD}{DIM}Candle scan complete — {now_pt.strftime('%H:%M:%S PT')}{RESET}")
    print(f"  {_hr('═')}\n")


# ─────────────────────────────────────────────────────────────────
# END-OF-DAY AGENT  (called once at 1:01 PM PT)
# ─────────────────────────────────────────────────────────────────

def run_agent_eod() -> None:
    """
    Full daily pipeline:
      1. Load favorites
      2. Run Finviz screener → promote new picks
      3. Fetch 60-day daily bars for all tickers
      4. Calculate EOD SMA8/SMA20 signals
      5. Paper-trade BUY signals at day's close price
      6. Display portfolio + fetch summary
    """
    today = date.today()
    _banner(f"EOD AGENT  ·  {today.isoformat()}  ·  1:01 PM PT SUMMARY")

    # ── 1. Favorites ─────────────────────────────────────────────
    _section("① EXISTING FAVORITES", YELLOW)
    watchlist_rows = list_all()
    watchlist      = [r["ticker"] for r in watchlist_rows if r["active"]]
    watchlist_set  = set(watchlist)
    _table_watchlist(watchlist, "Favorite  ★", YELLOW)

    # ── 2. Screener ──────────────────────────────────────────────
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
        print(f"  {DIM}No new picks today.{RESET}")

    promoted = []
    for ticker in new_picks:
        try:
            add_ticker(ticker, notes="finviz screener")
            promoted.append(ticker)
            watchlist_set.add(ticker)
        except Exception as exc:
            print(f"  {RED}✗ Could not promote {ticker}: {exc}{RESET}")

    if promoted:
        print(f"\n  {GREEN}✓ Promoted {len(promoted)}: {', '.join(promoted)}{RESET}")

    # ── 3 + 4. Fetch daily bars + signals ────────────────────────
    all_tickers   = sorted(watchlist_set)
    signal_rows   = []
    price_map     = {}
    fetch_results = []

    _section(
        f"③ EOD SIGNAL SCAN  ({len(all_tickers)} tickers  ·  "
        f"daily SMA8/SMA20  ·  CANDLE CLOSE = day close)",
        CYAN,
    )
    print(f"  {DIM}Fetching 60-day daily bars …{RESET}\n")

    for ticker in all_tickers:
        print(f"  {DIM}  {ticker} …{RESET}", end="\r")
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

    print(" " * 50, end="\r")
    _table_signals(signal_rows, candle_label="DAY CLOSE")

    # ── 5. Paper trades ──────────────────────────────────────────
    _section("④ EOD PAPER TRADES  (SMA8 crosses above SMA20 at day close)", MAGENTA)
    buy_signals = [r for r in signal_rows if r.get("signal") == "BUY" and r.get("fetch_ok")]

    if buy_signals:
        print(f"  {GREEN}{len(buy_signals)} BUY signal(s) at day close …{RESET}\n")
        for r in buy_signals:
            trader.handle_signal(
                ticker        = r["ticker"],
                signal        = "BUY",
                current_price = r["close"],
                as_of         = today,
            )
    else:
        print(f"  {DIM}No EOD BUY signals.{RESET}")

    # ── 6. Bought today ──────────────────────────────────────────
    _section("⑤ BOUGHT TODAY", GREEN)
    todays_buys = [e for e in trader._buy_log if e.get("date") == today]
    _table_bought(todays_buys)

    # ── Portfolio ────────────────────────────────────────────────
    _section("⑥ OPEN PORTFOLIO", CYAN)
    _table_portfolio(trader.open_positions(), price_map, as_of=today)

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
        f"{BOLD}P&L {pnl_col}{total_pnl:>+9.2f} ({pnl_pct:>+.1f}%){RESET}"
    )
    print(f"  {_hr()}")

    # ── Fetch summary ────────────────────────────────────────────
    _section("⑦ FETCH SUMMARY", WHITE)
    ok  = sum(1 for r in fetch_results if r["ok"])
    err = len(fetch_results) - ok
    print(f"  {GREEN}✓ {ok} fetched{RESET}  {RED}✗ {err} failed{RESET}  "
          f"{DIM}{len(fetch_results)} total{RESET}")
    for r in fetch_results:
        if not r["ok"]:
            print(f"    {RED}✗ {r['ticker']}: {r['error']}{RESET}")

    print(f"\n  {BOLD}{DIM}EOD run complete — {today.isoformat()}{RESET}")
    print(f"  {_hr('═')}\n")


# ── Alias for manual / test runs ─────────────────────────────────
def run_agent() -> None:
    """Manual alias — runs the full EOD pipeline immediately."""
    run_agent_eod()


if __name__ == "__main__":
    run_agent_eod()