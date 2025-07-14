from typing import Callable, Any
from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
import time
from utils.owners import get_owner_ids
from database.db_instance import get_database

MOD_COLLECTION = "mods"

async def is_mod(user_id: int) -> bool:
    db = await get_database()
    if db is None:
        return False
    mod_doc = await db[MOD_COLLECTION].find_one({"user_id": user_id})
    return bool(mod_doc)

async def promote_mod(update: Update, context: CallbackContext):
    if not update.effective_user or not update.effective_chat:
        return
    if update.effective_user.id not in get_owner_ids():
        if update.effective_message is not None:
            await update.effective_message.reply_text("Only owners can promote mods.")
        return
    args = context.args
    if not args:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Usage: /mod <user_id>")
        return
    try:
        target_id = int(args[0])
        if target_id <= 0:
            raise ValueError
    except Exception:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Invalid user_id. Please provide a valid numeric user ID.")
        return
    db = await get_database()
    if db is None:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Database unavailable. Please try again later.")
        return
    await db[MOD_COLLECTION].update_one(
        {"user_id": target_id},
        {"$set": {"user_id": target_id, "promoted_by": update.effective_user.id, "promoted_at": int(time.time())}},
        upsert=True
    )
    if update.effective_message is not None:
        await update.effective_message.reply_text(f"User {target_id} promoted to MOD.")

