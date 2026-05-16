# app/agent.py
# ---------------------------------------------------------------
# Agentic runner with rich terminal output.
#
# Flow:
#   1. Load favorites from watchlist DB  → display table (sorted A-Z)
#   2. Run Finviz screener               → only stocks matching criteria
#   3. New picks (not already favorites) → display table (sorted A-Z)
#   4. Promote new picks to DB
#   5. Fetch price data for all tickers
#   6. Print results summary
# ---------------------------------------------------------------

from app.fetcher   import fetch_stock
from app.storage   import save_data
from app.watchlist import get_tickers, list_all, add_ticker
from app.screener  import get_screener_tickers   # criteria filter is inside here

# ── ANSI colour helpers ──────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
WHITE  = "\033[97m"

WIDTH  = 66


def _hr(char: str = "─") -> str:
    return char * WIDTH


def _section_header(title: str, color: str) -> None:
    """Print a bold colored section heading with a rule below it."""
    print()
    print(f"  {BOLD}{color}{title}{RESET}")
    print(f"  {_hr()}")


def _table(tickers: list, color: str, label_col: str) -> None:
    """
    Print an alphabetically sorted ticker table.
    tickers   – list of ticker strings (already filtered/deduped)
    color     – ANSI color for the label column
    label_col – text shown in the Status column
    """
    sorted_tickers = sorted(tickers)   # ← A-Z sort

    print(f"  {BOLD}{'#':<5}{'Ticker':<14}{'Status'}{RESET}")
    print(f"  {_hr()}")

    for i, ticker in enumerate(sorted_tickers, 1):
        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{ticker:<14}{RESET}"
            f"{color}{label_col}{RESET}"
        )

    print(f"  {_hr()}")
    print(f"  {DIM}{len(sorted_tickers)} ticker(s){RESET}")


def _results_table(results: list) -> None:
    """Final summary table, sorted A-Z by ticker."""
    if not results:
        return

    sorted_results = sorted(results, key=lambda r: r["ticker"])

    print()
    print(f"  {BOLD}{CYAN}{'RESULTS SUMMARY':^{WIDTH}}{RESET}")
    print(f"  {_hr('═')}")
    print(f"  {BOLD}{'#':<5}{'Ticker':<14}{'Source':<22}{'Result'}{RESET}")
    print(f"  {_hr()}")

    for i, row in enumerate(sorted_results, 1):
        result_str = (
            f"{GREEN}✓  Saved{RESET}"
            if row["ok"]
            else f"{RED}✗  {row['error'][:28]}{RESET}"
        )
        print(
            f"  {i:<5}"
            f"{BOLD}{WHITE}{row['ticker']:<14}{RESET}"
            f"{DIM}{row['source']:<22}{RESET}"
            f"{result_str}"
        )

    print(f"  {_hr('═')}")
    ok  = sum(1 for r in results if r["ok"])
    err = len(results) - ok
    print(
        f"  {GREEN}{ok} saved{RESET}  "
        f"{RED}{err} failed{RESET}  "
        f"{DIM}{len(results)} total{RESET}"
    )
    print()


# ── Main entry point ─────────────────────────────────────────────

def run_agent() -> None:
    print()
    print(f"  {BOLD}{CYAN}{_hr()}{RESET}")
    print(f"  {BOLD}{CYAN}  STOCK AGENT{RESET}")
    print(f"  {BOLD}{CYAN}{_hr()}{RESET}")

    # ── 1. Load existing favorites ───────────────────────────────
    print(f"\n  {DIM}Loading favorites from watchlist …{RESET}")
    watchlist_rows = list_all()                          # full rows with metadata
    watchlist      = [r["ticker"] for r in watchlist_rows]
    watchlist_set  = set(watchlist)

    source_map = {
        r["ticker"]: r.get("source", "manual")
        for r in watchlist_rows
    }

    # ── 2. Show FAVORITES table first ────────────────────────────
    _section_header(
        f"EXISTING FAVORITES  ({len(watchlist)} stock{'s' if len(watchlist) != 1 else ''})",
        YELLOW,
    )
    if watchlist:
        _table(watchlist, YELLOW, "Favorite ★")
    else:
        print(f"  {DIM}No favorites yet.{RESET}")

    # ── 3. Run screener — only stocks that pass all criteria ─────
    print(f"\n  {DIM}Running Finviz screener  "
          f"(RSI<60 · SMA50 · SMA200 · Beta>1 · ATR>1 · Vol>500K) …{RESET}")
    screener_picks = get_screener_tickers()              # criteria enforced inside

    # Keep only picks that are NOT already a favorite
    new_picks = sorted(
        [t for t in screener_picks if t not in watchlist_set]
    )  # A-Z

    # ── 4. Show NEW SCREENER PICKS table ─────────────────────────
    _section_header(
        f"NEW SCREENER PICKS  ({len(new_picks)} stock{'s' if len(new_picks) != 1 else ''} match criteria, not yet in favorites)",
        GREEN,
    )
    if new_picks:
        _table(new_picks, GREEN, "New Pick →  Promoting")
    else:
        print(f"  {DIM}No new picks today — every screener match is already a favorite.{RESET}")

    # ── 5. Promote new picks to the watchlist DB ─────────────────
    promoted = []
    for ticker in new_picks:
        try:
            add_ticker(ticker, source="screener", notes="finviz screener")
            promoted.append(ticker)
            watchlist_set.add(ticker)
            source_map[ticker] = "screener"
        except Exception as exc:
            print(f"  {RED}✗ Could not promote {ticker}: {exc}{RESET}")

    if promoted:
        print(
            f"\n  {GREEN}✓ Promoted {len(promoted)} ticker(s) to favorites: "
            f"{', '.join(sorted(promoted))}{RESET}"
        )

    # ── 6. Fetch price data for all tickers (favorites + promoted) ──
    all_tickers = sorted(watchlist_set)   # A-Z for consistent ordering

    print()
    print(f"  {BOLD}Fetching price data for {len(all_tickers)} ticker(s) …{RESET}")
    print(f"  {_hr()}")

    results = []

    for ticker in all_tickers:
        source = source_map.get(ticker, "screener")
        display_source = "Screener → Promoted" if ticker in promoted else "Favorite"
        try:
            print(f"  {DIM}Fetching {ticker} …{RESET}", end="\r")
            df = fetch_stock(ticker)

            if df is None or df.empty:
                results.append({"ticker": ticker, "source": display_source,
                                 "ok": False, "error": "No data returned"})
                continue

            save_data(df)
            results.append({"ticker": ticker, "source": display_source,
                             "ok": True, "error": ""})

        except Exception as exc:
            results.append({"ticker": ticker, "source": display_source,
                             "ok": False, "error": str(exc)})

    # ── 7. Results summary ───────────────────────────────────────
    _results_table(results)


if __name__ == "__main__":
    run_agent()