from datetime import datetime, time, timedelta
import asyncio
import logging
from database.db import Database
from game.bank_system import BankSystem

logger = logging.getLogger(__name__)

async def send_tax_notification(bot, tax_report):
    """Send tax notification to the user."""
    user_id = tax_report["user_id"]
    tax_message = "💰 **Daily Tax Report**\n\n"
    
    for currency, amount in tax_report["taxes"].items():
        tax_message += f"Tax deducted ({currency}): {amount}\n"
    
    try:
        await bot.send_message(chat_id=user_id, text=tax_message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send tax notification to {user_id}: {e}")

# Note: The actual tax scheduling is now handled in game/scheduler.py
# This function remains for backward compatibility
def start_scheduled_tasks(bot):
    """Start all scheduled tasks.
    Note: Tax task is now handled by game/scheduler.py
    """
    # Scheduled tasks logging removed for cleaner logs
    pass
