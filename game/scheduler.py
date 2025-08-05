from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem
from database.db_instance import db_instance
import asyncio

# Scheduler setup for daily midnight tax
async def run_midnight_tax():
    bank_system = BankSystem(db_instance)
    await bank_system.check_and_apply_midnight_tax()
    print("[Scheduler] Midnight tax applied!")


def start_scheduler():
    scheduler = AsyncIOScheduler()
    # Schedule job to run every day at midnight
    scheduler.add_job(lambda: asyncio.create_task(run_midnight_tax()), 'cron', hour=0, minute=0)
    scheduler.start()
    print("[Scheduler] Midnight tax scheduler started!")


