from sqlalchemy import create_engine, text
import pandas as pd

engine = create_engine("sqlite:///stocks.db")

def save_data(df: pd.DataFrame):

    expected_cols = ["date", "open", "high", "low", "close", "volume", "ticker", "fetched_at"]
    df = df[expected_cols]

    ticker = df["ticker"].iloc[0]

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM prices WHERE ticker = :ticker"),
            {"ticker": ticker}
        )

    df.to_sql("prices", engine, if_exists="append", index=False)

    print(f"Saved {len(df)} rows for {ticker}")