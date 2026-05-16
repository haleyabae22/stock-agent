# reset_db.py
# -------------------------------------------------
# Deletes and recreates stocks.db from scratch
# -------------------------------------------------

import os
from sqlalchemy import create_engine, text

DB_FILE = "stocks.db"

# 1. Delete old DB
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
    print("✓ Old stocks.db deleted")
else:
    print("• No existing database found")

# 2. Recreate DB
engine = create_engine(f"sqlite:///{DB_FILE}")

with engine.begin() as conn:
    # Prices table
    conn.execute(text("""
        CREATE TABLE prices (
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            ticker TEXT,
            fetched_at TEXT
        )
    """))

    # Watchlist table (UPDATED)
    conn.execute(text("""
        CREATE TABLE watchlist (
            ticker TEXT PRIMARY KEY,
            active INTEGER NOT NULL DEFAULT 1,
            added TEXT,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'manual'
        )
    """))

    # Seed starter stocks
    starter = ["AAPL", "TSLA", "MSFT", "NVDA"]

    for ticker in starter:
        conn.execute(
            text("""
                INSERT INTO watchlist (ticker, active, added, source)
                VALUES (:ticker, 1, date('now'), 'seed')
            """),
            {"ticker": ticker}
        )

print("✓ Fresh database created")
print("✓ Starter watchlist added")