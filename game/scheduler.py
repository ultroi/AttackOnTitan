
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem, TAX_RATE, TAX_THRESHOLDS
from database.db import Database
import asyncio
import logging

# Global bot instance for notifications
global_bot = None

# Scheduler setup for daily midnight tax
async def run_midnight_tax():
    logger = logging.getLogger("scheduler")
    logger.info("[Scheduler] Midnight tax job started!")
    try:
        db = Database()
        await db.init_db()
        bank_system = BankSystem(db)
        tax_reports = await bank_system.check_and_apply_midnight_tax()
        
        # Send notifications to users if bot is available
        if global_bot and tax_reports:
            logger.info(f"[Scheduler] Sending tax notifications to {len(tax_reports)} players")
            notification_count = 0
            
            for tax_report in tax_reports:
                try:
                    user_id = tax_report["user_id"]
                    
                    # Skip if there are no taxes to report
                    if not tax_report["taxes"]:
                        continue
                        
                    tax_message = "💰 **Daily Tax Report**\n\n"
                    tax_message += "The following taxes have been deducted from your inventory:\n\n"
                    
                    for currency, amount in tax_report["taxes"].items():
                        tax_message += f"• {currency.capitalize()}: {amount} ({TAX_RATE * 100:.1f}% tax)\n"
                    
                    tax_message += "\nTaxes are collected on amounts exceeding these thresholds:\n"
                    for currency, threshold in TAX_THRESHOLDS.items():
                        tax_message += f"• {currency.capitalize()}: {threshold}\n"
                    
                    # Use a delay to avoid hitting rate limits
                    await asyncio.sleep(0.1)
                    await global_bot.send_message(
                        chat_id=user_id, 
                        text=tax_message, 
                        parse_mode="Markdown"
                    )
                    logger.info(f"[Scheduler] Tax notification sent to user {user_id}")
                    notification_count += 1
                except Exception as e:
                    logger.error(f"[Scheduler] Failed to send tax notification to {user_id}: {e}")
            
            logger.info(f"[Scheduler] Sent tax notifications to {notification_count} players")
        
        logger.info(f"[Scheduler] Midnight tax applied successfully! Processed {len(tax_reports)} players.")
    except Exception as e:
        logger.error(f"[Scheduler] Midnight tax failed: {e}", exc_info=True)


def start_scheduler(bot=None):
    global global_bot
    global_bot = bot
    logger = logging.getLogger("scheduler")
    scheduler = AsyncIOScheduler()
    # Schedule job to run every day at midnight
    scheduler.add_job(lambda: asyncio.create_task(run_midnight_tax()), 'cron', hour=0, minute=0)
    scheduler.start()
    logger.info("[Scheduler] Midnight tax scheduler started!")


