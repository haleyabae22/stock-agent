from apscheduler.schedulers.blocking import BlockingScheduler
from app.agent import run_agent
from sqlalchemy import create_engine
import pandas as pd
import pytz

engine = create_engine("sqlite:///stocks.db")

pacific = pytz.timezone("America/Los_Angeles")

def verify_data():
    try:
        df = pd.read_sql(
            """
            SELECT *
            FROM prices
            ORDER BY ticker ASC, date DESC
            """,
            engine
        )

        print("\n=== DATABASE CHECK ===\n")
        print(df)

    except Exception as e:
        print("\n=== DATABASE CHECK FAILED ===")
        print(e)


def job():
    print("\nRunning scheduled agent...\n")
    run_agent()
    verify_data()


scheduler = BlockingScheduler(timezone=pacific)

# run immediately once
print("\nRunning agent immediately...\n")
job()

# schedule every day at 1:00 PM PT
scheduler.add_job(job, "cron", hour=13, minute=0)

print("\nScheduler started: running daily at 1:00 PM PT\n")
scheduler.start()