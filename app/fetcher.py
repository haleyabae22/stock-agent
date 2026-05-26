# app/fetcher.py
# ─────────────────────────────────────────────────────────────────
# Two fetch modes:
#
#   fetch_stock(ticker)          → daily OHLCV (60-day history)
#                                  used by screener + EOD agent
#
#   fetch_intraday(ticker)       → 10-minute OHLCV candles
#                                  last 5 trading days of intraday data
#                                  used by the intraday candle agent
#
# Both return DataFrames with lowercase columns:
#   date | open | high | low | close | volume | ticker | fetched_at
#
# The intraday frame also carries:
#   datetime  — full UTC timestamp of the bar open
#   candle    — candle open time as "HH:MM PT" string
# ─────────────────────────────────────────────────────────────────

import pytz
import yfinance as yf
import pandas as pd
from datetime import datetime

PT = pytz.timezone("America/Los_Angeles")


# ── Shared column normalizer ─────────────────────────────────────

def _normalize(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Flatten MultiIndex columns, lowercase everything, validate required
    columns are present, then add ticker + fetched_at metadata columns.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col
                      for col in df.columns]

    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ["open", "high", "low", "close", "volume"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns after normalize: {missing} "
                         f"(have: {list(df.columns)})")

    df["ticker"]     = ticker
    df["fetched_at"] = datetime.utcnow()
    return df


# ── Daily fetch (60-day history) ─────────────────────────────────

def fetch_stock(ticker: str, period: str = "60d") -> pd.DataFrame:
    """
    Fetch daily OHLCV history.

    Args:
        ticker: Stock symbol e.g. "AAPL"
        period: yfinance period string (default "60d" — gives ~42 trading
                days, enough for SMA8 + SMA20 with crossover detection)

    Returns:
        DataFrame with columns: date, open, high, low, close, volume,
        ticker, fetched_at.  Index is a RangeIndex (date is a column).

    Raises:
        RuntimeError wrapping the original exception on any failure.
    """
    try:
        raw = yf.download(
            ticker,
            period      = period,
            interval    = "1d",
            auto_adjust = False,
            progress    = False,
            threads     = False,
        )

        if raw is None or raw.empty:
            raise ValueError("No data returned from yfinance")

        df = raw.reset_index()
        df = _normalize(df, ticker)

        # Rename the date index column (yfinance calls it "Date" or "Datetime")
        for col in df.columns:
            if col in ("date", "datetime"):
                break
        else:
            df = df.rename(columns={df.columns[0]: "date"})

        if "date" not in df.columns and "datetime" in df.columns:
            df = df.rename(columns={"datetime": "date"})

        return df[["date", "open", "high", "low", "close", "volume",
                   "ticker", "fetched_at"]]

    except Exception as exc:
        raise RuntimeError(f"{ticker} daily fetch failed: {exc}") from exc


# ── Intraday fetch (10-minute candles) ───────────────────────────

def fetch_intraday(ticker: str, days: int = 5) -> pd.DataFrame:
    """
    Fetch 10-minute intraday OHLCV candles.

    yfinance supports "10m" interval for up to ~60 days of history.
    We default to the last 5 trading days so we have enough bars for
    intraday SMA8 (8 × 10 min = 80 min) and SMA20 (200 min = 3h20m).

    Args:
        ticker: Stock symbol e.g. "AAPL"
        days:   Number of recent trading days to fetch (default 5)

    Returns:
        DataFrame with columns:
            datetime  — bar open time (UTC, timezone-aware)
            candle    — bar open time as "HH:MM PT" string
            open, high, low, close, volume
            ticker, fetched_at

        Rows are filtered to 6:30 AM – 1:00 PM PT only.
        Index is a RangeIndex.

    Raises:
        RuntimeError on failure.
    """
    try:
        period = f"{days}d"
        raw = yf.download(
            ticker,
            period      = period,
            interval    = "10m",
            auto_adjust = False,
            progress    = False,
            threads     = False,
        )

        if raw is None or raw.empty:
            raise ValueError("No intraday data returned from yfinance")

        df = raw.reset_index()
        df = _normalize(df, ticker)

        # yfinance returns the bar-open timestamp as "Datetime" for intraday
        dt_col = None
        for c in df.columns:
            if c in ("datetime", "date"):
                dt_col = c
                break
        if dt_col is None:
            dt_col = df.columns[0]

        df = df.rename(columns={dt_col: "datetime"})

        # Ensure timezone-aware UTC
        if df["datetime"].dt.tz is None:
            df["datetime"] = df["datetime"].dt.tz_localize("UTC")
        else:
            df["datetime"] = df["datetime"].dt.tz_convert("UTC")

        # Add Pacific-time candle label
        df["candle"] = (
            df["datetime"]
            .dt.tz_convert(PT)
            .dt.strftime("%H:%M PT")
        )

        # Filter to market hours 6:30 AM – 1:00 PM PT
        pt_times = df["datetime"].dt.tz_convert(PT)
        market_open  = pd.Timestamp("today", tz=PT).replace(
            hour=6, minute=30, second=0, microsecond=0)
        market_close = pd.Timestamp("today", tz=PT).replace(
            hour=13, minute=0, second=0, microsecond=0)

        # Time-of-day filter (works across multiple days)
        pt_time_of_day = pt_times.dt.time
        open_t  = market_open.time()
        close_t = market_close.time()
        mask = (pt_time_of_day >= open_t) & (pt_time_of_day < close_t)
        df = df[mask].reset_index(drop=True)

        if df.empty:
            raise ValueError("No bars within market hours after filtering")

        cols = ["datetime", "candle", "open", "high", "low", "close",
                "volume", "ticker", "fetched_at"]
        return df[[c for c in cols if c in df.columns]]

    except Exception as exc:
        raise RuntimeError(f"{ticker} intraday fetch failed: {exc}") from exc