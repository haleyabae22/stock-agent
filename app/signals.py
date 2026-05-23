"""
app/signals.py
--------------
Calculates technical indicators and detects the 8/20-day SMA crossover
buy and sell signals.

Public API:
    calculate_smas(df)          → df with sma8, sma20 columns added
    detect_buy_signal(df)       → True if SMA8 just crossed above SMA20
    detect_sell_signal(df)      → True if SMA8 just crossed below SMA20
    get_signal(df)              → "BUY" | "SELL" | "HOLD"
    signal_summary(ticker, df)  → dict with full signal metadata

Run directly to self-test:
    python -m app.signals
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ─────────────────────────────────────────────
# Core indicator calculations
# ─────────────────────────────────────────────

def calculate_smas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add SMA8 and SMA20 columns to a price DataFrame.

    Args:
        df: DataFrame with at least a 'Close' column and a DatetimeIndex.
            Requires at least 20 rows for a valid SMA20.

    Returns:
        The same DataFrame with two new columns:
            sma8   — 8-period simple moving average of Close
            sma20  — 20-period simple moving average of Close
        Rows with insufficient history will have NaN in those columns.
    """
    df = df.copy()

    # ── Normalize columns to lowercase regardless of yfinance version ──
    # Handles: MultiIndex tuples, Title Case, UPPER, mixed whitespace
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in df.columns
        ]
    df.columns = [str(c).strip().lower() for c in df.columns]

    if "close" not in df.columns:
        raise ValueError(
            f"DataFrame must contain a 'close' column. Got: {df.columns.tolist()}"
        )

    df["sma8"]  = df["close"].rolling(window=8,  min_periods=8).mean()
    df["sma20"] = df["close"].rolling(window=20, min_periods=20).mean()
    return df


def detect_buy_signal(df: pd.DataFrame) -> bool:
    """
    Detect a golden-cross buy signal: SMA8 crosses ABOVE SMA20.

    Logic:
        - Yesterday: sma8 <= sma20  (SMA8 was at or below SMA20)
        - Today:     sma8 >  sma20  (SMA8 has moved above SMA20)

    Args:
        df: DataFrame already processed by calculate_smas().
            Must have at least 2 valid (non-NaN) sma8/sma20 rows.

    Returns:
        True if a crossover-up occurred on the most recent bar, else False.
    """
    valid = df.dropna(subset=["sma8", "sma20"])
    if len(valid) < 2:
        return False

    prev = valid.iloc[-2]
    curr = valid.iloc[-1]

    was_below_or_equal = prev["sma8"] <= prev["sma20"]
    now_above          = curr["sma8"]  > curr["sma20"]
    return bool(was_below_or_equal and now_above)


def detect_sell_signal(df: pd.DataFrame) -> bool:
    """
    Detect a death-cross sell signal: SMA8 crosses BELOW SMA20.

    Logic:
        - Yesterday: sma8 >= sma20  (SMA8 was at or above SMA20)
        - Today:     sma8 <  sma20  (SMA8 has moved below SMA20)

    Args:
        df: DataFrame already processed by calculate_smas().

    Returns:
        True if a crossover-down occurred on the most recent bar, else False.
    """
    valid = df.dropna(subset=["sma8", "sma20"])
    if len(valid) < 2:
        return False

    prev = valid.iloc[-2]
    curr = valid.iloc[-1]

    was_above_or_equal = prev["sma8"] >= prev["sma20"]
    now_below          = curr["sma8"]  < curr["sma20"]
    return bool(was_above_or_equal and now_below)


def get_signal(df: pd.DataFrame) -> str:
    """
    Return the current signal as a string.

    Returns:
        "BUY"  — SMA8 just crossed above SMA20
        "SELL" — SMA8 just crossed below SMA20
        "HOLD" — no crossover on the most recent bar
    """
    if detect_buy_signal(df):
        return "BUY"
    if detect_sell_signal(df):
        return "SELL"
    return "HOLD"


def signal_summary(ticker: str, df: pd.DataFrame) -> dict:
    """
    Build a full signal metadata dict for logging and storage.

    Args:
        ticker: Stock symbol e.g. "AAPL"
        df:     DataFrame already processed by calculate_smas().

    Returns:
        Dict with keys:
            ticker, date, close, sma8, sma20, gap_pct, signal
    """
    if df.empty:
        return {"ticker": ticker, "signal": "NO_DATA"}

    valid = df.dropna(subset=["sma8", "sma20"])
    if valid.empty:
        return {"ticker": ticker, "signal": "INSUFFICIENT_DATA"}

    last = valid.iloc[-1]
    sma8  = round(float(last["sma8"]),  4)
    sma20 = round(float(last["sma20"]), 4)
    close = round(float(last["close"]), 4)
    gap_pct = round((sma8 - sma20) / sma20 * 100, 3)  # + means SMA8 above SMA20

    return {
        "ticker":   ticker,
        "date":     last.name.date().isoformat() if hasattr(last.name, "date") else str(last.name),
        "close":    close,
        "sma8":     sma8,
        "sma20":    sma20,
        "gap_pct":  gap_pct,   # positive = SMA8 above SMA20
        "signal":   get_signal(df),
    }


# ─────────────────────────────────────────────
# Helpers for generating synthetic test data
# ─────────────────────────────────────────────

