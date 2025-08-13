
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem
from database.db import Database
import asyncio
import logging

# Scheduler setup for daily midnight tax
async def run_midnight_tax():
    logger = logging.getLogger("scheduler")
    logger.info("[Scheduler] Midnight tax job started!")
    try:
        db = Database()
        await db.init_db()
        bank_system = BankSystem(db)
        await bank_system.check_and_apply_midnight_tax()
        logger.info("[Scheduler] Midnight tax applied successfully!")
    except Exception as e:
        logger.error(f"[Scheduler] Midnight tax failed: {e}", exc_info=True)


def start_scheduler():
    logger = logging.getLogger("scheduler")
    scheduler = AsyncIOScheduler()
    # Schedule job to run every day at midnight
    scheduler.add_job(lambda: asyncio.create_task(run_midnight_tax()), 'cron', hour=0, minute=0)
    scheduler.start()
    logger.info("[Scheduler] Midnight tax scheduler started!")


