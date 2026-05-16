import yfinance as yf
import pandas as pd
from datetime import datetime

def fetch_stock(ticker: str):
    try:
        df = yf.download(
            ticker,
            period="5d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df is None or df.empty:
            raise ValueError("No data returned from yfinance")

        df = df.reset_index()

        # flatten multiindex if it appears
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]

        df.columns = [str(c).strip().lower() for c in df.columns]

        # normalize date column
        if "date" not in df.columns:
            df = df.rename(columns={df.columns[0]: "date"})

        required = ["date", "open", "high", "low", "close", "volume"]

        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        df = df[required]
        df["ticker"] = ticker
        df["fetched_at"] = datetime.utcnow()

        return df

    except Exception as e:
        raise RuntimeError(f"{ticker} fetch failed: {str(e)}")