def _make_price_series(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of closing prices (lowercase cols, matches fetcher.py)."""
    dates = pd.date_range(start=start, periods=len(prices), freq="B")
    df = pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.01 for p in prices],
        "low":    [p * 0.99 for p in prices],
        "close":  prices,
        "volume": [1_000_000] * len(prices),
    }, index=dates)
    return df


# ─────────────────────────────────────────────
# Self-tests
# ─────────────────────────────────────────────

def _test_sma_values():
    """SMAs should equal hand-calculated rolling means."""
    print("  test_sma_values ... ", end="")
    prices = list(range(1, 26))           # 1,2,3,...,25
    df = calculate_smas(_make_price_series(prices))

    # SMA8 on bar 8 (index 7) should be mean(1..8) = 4.5
    expected_sma8_day8 = sum(range(1, 9)) / 8
    actual = df["sma8"].dropna().iloc[0]
    assert abs(actual - expected_sma8_day8) < 0.001, f"SMA8 mismatch: {actual}"

    # SMA20 on bar 20 (index 19) should be mean(1..20) = 10.5
    expected_sma20_day20 = sum(range(1, 21)) / 20
    actual20 = df["sma20"].dropna().iloc[0]
    assert abs(actual20 - expected_sma20_day20) < 0.001, f"SMA20 mismatch: {actual20}"

    # First 7 rows should have NaN sma8
    assert df["sma8"].iloc[:7].isna().all(), "First 7 sma8 should be NaN"

    print("PASS")


def _test_buy_signal_detected():
    """A clean golden cross should return BUY."""
    print("  test_buy_signal_detected ... ", end="")
    # 25 days declining (SMA8 < SMA20), then sharp rise on last 2 days
    prices = [100 - i * 0.5 for i in range(23)] + [105, 112]
    df = calculate_smas(_make_price_series(prices))
    assert detect_buy_signal(df), "Expected BUY signal on golden cross"
    assert get_signal(df) == "BUY"
    print("PASS")


def _test_sell_signal_detected():
    """A clean death cross should return SELL."""
    print("  test_sell_signal_detected ... ", end="")
    # 25 days rising (SMA8 > SMA20), then sharp drop on last 2 days
    prices = [100 + i * 0.5 for i in range(23)] + [95, 88]
    df = calculate_smas(_make_price_series(prices))
    assert detect_sell_signal(df), "Expected SELL signal on death cross"
    assert get_signal(df) == "SELL"
    print("PASS")


def _test_hold_when_no_crossover():
    """A flat trend with no crossover should return HOLD."""
    print("  test_hold_when_no_crossover ... ", end="")
    prices = [100.0] * 25          # perfectly flat — SMA8 == SMA20 always
    df = calculate_smas(_make_price_series(prices))
    # No crossover (equal is not a cross)
    assert not detect_buy_signal(df)
    assert not detect_sell_signal(df)
    assert get_signal(df) == "HOLD"
    print("PASS")


def _test_insufficient_data():
    """Fewer than 20 bars should not trigger a signal."""
    print("  test_insufficient_data ... ", end="")
    prices = [100.0] * 15          # only 15 bars — not enough for SMA20
    df = calculate_smas(_make_price_series(prices))
    assert not detect_buy_signal(df)
    assert not detect_sell_signal(df)
    assert get_signal(df) == "HOLD"
    print("PASS")


def _test_signal_summary_fields():
    """signal_summary() should return all required keys with correct types."""
    print("  test_signal_summary_fields ... ", end="")
    prices = [100 - i * 0.5 for i in range(23)] + [105, 112]
    df = calculate_smas(_make_price_series(prices))
    summary = signal_summary("TEST", df)

    required = {"ticker", "date", "close", "sma8", "sma20", "gap_pct", "signal"}
    assert required == set(summary.keys()), f"Missing keys: {required - set(summary.keys())}"
    assert summary["ticker"] == "TEST"
    assert summary["signal"] == "BUY"
    assert isinstance(summary["gap_pct"], float)
    print("PASS")


def _test_no_double_signal():
    """Once a crossover has fired, the next bar should be HOLD (not a repeat BUY)."""
    print("  test_no_double_signal ... ", end="")
    # Cross happens at bar -2. Add one more bar that stays above — should be HOLD.
    prices = [100 - i * 0.5 for i in range(23)] + [105, 112, 113]
    df = calculate_smas(_make_price_series(prices))
    # SMA8 is now well above SMA20 — no new crossover
    assert not detect_buy_signal(df), "Should be HOLD after crossover already fired"
    assert get_signal(df) == "HOLD"
    print("PASS")


def run_tests():
    print("\n=== signals.py tests ===\n")
    _test_sma_values()
    _test_buy_signal_detected()
    _test_sell_signal_detected()
    _test_hold_when_no_crossover()
    _test_insufficient_data()
    _test_signal_summary_fields()
    _test_no_double_signal()
    print("\n✓ All signals tests passed\n")


if __name__ == "__main__":
    run_tests()

    # Demo: show what a summary dict looks like
    print("=== Example signal_summary output ===\n")
    demo_prices = [100 - i * 0.5 for i in range(23)] + [105, 112]
    demo_df     = calculate_smas(_make_price_series(demo_prices))
    summary     = signal_summary("DEMO", demo_df)
    for k, v in summary.items():
        print(f"  {k:<10} {v}")