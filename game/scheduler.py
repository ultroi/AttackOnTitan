from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem, TAX_RATE, TAX_THRESHOLDS
from database.db import Database
import asyncio
import logging
from datetime import datetime, timezone

# Global bot instance for notifications
global_bot = None

# Scheduler setup for daily midnight tax
async def run_midnight_tax():
    """Execute midnight tax collection."""
    logger = logging.getLogger("scheduler")
    current_time = datetime.now()
    
    logger.info(f"[Scheduler] Midnight tax job started at {current_time}!")
    
    try:
        # Initialize database connection
        db = Database()
        await db.init_db()
        bank_system = BankSystem(db)
        
        logger.info("[Scheduler] Database initialized, starting tax collection...")
        
        # Execute tax collection
        tax_reports = await bank_system.check_and_apply_midnight_tax()
        
        logger.info(f"[Scheduler] Tax collection completed. {len(tax_reports)} players taxed.")
        
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
                        
                    # Create tax notification message
                    tax_message = "💰 **Daily Tax Report**\n\n"
                    tax_message += "The following taxes have been deducted from your inventory:\n\n"
                    
                    total_tax = 0
                    for currency, amount in tax_report["taxes"].items():
                        tax_message += f"• {currency.capitalize()}: `{amount}` ({TAX_RATE * 100:.1f}% tax)\n"
                        # Calculate total tax value for summary
                        if currency == 'marks':
                            total_tax += amount
                        elif currency == 'valor':
                            total_tax += amount * 5000  # valor worth
                        elif currency == 'crystal':
                            total_tax += amount * 100000  # crystal worth
                    
                    tax_message += "\nTaxes are collected on amounts exceeding these thresholds:\n"
                    for currency, threshold in TAX_THRESHOLDS.items():
                        tax_message += f"• {currency.capitalize()}: `{threshold}`\n"
                    
                    tax_message += "\n*Tax is collected daily at midnight to fund server operations.*"
                    
                    # Add small delay to avoid rate limits
                    await asyncio.sleep(0.2)
                    
                    # Send notification
                    await global_bot.send_message(
                        chat_id=user_id, 
                        text=tax_message, 
                        parse_mode="Markdown"
                    )
                    
                    logger.info(f"[Scheduler] Tax notification sent to user {user_id}")
                    notification_count += 1
                    
                except Exception as e:
                    logger.error(f"[Scheduler] Failed to send tax notification to {user_id}: {e}")
            
            logger.info(f"[Scheduler] Successfully sent tax notifications to {notification_count} players")
        elif not global_bot:
            logger.warning("[Scheduler] Bot instance not available, skipping notifications")
        else:
            logger.info("[Scheduler] No tax reports to send notifications for")
        
        logger.info(f"[Scheduler] Midnight tax job completed successfully at {datetime.now()}")
        
    except Exception as e:
        logger.error(f"[Scheduler] Midnight tax job failed: {e}", exc_info=True)


def start_scheduler(bot=None):
    """Start the scheduler with midnight tax job."""
    global global_bot
    global_bot = bot
    
    logger = logging.getLogger("scheduler")
    scheduler = AsyncIOScheduler()
    
    # Add job to run every day at midnight (00:00)
    scheduler.add_job(
        lambda: asyncio.run(run_midnight_tax()),
        'cron',
        hour=0,
        minute=0,
        second=0,
        id='midnight_tax_job',
        replace_existing=True,
        misfire_grace_time=300  # Allow up to 5 minutes delay
    )
    
    # Start the scheduler
    scheduler.start()
    
    # Get next run time for logging
    next_run = scheduler.get_job('midnight_tax_job').next_run_time
    
    logger.info(f"[Scheduler] Midnight tax scheduler started successfully!")
    logger.info(f"[Scheduler] Next tax collection scheduled for: {next_run}")
    
    return scheduler


# Function to manually trigger tax for testing
async def manual_tax_trigger(bot=None):
    """Manually trigger tax collection for testing purposes."""
    global global_bot
    if bot:
        global_bot = bot
    
    logger = logging.getLogger("scheduler")
    logger.info("[Manual Trigger] Starting manual tax collection...")
    
    try:
        db = Database()
        await db.init_db()
        bank_system = BankSystem(db)
        
        # Use force_tax_execution for testing
        tax_reports = await bank_system.force_tax_execution()
        
        logger.info(f"[Manual Trigger] Manual tax collection completed. {len(tax_reports)} players taxed.")
        return tax_reports
        
    except Exception as e:
        logger.error(f"[Manual Trigger] Manual tax collection failed: {e}", exc_info=True)
        return []


# Function to check scheduler status
def get_scheduler_status():
    """Get current scheduler status and next run times."""
    try:
        from apscheduler.schedulers.base import STATE_RUNNING, STATE_PAUSED, STATE_STOPPED
        
        # This would need to be called from where scheduler is stored
        # For now, return basic info
        return {
            "status": "running",
            "next_tax_run": "Check logs for next run time",
            "jobs_count": 1
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
