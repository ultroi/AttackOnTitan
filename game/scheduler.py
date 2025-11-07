from apscheduler.schedulers.asyncio import AsyncIOScheduler
from game.bank_system import BankSystem
from database.models import BankAccount
from database.db import Database
from database.db_instance import get_persistent_database
import asyncio
import logging
from datetime import datetime
import pytz  # Import pytz for timezone support

# Global bot instance for notifications
global_bot = None

# Scheduler setup for daily midnight tax (Configured for IST - Indian Standard Time)
async def run_midnight_tax():
    """Execute midnight tax collection at midnight IST (00:00)."""
    logger = logging.getLogger("scheduler")
    ist_timezone = pytz.timezone('Asia/Kolkata')
    
    # Midnight tax job started logging removed for cleaner logs
    
    try:
        # Initialize database connection
        # Note: We're NOT using asyncio.run() here since we're already in an async context
        db = Database()
        try:
            # Ensure we initialize the motor DB using shared instance
            motor_db = await get_persistent_database()
            if motor_db is None:
                logger.error("[Scheduler] Could not get persistent database instance")
                return
            await db.init_db(motor_db)
        except RuntimeError as e:
            if "attached to a different loop" in str(e):
                logger.error("[Scheduler] Event loop error in db initialization - trying to use current loop")
                pass
            else:
                raise
            
        bank_system = BankSystem(db)
        
        # Database initialized logging removed for cleaner logs
        
        # Execute tax collection
        tax_reports = await bank_system.check_and_apply_midnight_tax()

        # Fetch all players once to reuse for opening-penalty checks below
        all_players = await db.get_all_players()

        # Send notifications to users if bot is available
        if global_bot and tax_reports:
            # Tax notifications logging removed for cleaner logs
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
        
        # --- Automatic opening penalty check for players who reached level threshold but didn't open account ---
        try:
            logger.info("[Scheduler] Running automatic opening-penalty checks for eligible players")
            bank_system = BankSystem(db)

            # Iterate players and ensure a BankAccount record exists, then apply opening penalty logic
            for player in all_players:
                try:
                    if not getattr(player, 'user_id', None):
                        continue
                    # Only consider players who reached the bank open level
                    if getattr(player, 'level', 0) < getattr(bank_system, 'BANK_OPEN_LEVEL', 15):
                        continue

                    account = await db.get_bank_account(player.user_id)
                    if not account:
                        # Create a placeholder bank account (not opened)
                        account = BankAccount(
                            user_id=player.user_id,
                            opened=False,
                            opened_at=None,
                            marks_balance=0,
                            valor_balance=0,
                            crystal_balance=0,
                            penalty_start_date=None,
                            penalty_applied=False
                        )
                        await db.save_bank_account(account)

                    # If a 24-hour warning is scheduled and not yet sent, send it now
                    try:
                        if getattr(account, 'penalty_warning_date', None) and not getattr(account, 'penalty_warning_sent', False):
                            warning_date = account.penalty_warning_date
                            now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
                            if warning_date and now_ist >= warning_date:
                                # Send warning via bot if available
                                try:
                                    if global_bot:
                                        warn_text = (
                                            "⚠️ Reminder: You reached the bank-open level but haven't opened your bank account.\n"
                                            "Please open your bank account within 2 more days to avoid penalties.\n"
                                            "Use /bank to open your bank account now."
                                        )
                                        await global_bot.send_message(chat_id=player.user_id, text=warn_text, parse_mode="HTML")
                                        logger.info(f"[Scheduler] Sent bank opening warning to {player.user_id}")
                                except Exception as e:
                                    logger.error(f"[Scheduler] Failed to send opening warning to {player.user_id}: {e}")
                                # Mark warning as sent and persist
                                account.penalty_warning_sent = True
                                await db.save_bank_account(account)
                    except Exception as e:
                        logger.error(f"[Scheduler] Error checking/sending opening warning for {player.user_id}: {e}")

                    # Call the apply_opening_penalty method which will set penalty_start_date or apply penalty
                    try:
                        result_msg = await bank_system.apply_opening_penalty(player, account)
                        # If a message was returned (other than No penalty applied), notify the player
                        if result_msg and result_msg != "No penalty applied.":
                            try:
                                if global_bot:
                                    await global_bot.send_message(chat_id=player.user_id, text=result_msg, parse_mode="HTML")
                                    logger.info(f"[Scheduler] Sent opening-penalty message to {player.user_id}")
                            except Exception as e:
                                logger.error(f"[Scheduler] Failed to send opening penalty message to {player.user_id}: {e}")
                    except Exception as e:
                        logger.error(f"[Scheduler] Error applying opening penalty for {player.user_id}: {e}")
                except Exception as e:
                    logger.error(f"[Scheduler] Error processing player {getattr(player, 'user_id', 'unknown')}: {e}")
            logger.info("[Scheduler] Opening-penalty checks completed")
        except Exception as e:
            logger.error(f"[Scheduler] Failed running opening-penalty checks: {e}")
        
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
    # Use the current event loop implicitly; no explicit assignment needed
    
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
    
    logger.info("[Scheduler] Midnight tax scheduler started successfully!")
    logger.info("[Scheduler] Next tax collection scheduled for: %s", next_run)
    
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
        # This would need to be called from where scheduler is stored
        # For now, return basic info
        return {
            "status": "running",
            "next_tax_run": "Check logs for next run time",
            "jobs_count": 1
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
