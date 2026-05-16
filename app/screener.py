"""
app/screener.py
---------------
Queries Finviz for new stock candidates using the configured technical filters.

Active filters (matching your Finviz preset):
  - RSI (14):               Not Overbought (<60)
  - 50-Day SMA:             Price above SMA50
  - 200-Day SMA:            Price above SMA200
  - Beta:                   Over 1
  - Average True Range:     Over 1

Usage:
    from app.screener import run_screener, get_screener_tickers

    results = run_screener()         # list of dicts with full stock info
    tickers = get_screener_tickers() # just the ticker symbols
"""

from finvizfinance.screener.technical import Technical
import pandas as pd

# ------------------------------------------------------------------
# Filter map — keys are Finviz internal filter names,
# values are the option strings Finviz accepts.
# Adjust any value here to change screening criteria.
# ------------------------------------------------------------------
FILTERS = {
    "RSI (14)":                          "Not Overbought (<60)",
    "50-Day Simple Moving Average":      "Price above SMA50",
    "200-Day Simple Moving Average":     "Price above SMA200",
    "Beta":                              "Over 1",
    "Average True Range":                "Over 1",
}

# Post-filter: drop stocks whose Avg Volume < this number.
# Finviz free tier doesn't expose a precise volume filter, so we do it here.
# Set to 0 to disable.
MIN_AVG_VOLUME = 500_000


def run_screener(filters: dict = None, min_volume: int = MIN_AVG_VOLUME) -> list[dict]:
    """
    Run the Finviz screener with the configured filters.

    Args:
        filters:    Override the default FILTERS dict. Pass None to use defaults.
        min_volume: Post-filter: drop stocks with Avg Volume below this number.
                    Set to 0 to skip.

    Returns:
        List of dicts, one per stock. Keys include:
        Ticker, Company, Sector, Industry, Price, Change, Volume,
        Avg Volume, RSI (14), Beta, ATR, etc.
    """
    active_filters = filters if filters is not None else FILTERS

    screener = Technical()
    screener.set_filter(filters_dict=active_filters)

    try:
        df: pd.DataFrame = screener.screener_view()
    except Exception as e:
        print(f"✗ Screener fetch failed: {e}")
        return []

    if df is None or df.empty:
        print("  Screener returned no results with current filters.")
        return []

    # Normalize column names (Finviz occasionally changes capitalization)
    df.columns = [c.strip() for c in df.columns]

    # Post-filter by average volume if the column exists
    if min_volume > 0 and "Avg Volume" in df.columns:
        vol_series = (
            df["Avg Volume"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("M", "e6", regex=False)
            .str.replace("K", "e3", regex=False)
        )
        df["Avg Volume"] = pd.to_numeric(vol_series, errors="coerce").fillna(0)
        before = len(df)
        df = df[df["Avg Volume"] >= min_volume]
        dropped = before - len(df)
        if dropped:
            print(f"  Volume filter removed {dropped} low-liquidity stock(s)")

    results = df.to_dict(orient="records")
    print(f"✓ Screener found {len(results)} candidates")
    return results


def get_screener_tickers(filters: dict = None, min_volume: int = MIN_AVG_VOLUME) -> list[str]:
    """
    Convenience wrapper — returns only the ticker symbols.

    Returns:
        e.g. ["AAPL", "NVDA", "META"]
    """
    results = run_screener(filters=filters, min_volume=min_volume)
    return [row["Ticker"] for row in results if "Ticker" in row]


def describe_filters(filters: dict = None) -> None:
    """Print a human-readable summary of the active filters."""
    active = filters if filters is not None else FILTERS
    print("\nActive screener filters:")
    for k, v in active.items():
        print(f"  {k:<45} {v}")
    print(f"  {'Min Avg Volume (post-filter)':<45} {MIN_AVG_VOLUME:,}\n")


# ------------------------------------------------------------------
# Self-test
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Screener self-test ===")
    describe_filters()

    tickers = get_screener_tickers()
    print(f"Tickers returned: {tickers[:20]}")  # first 20

    results = run_screener()
    if results:
        print("\nFirst 5 results (selected fields):")
        for row in results[:5]:
            print(
                f"  {row.get('Ticker','?'):<8}"
                f"  price={row.get('Price','?'):<8}"
                f"  RSI={row.get('RSI (14)','?'):<6}"
                f"  change={row.get('Change','?')}"
            )