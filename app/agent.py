from app.fetcher import fetch_stock
from app.storage import save_data

TICKERS = ["AAPL", "TSLA", "MSFT", "NVDA"]

def run_agent():
    print("Agent running...")
    for ticker in TICKERS:
        try:
            df = fetch_stock(ticker)
            save_data(df)
            print(f"  ✓ {ticker} saved")
        except Exception as e:
            print(f"  ✗ {ticker} failed: {e}")