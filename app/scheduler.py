from apscheduler.schedulers.blocking import BlockingScheduler
from app.agent import run_agent
from datetime import datetime, timedelta


def main():
    scheduler = BlockingScheduler()

    run_time = datetime.now() + timedelta(minutes=2)

    scheduler.add_job(run_agent, "date", run_date=run_time)

    print(f"Scheduled run_agent for {run_time}")
    scheduler.start()


if __name__ == "__main__":
    main()