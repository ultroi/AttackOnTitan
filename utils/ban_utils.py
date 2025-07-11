from typing import Callable, Any
from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
import time
from utils.owners import get_owner_ids
from database.db_instance import get_database


# Ban collection name
BAN_COLLECTION = "bans"
BAN_LOG_CHAT_ID = -1002873117075

# Decorator to protect commands from banned users
def ban_protected(func: Callable[[Update, CallbackContext], Any]) -> Callable[[Update, CallbackContext], Any]:
    async def wrapper(update: Update, context: CallbackContext):
        user_id = getattr(update.effective_user, 'id', None)
        if user_id is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("User info missing. Cannot process command.")
            return
        try:
            db = await get_database()
            if db is None:
                if update.effective_message is not None:
                    await update.effective_message.reply_text("Database unavailable. Please try again later.")
                return
            ban_doc = await db[BAN_COLLECTION].find_one({"user_id": user_id})
            if ban_doc:
                expiry = ban_doc.get("expiry")
                if expiry and expiry < int(time.time()):
                    # Ban expired, remove
                    await db[BAN_COLLECTION].delete_one({"user_id": user_id})
                else:
                    # Notify only once per session
                    if context.user_data is None:
                        context.user_data = {}
                    if not context.user_data.get('ban_notified', False):
                        if update.effective_message is not None:
                            await update.effective_message.reply_text("You are banned!!")
                        context.user_data['ban_notified'] = True
                    # After first notification, do not respond to further commands
                    return
        except Exception:
            if update.effective_message is not None:
                await update.effective_message.reply_text("Error accessing database. Please try again later.")
            return
        return await func(update, context)
    return wrapper


# Ban command handler
@ban_protected
async def ban_user(update: Update, context: CallbackContext):
    if not update.effective_user or not update.effective_chat:
        return
    # Only allow owners to ban
    if update.effective_user.id not in get_owner_ids():
        if update.effective_message is not None:
            await update.effective_message.reply_text("You are not authorized to ban users. Only owners can use this command.")
        return
    args = context.args
    if not args:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Usage: /bfb <user_id> [reason] [duration]")
        return
    target_id = int(args[0])
    reason = ''
    duration = None
    # Parse reason and duration
    if len(args) > 1:
        for i, arg in enumerate(args[1:], 1):
            if arg.endswith('h') or arg.endswith('d'):
                # Duration
                val = arg[:-1]
                try:
                    val = int(val)
                    if arg.endswith('h'):
                        duration = val * 3600
                    elif arg.endswith('d'):
                        duration = val * 86400
                except Exception:
                    pass
            else:
                reason += (arg + ' ')
        reason = reason.strip()
    expiry = None
    if duration:
        expiry = int(time.time()) + duration
    try:
        db = await get_database()
        if db is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("Database unavailable. Please try again later.")
            return
        await db[BAN_COLLECTION].update_one(
            {"user_id": target_id},
            {"$set": {"user_id": target_id, "expiry": expiry, "reason": reason, "banned_by": update.effective_user.id, "banned_at": int(time.time())}},
            upsert=True
        )
    except Exception:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Error accessing database. Please try again later.")
        return
    # Notify banned user (if possible)
    try:
        if duration:
            await context.bot.send_message(target_id, f"You have been banned for {duration//3600 if duration>=3600 else duration//60} {'hours' if duration>=3600 else 'minutes'}.")
        else:
            await context.bot.send_message(target_id, "You have been permanently banned.")
    except Exception:
        pass

    # Log to group in proper format
    admin = update.effective_user
    time_str = f"{duration//3600 if duration and duration>=3600 else duration//60 if duration else 'Permanent'} {'hours' if duration and duration>=3600 else 'minutes' if duration else ''}" if duration else 'Permanent'
    if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_display = update.message.reply_to_message.from_user.first_name
    else:
        target_display = str(target_id)
    msg = (
        f"<b>#BanEvent</b>\n\n"
        f"<b>Target</b> : <a href=\"tg://user?id={target_id}\">{target_display}</a>\n"
        f"<b>Target ID</b> : <code>{target_id}</code>\n"
        f"<b>By</b> : <a href=\"tg://user?id={admin.id}\">{admin.first_name}</a>\n"
        f"<b>Reason</b> : <code>{reason}</code>\n"
        f"<b>Time</b> : <code>{time_str}</code>"
    )
    await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)
    if update.effective_message is not None:
        await update.effective_message.reply_text(f"User {target_id} banned. Time: {time_str}")

# Unban command handler
@ban_protected
async def unban_user(update: Update, context: CallbackContext):
    if not update.effective_user or not update.effective_chat:
        return
    # Only allow admins to unban
    if update.effective_user.id not in get_owner_ids():
        if update.effective_message is not None:
            await update.effective_message.reply_text("You are not authorized to unban users.")
        return
    args = context.args
    if not args:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Usage: /ubfb <user_id>")
        return
    target_id = int(args[0])
    try:
        db = await get_database()
        if db is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("Database unavailable. Please try again later.")
            return
        await db[BAN_COLLECTION].delete_one({"user_id": target_id})
    except Exception:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Error accessing database. Please try again later.")
        return
    if update.effective_message is not None:
        await update.effective_message.reply_text(f"User {target_id} unbanned.")
    # Log to group in proper format
    admin = update.effective_user
    if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_display = update.message.reply_to_message.from_user.first_name
    else:
        target_display = str(target_id)
    msg = (
        f"<b>#UnbanEvent</b>\n\n"
        f"<b>Target</b> : <a href=\"tg://user?id={target_id}\">{target_display}</a>\n"
        f"<b>Target ID</b> : <code>{target_id}</code>\n"
        f"<b>By</b> : <a href=\"tg://user?id={admin.id}\">{admin.first_name}</a>\n"
        f"<b>Time</b> : <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</code>"
    )
    await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)


async def is_banned(user_id: int) -> bool:
    try:
        db = await get_database()
        if db is None:
            return False
        ban_doc = await db[BAN_COLLECTION].find_one({"user_id": user_id})
        if not ban_doc:
            return False
        expiry = ban_doc.get("expiry")
        if expiry and expiry < int(time.time()):
            await db[BAN_COLLECTION].delete_one({"user_id": user_id})
            return False
        return True
    except Exception:
        return False
