from typing import Callable, Any
from telegram import Update
from telegram.ext import CallbackContext
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod

MAINTENANCE_FILE = "maintenance.flag"

# Helper functions to set/get maintenance mode

def set_maintenance(on: bool):
    if on:
        with open(MAINTENANCE_FILE, "w") as f:
            f.write("on")
    else:
        try:
            import os
            os.remove(MAINTENANCE_FILE)
        except Exception:
            pass

def is_maintenance() -> bool:
    try:
        with open(MAINTENANCE_FILE, "r") as f:
            return f.read().strip() == "on"
    except Exception:
        return False

# Decorator for maintenance protection

def maintenance_protected(func: Callable[[Update, CallbackContext], Any]) -> Callable[[Update, CallbackContext], Any]:
    async def wrapper(update: Update, context: CallbackContext):
        user_id = getattr(update.effective_user, "id", None)
        if is_maintenance():
            owner_ids = get_owner_ids()
            mod = await is_mod(user_id)
            if user_id not in owner_ids and not mod:
                if update.effective_message:
                    await update.effective_message.reply_text("Bot is under maintenance !!")
                return
        return await func(update, context)
    return wrapper

# Command handlers
async def maintenance(update: Update, context: CallbackContext):
    owner_ids = get_owner_ids()
    user_id = getattr(update.effective_user, "id", None)
    if user_id not in owner_ids:
        await update.effective_message.reply_text("Only owners can change maintenance mode.")
        return

    if not context.args:
        await update.effective_message.reply_text("Usage: /maintenance on|off")
        return

    arg = context.args[0].lower()
    if arg == "on":
        set_maintenance(True)
        await update.effective_message.reply_text("Maintenance mode enabled. Only mods and owners can use commands.")
    elif arg == "off":
        set_maintenance(False)
        await update.effective_message.reply_text("Maintenance mode disabled. Bot is now open to all users.")
    else:
        await update.effective_message.reply_text("Usage: /maintenance on|off")
