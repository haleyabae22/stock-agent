import yfinance as yf
import pandas as pd
from datetime import datetime

def fetch_stock(ticker: str):
    df = yf.download(ticker, period="5d", auto_adjust=False)

    # 1. RESET INDEX FIRST
    df = df.reset_index()

    # 2. HANDLE MULTIINDEX SAFELY
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    # 3. FORCE CLEAN STANDARDIZATION
    df.columns = [str(c).strip().lower() for c in df.columns]

    # 4. DEBUG (VERY IMPORTANT for now)
    print("DEBUG COLUMNS:", df.columns.tolist())

    # 5. SAFE COLUMN SELECTION (no assumptions)
    required = ["date", "open", "high", "low", "close", "volume"]

    # yfinance sometimes uses "Date" instead of "date"
    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns after cleaning: {missing}")

    df = df[required]

    df["ticker"] = ticker
    df["fetched_at"] = datetime.utcnow()

    return df