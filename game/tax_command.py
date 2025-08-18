from telegram import Update
from telegram.ext import ContextTypes
from game.bank_system import BankSystem
from utils.ban_utils import ban_protected
from utils.mod_utils import mod_only
from utils.maintenance import maintenance_protected
import logging
import asyncio

logger = logging.getLogger(__name__)

from datetime import datetime, timedelta
import logging
import asyncio

logger = logging.getLogger(__name__)

def get_time_until_midnight():
    """Calculate time remaining until midnight"""
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_left = midnight - now
    hours = int(time_left.total_seconds() // 3600)
    minutes = int((time_left.total_seconds() % 3600) // 60)
    return hours, minutes

@maintenance_protected
@ban_protected
async def tax_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to check personal tax status"""
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]
    bank_system = BankSystem(db)
    
    # Get player data
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You don't have a character yet. Use /start to create one!")
        return
    
    # Check tax status
    tax_info = await bank_system.check_player_tax_status(player)
    
    # Calculate time until midnight
    hours, minutes = get_time_until_midnight()
    
    # Create response message
    msg = "💰 **Tax Status**\n\n"
    msg += f"Time until tax collection: {hours}h:{minutes}m\n\n"
    
    # Check if player meets level requirement
    if not tax_info.get('level_requirement_met', True):
        msg += f"�️ **You are exempt from taxation!**\n"
        msg += f"Tax exemption for players below level {tax_info['level_requirement']}.\n\n"
        msg += "Your current balances:\n"
        for currency in ["marks", "valor", "crystal"]:
            balance = tax_info[f'{currency}_balance']
            threshold = tax_info['thresholds'][currency]
            msg += f"• {currency.capitalize()}: {balance} / {threshold}\n"
    elif tax_info["would_be_taxed"]:
        msg += "You would be taxed at midnight for the following currencies:\n\n"
        
        for currency, amount in tax_info["taxes"].items():
            msg += f"• {currency.capitalize()}: {amount} ({tax_info['tax_rate']} of {tax_info[f'{currency}_balance']})\n"
            msg += f"  *Threshold: {tax_info['thresholds'][currency]}*\n\n"
    else:
        msg += "You are below all tax thresholds and would not be taxed at midnight.\n\n"
        
        for currency in ["marks", "valor", "crystal"]:
            balance = tax_info[f'{currency}_balance']
            threshold = tax_info['thresholds'][currency]
            msg += f"• {currency.capitalize()}: {balance} / {threshold}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")


@mod_only
async def force_tax_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to force tax collection"""
    user_id = str(update.effective_user.id)
    
    if not context.args or context.args[0].lower() not in ["confirm", "simulate"]:
        hours, minutes = get_time_until_midnight()
        await update.message.reply_text(
            "⚠️ This will force tax collection for all eligible players.\n"
            "Use `/forcetax confirm` to execute or `/forcetax simulate` to see what would happen without taking action.\n\n"
            f"Time until natural tax collection: {hours}h:{minutes}m"
        )
        return
    
    mode = context.args[0].lower()
    is_simulation = (mode == "simulate")
    
    await update.message.reply_text("Starting tax check process...")
    
    try:
        db = context.bot_data["db"]
        bank_system = BankSystem(db)
        
        # Only perform actual tax collection if not in simulation mode
        if is_simulation:
            # Just count how many players would be taxed
            players_to_tax = 0
            players_exempt_by_level = 0
            total_tax = {"marks": 0, "valor": 0, "crystal": 0}
            
            all_players = await db.get_all_players()
            for player in all_players:
                tax_info = await bank_system.check_player_tax_status(player)
                if player.level < 15:
                    players_exempt_by_level += 1
                elif tax_info["would_be_taxed"]:
                    players_to_tax += 1
                    for currency, amount in tax_info["taxes"].items():
                        total_tax[currency] += amount
            
            hours, minutes = get_time_until_midnight()
            await update.message.reply_text(
                f"💰 **Tax Simulation Results**\n\n"
                f"Time until natural tax collection: {hours}h:{minutes}m\n\n"
                f"• Players that would be taxed: {players_to_tax}\n"
                f"• Players exempt due to being below level 15: {players_exempt_by_level}\n"
                f"• Total taxes that would be collected:\n"
                f"  - Marks: {total_tax['marks']}\n"
                f"  - Valor: {total_tax['valor']}\n"
                f"  - Crystal: {total_tax['crystal']}\n"
            )
        else:
            # Perform actual tax collection using force_tax_execution for admin command
            tax_reports = await bank_system.force_tax_execution()
            
            # Create summary
            total_players = len(tax_reports)
            total_tax = {"marks": 0, "valor": 0, "crystal": 0}
            
            for report in tax_reports:
                for currency, amount in report["taxes"].items():
                    total_tax[currency] += amount
            
            await update.message.reply_text(
                f"✅ **Tax Collection Complete**\n\n"
                f"• Players taxed: {total_players}\n"
                f"• Total collected:\n"
                f"  - Marks: {total_tax['marks']}\n"
                f"  - Valor: {total_tax['valor']}\n"
                f"  - Crystal: {total_tax['crystal']}\n"
            )
            
            # Send notifications to players
            notification_sent = 0
            for report in tax_reports:
                try:
                    tax_message = "💰 **Daily Tax Report**\n\n"
                    for currency, amount in report["taxes"].items():
                        tax_message += f"Tax deducted ({currency}): {amount}\n"
                    
                    await context.bot.send_message(
                        chat_id=report["user_id"],
                        text=tax_message,
                        parse_mode="Markdown"
                    )
                    notification_sent += 1
                    # Small delay to avoid flooding API
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Failed to send tax notification: {e}")
            
            await update.message.reply_text(f"Sent notifications to {notification_sent} players")
    
    except Exception as e:
        logger.error(f"Error in force_tax_check_command: {e}", exc_info=True)
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")
