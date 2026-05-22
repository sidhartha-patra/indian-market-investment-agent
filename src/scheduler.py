"""Daily scheduler for ingestion + recommendations + alerts."""
from __future__ import annotations
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from src.ingestion.run_all import main as ingest
from src.agent.recommend import generate_recommendations
from src.notify.telegram_bot import send_message, format_recommendations

logger = logging.getLogger(__name__)


def daily_pipeline() -> None:
    logger.info("== Daily pipeline starting ==")
    ingest()
    recs = generate_recommendations()
    msg = format_recommendations(recs)
    send_message(msg)
    logger.info("== Daily pipeline complete ==")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sched = BlockingScheduler(timezone="Asia/Kolkata")
    # Pre-market 8:45 AM IST
    sched.add_job(daily_pipeline, CronTrigger(hour=8, minute=45, day_of_week="mon-fri"))
    # Post-market 4:00 PM IST
    sched.add_job(daily_pipeline, CronTrigger(hour=16, minute=0, day_of_week="mon-fri"))
    logger.info("Scheduler started. Press Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
