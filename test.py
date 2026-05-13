from app.agent import run_agent
from sqlalchemy import create_engine, text
import pandas as pd
from sqlalchemy import text

# -------------------------
# DB SETUP
# -------------------------
engine = create_engine("sqlite:///stocks.db")


# -------------------------
# RESET DATABASE (IMPORTANT FOR TESTING)
# -------------------------
def reset_db():
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS prices"))

        # recreate empty table with correct schema
        conn.execute(text("""
            CREATE TABLE prices (
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                ticker TEXT,
                fetched_at TEXT
            )
        """))

    print("✓ Database reset + recreated")


# -------------------------
# VERIFY DATABASE CONTENT
# -------------------------
def verify_data():
    df = pd.read_sql(
        """
        SELECT *
        FROM prices
        ORDER BY ticker ASC, date DESC
        """,
        engine
    )

    print("\n=== DATABASE CHECK ===\n")

    # optional: nicer display formatting
    pd.set_option("display.width", 120)
    pd.set_option("display.max_columns", None)

    print(df.reset_index(drop=True))
    
# -------------------------
# TEST RUN
# -------------------------
if __name__ == "__main__":
    print("Running agent test...\n")

    # OPTIONAL but recommended for clean testing
    reset_db()

    run_agent()

    verify_data()