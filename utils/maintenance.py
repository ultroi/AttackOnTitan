
from typing import Callable, Any
from telegram import Update
from telegram.ext import CallbackContext
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod
from utils.maintenance_db import set_maintenance_db, is_maintenance_db

# Decorator for maintenance protection

def maintenance_protected(func: Callable[[Update, CallbackContext], Any]) -> Callable[[Update, CallbackContext], Any]:
    async def wrapper(*args, **kwargs):
        update = args[0] if args else None
        context = args[1] if len(args) > 1 else None
        user_id = getattr(update.effective_user, "id", None)
        if await is_maintenance_db():
            owner_ids = get_owner_ids()
            mod = await is_mod(user_id)
            if user_id not in owner_ids and not mod:
                if update.effective_message:
                    await update.effective_message.reply_text("Bot is under maintenance !!")
                return
        return await func(*args, **kwargs)
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
        await set_maintenance_db(True)
        await update.effective_message.reply_text("Maintenance mode enabled. Only mods and owners can use commands.")
    elif arg == "off":
        await set_maintenance_db(False)
        await update.effective_message.reply_text("Maintenance mode disabled. Bot is now open to all users.")
    else:
        await update.effective_message.reply_text("Usage: /maintenance on|off")
