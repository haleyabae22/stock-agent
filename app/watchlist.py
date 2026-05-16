"""
app/watchlist.py
"""

import os
from datetime import datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
engine = create_engine(os.getenv("DB_URL", "sqlite:///stocks.db"))

DEFAULT_TICKERS = ["AAPL", "TSLA", "MSFT", "NVDA"]


def init_watchlist(seed_defaults: bool = True) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1,
                added TEXT NOT NULL DEFAULT (date('now')),
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'manual'
            )
        """))

    if seed_defaults:
        for ticker in DEFAULT_TICKERS:
            _insert_if_missing(ticker, source="seed")

    print(f"✓ Watchlist ready ({len(get_tickers())} active tickers)")


def get_tickers() -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ticker FROM watchlist WHERE active = 1 ORDER BY ticker ASC")
        ).fetchall()
    return [r[0] for r in rows]


def list_all() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ticker, active, added, notes, source
                FROM watchlist
                ORDER BY added DESC
            """)
        ).fetchall()

    return [
        {
            "ticker": r[0],
            "active": bool(r[1]),
            "added": r[2],
            "notes": r[3],
            "source": r[4]
        }
        for r in rows
    ]


# ✅ UPDATED: add_ticker now supports source
def add_ticker(ticker: str, source: str = "manual", notes: str = None) -> bool:
    ticker = ticker.upper().strip()

    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT active FROM watchlist WHERE ticker = :t"),
            {"t": ticker}
        ).fetchone()

    if existing is None:
        _insert_if_missing(ticker, source=source, notes=notes)
        print(f"✓ Added '{ticker}' to watchlist (source={source})")
        return True

    elif existing[0] == 0:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE watchlist
                    SET active = 1, source = :s, notes = :n
                    WHERE ticker = :t
                """),
                {"s": source, "n": notes, "t": ticker}
            )
        print(f"✓ Re-activated '{ticker}' (source={source})")
        return True

    else:
        print(f"  '{ticker}' is already active — no change made")
        return False


def disable_ticker(ticker: str) -> bool:
    ticker = ticker.upper().strip()

    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE watchlist SET active = 0 WHERE ticker = :t"),
            {"t": ticker}
        )

    if result.rowcount == 0:
        print(f"  '{ticker}' not found in watchlist")
        return False

    print(f"✓ Disabled '{ticker}'")
    return True


def remove_ticker(ticker: str) -> bool:
    ticker = ticker.upper().strip()

    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM watchlist WHERE ticker = :t"),
            {"t": ticker}
        )

    if result.rowcount == 0:
        print(f"  '{ticker}' not found in watchlist")
        return False

    print(f"✓ Permanently removed '{ticker}'")
    return True


# ✅ UPDATED helper
def _insert_if_missing(ticker: str, source: str = "manual", notes: str = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT OR IGNORE INTO watchlist (ticker, active, added, notes, source)
                VALUES (:t, 1, :d, :n, :s)
            """),
            {
                "t": ticker.upper(),
                "d": datetime.utcnow().date().isoformat(),
                "n": notes,
                "s": source
            }
        )