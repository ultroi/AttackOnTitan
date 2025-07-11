import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from utils.owners import get_owner_ids


# Channel for reset logs
RESET_LOG_CHANNEL = -1002873117075

def hlink(name, url):
    return f'<a href="{url}">{name}</a>'

def hcode(text):
    return f'<code>{text}</code>'

async def reset_user_data(db, user_id: str):
    await db.characters.delete_many({"user_id": user_id})
    await db.players.delete_many({"user_id": user_id})
    await db.titans.delete_many({"user_id": user_id})
    await db.equipment.delete_many({"user_id": user_id})
    await db.shop_purchases_collection.delete_many({"user_id": user_id})

async def send_reset_log(context: ContextTypes.DEFAULT_TYPE, target_user, by_user, reason):
    name_link = hlink(target_user.first_name, f"tg://user?id={target_user.id}")
    by_link = hlink(by_user.first_name, f"tg://user?id={by_user.id}")
    log_text = (
        f"<b>#Reset</b>\n\n"
        f"<b>Name :</b> {name_link}\n"
        f"<b>ID :</b> {hcode(str(target_user.id))}\n"
        f"<b>By :</b> {by_link}\n"
        f"<b>Reason :</b> {reason if reason else 'None'}"
    )
    await context.bot.send_message(chat_id=RESET_LOG_CHANNEL, text=log_text, parse_mode=ParseMode.HTML)

async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_ids = get_owner_ids()
    user = update.effective_user
    if not user or user.id not in owner_ids:
        return
    args = context.args
    if not args:
        if update.message:
            await update.message.reply_text("Usage: /nuke <user_id> [reason]")
        return
    target_id = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else None
    try:
        # Use pre-initialized db instance from bot_data for speed
        db = context.bot_data.get("db")
        if db is None:
            if update.message:
                await update.message.reply_text("Database instance not found. Reset aborted.")
            return
        await reset_user_data(db, target_id)
        # Try to get user info for logging
        try:
            target_user = await context.bot.get_chat(target_id)
        except Exception:
            class Dummy:
                id = target_id
                first_name = "Unknown"
            target_user = Dummy()
        await send_reset_log(context, target_user, user, reason)
        if update.message:
            await update.message.reply_text(f"All data for user {target_id} has been reset.")
    except Exception as e:
        logging.exception("Reset failed")
        if update.message:
            await update.message.reply_text(f"Failed to reset user: {e}")


