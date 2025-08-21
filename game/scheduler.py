from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem, TAX_RATE, TAX_THRESHOLDS
from database.db import Database
import asyncio
import logging
from datetime import datetime, timezone
import pytz  # Import pytz for timezone support

# Global bot instance for notifications
global_bot = None

# Scheduler setup for daily midnight tax (Configured for IST - Indian Standard Time)
async def run_midnight_tax():
    """Execute midnight tax collection at midnight IST (00:00)."""
    logger = logging.getLogger("scheduler")
    ist_timezone = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist_timezone)
    
    logger.info(f"[Scheduler] Midnight tax job started at {current_time} (IST)!")
    
    try:
        # Initialize database connection
        # Note: We're NOT using asyncio.run() here since we're already in an async context
        db = Database()
        try:
            # Ensure we're initializing with the current event loop
            await db.init_db()
        except RuntimeError as e:
            if "attached to a different loop" in str(e):
                logger.error("[Scheduler] Event loop error in db initialization - trying to use current loop")
                # We'll continue without reinitializing, as the scheduler should have its own event loop
                # and the database should be initialized in the main application
                pass
            else:
                raise
            
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
                    tax_message = "💰 **Tax Deducted:**\n"
                    for currency, amount in tax_report["taxes"].items():
                        tax_message += f"• {currency.capitalize()}: {amount}\n"
                    
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
        
        logger.info(f"[Scheduler] Midnight tax job completed successfully at {datetime.now(ist_timezone)} (IST)")
        
    except Exception as e:
        logger.error(f"[Scheduler] Midnight tax job failed: {e}", exc_info=True)


def start_scheduler(bot=None):
    """Start the scheduler with midnight tax job."""
    global global_bot
    global_bot = bot
    
    logger = logging.getLogger("scheduler")
    # Create scheduler with the event loop of the application
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")  # Changed from UTC to IST timezone
    # Ensure it uses the right event loop
    asyncio_event_loop = asyncio.get_event_loop()
    
    # Add job to run every day at midnight (00:00) in IST timezone
    scheduler.add_job(
        run_midnight_tax,  # Pass the coroutine directly, don't wrap in asyncio.run()
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
        # Create database with explicit connection to current event loop
        db = Database()
        # Make sure init_db is called from the same context/event loop
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
