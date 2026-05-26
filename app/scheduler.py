# scheduler.py
# ─────────────────────────────────────────────────────────────────
# Drives the stock agent on 10-minute candles during market hours.
#
# Market window (US Pacific):
#   Open  →  6:30 AM PT  (NYSE open)
#   Close →  1:00 PM PT  (NYSE close = 4:00 PM ET)
#
# Candle grid:  :00, :10, :20, :30, :40, :50  every hour
#   • run_agent_intraday() fires 30 s BEFORE each candle close
#     so yfinance has time to return the completed bar.
#   • end-of-day summary fires once at 1:01 PM PT.
#
# Two report modes (set REPORT_MODE in this file or via env var):
#   "crossover"  — report fires the moment SMA8 crosses SMA20
#                  (mid-candle, uses the latest tick price)
#   "candle_close" — report fires only at the confirmed candle close
#                    (default; more reliable, fewer false signals)
#
# Run:
#   python scheduler.py
# ─────────────────────────────────────────────────────────────────

import os
import logging
from datetime import datetime, time as dtime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron       import CronTrigger

from app.agent import run_agent_intraday, run_agent_eod

# ── Configuration ────────────────────────────────────────────────

# "crossover"    → alert fires the instant the cross is detected
#                  (uses live mid-candle price, may get noisy wicks)
# "candle_close" → alert fires only after the 10-min bar is confirmed
#                  (default; cleaner signal, 10-min lag at worst)
REPORT_MODE: str = os.getenv("REPORT_MODE", "candle_close")

PT = pytz.timezone("America/Los_Angeles")

# Market session: 6:30 AM – 1:00 PM Pacific
MARKET_OPEN  = dtime(6, 30)
MARKET_CLOSE = dtime(13, 0)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Guard: skip if outside market hours ──────────────────────────

def _is_market_open() -> bool:
    """Return True only during the configured PT market window on weekdays."""
    now_pt = datetime.now(PT)
    if now_pt.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now_pt.time().replace(second=0, microsecond=0)
    return MARKET_OPEN <= t < MARKET_CLOSE


# ── Scheduled callbacks ───────────────────────────────────────────

def candle_job() -> None:
    """
    Fires 30 s before each 10-minute candle close (see cron below).
    Passes the report mode so agent knows whether to alert immediately
    on crossover or wait for the bar to fully close.
    """
    if not _is_market_open():
        log.info("Outside market hours — candle job skipped.")
        return

    now_pt = datetime.now(PT)
    log.info(f"▶ Candle job  mode={REPORT_MODE}  {now_pt.strftime('%H:%M:%S PT')}")

    try:
        run_agent_intraday(report_mode=REPORT_MODE)
    except Exception as exc:
        log.error(f"candle_job error: {exc}", exc_info=True)


def eod_job() -> None:
    """End-of-day summary — fires once at 1:01 PM PT."""
    now_pt = datetime.now(PT)
    if now_pt.weekday() >= 5:
        log.info("Weekend — EOD job skipped.")
        return

    log.info(f"▶ EOD summary  {now_pt.strftime('%H:%M:%S PT')}")
    try:
        run_agent_eod()
    except Exception as exc:
        log.error(f"eod_job error: {exc}", exc_info=True)


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    scheduler = BlockingScheduler(timezone=PT)

    # ── Candle-close jobs ─────────────────────────────────────────
    # Fire at :30 s past XX:09, XX:19, XX:29, XX:39, XX:49, XX:59
    # That is 30 seconds BEFORE the 10-min bar closes, giving yfinance
    # time to return the completed OHLCV row.
    #
    # Candle boundaries (PT):  :00, :10, :20, :30, :40, :50
    # We want to run at:       :09:30, :19:30, :29:30, :39:30, :49:30, :59:30
    #
    # APScheduler cron: minute="9,19,29,39,49,59", second="30"
    scheduler.add_job(
        candle_job,
        CronTrigger(
            day_of_week = "mon-fri",
            hour        = "6-12",          # 6 AM–12 PM PT covers full session
            minute      = "9,19,29,39,49,59",
            second      = "30",
            timezone    = PT,
        ),
        id            = "candle_job",
        name          = "10-min candle scan",
        max_instances = 1,                 # never overlap
        coalesce      = True,              # skip missed fires if behind
    )

    # ── End-of-day summary ────────────────────────────────────────
    scheduler.add_job(
        eod_job,
        CronTrigger(
            day_of_week = "mon-fri",
            hour        = 13,
            minute      = 1,
            second      = 0,
            timezone    = PT,
        ),
        id   = "eod_job",
        name = "End-of-day summary",
    )

    # ── Start ─────────────────────────────────────────────────────
    log.info("═" * 60)
    log.info(f"  Stock Agent Scheduler  —  REPORT_MODE = {REPORT_MODE}")
    log.info(f"  Market window : {MARKET_OPEN.strftime('%H:%M')} – "
             f"{MARKET_CLOSE.strftime('%H:%M')} PT  (Mon–Fri)")
    log.info(f"  Candle size   : 10 minutes")
    log.info(f"  Candle jobs   : :09:30, :19:30, :29:30, :39:30, :49:30, :59:30")
    log.info(f"  EOD summary   : 13:01 PT")
    log.info("═" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()