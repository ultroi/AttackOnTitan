from datetime import datetime, time, timedelta
import asyncio
from database.db import Database
from game.bank_system import BankSystem

async def send_tax_notification(bot, tax_report):
    """Send tax notification to the user."""
    user_id = tax_report["user_id"]
    tax_message = "💰 **Daily Tax Report**\n\n"
    
    for currency, amount in tax_report["taxes"].items():
        tax_message += f"Tax deducted ({currency}): {amount}\n"
    
    try:
        await bot.send_message(chat_id=user_id, text=tax_message)
    except Exception as e:
        print(f"Failed to send tax notification to {user_id}: {e}")

async def midnight_tax_check(bot):
    """Check and apply taxes at midnight."""
    db = Database()
    bank_system = BankSystem(db)
    
    while True:
        now = datetime.now()
        # Calculate time until next midnight
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min)
        seconds_until_midnight = (midnight - now).total_seconds()
        
        # Wait until midnight
        await asyncio.sleep(seconds_until_midnight)
        
        # Apply taxes and send notifications
        try:
            tax_reports = await bank_system.check_and_apply_midnight_tax()
            
            # Send notifications to taxed players
            for tax_report in tax_reports:
                await send_tax_notification(bot, tax_report)
                
        except Exception as e:
            print(f"Error in midnight tax check: {e}")
        
        # Add a small delay to prevent multiple executions
        await asyncio.sleep(1)

def start_scheduled_tasks(bot):
    """Start all scheduled tasks."""
    asyncio.create_task(midnight_tax_check(bot))
