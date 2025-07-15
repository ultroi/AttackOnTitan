from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod
from database.db import Database
import logging

logger = logging.getLogger(__name__)

async def add_resource_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        await update.message.reply_text("User not found.")
        return
    user_id = user.id
    owner_ids = get_owner_ids()
    if user_id not in owner_ids and not await is_mod(user_id):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /add <gems|crystal|gas|valor> <amount> [user_id]")
        return
    resource = args[0].lower()
    amount = args[1]
    target_user_id = None
    if len(args) >= 3:
        target_user_id = args[2]
    elif update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
    else:
        await update.message.reply_text("Please specify a user ID or reply to a user's message.")
        return
    try:
        amount = int(amount)
        if amount <= 0:
            await update.message.reply_text("Amount must be positive.")
            return
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    if resource not in ["marks", "crystal", "gas", "valor"]:
        await update.message.reply_text("Resource must be one of: gems, crystal, gas, valor.")
        return
    db: Database = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Database not initialized.")
        return
    player = await db.get_player(str(target_user_id))
    if not player:
        await update.message.reply_text("Target user not found.")
        return
    update_data = {}
    if resource == "marks":
        update_data["marks"] = getattr(player, "marks", 0) + amount
    elif resource == "crystal":
        update_data["crystal"] = getattr(player, "crystal", 0) + amount
    elif resource == "gas":
        update_data["gas"] = getattr(player, "gas", 0) + amount
    elif resource == "valor":
        update_data["valor"] = getattr(player, "valor", 0) + amount
    await db.update_player(target_user_id, update_data)
    await update.message.reply_text(f"Added {amount} {resource} to user {target_user_id}.")

# Register handler in main.py
