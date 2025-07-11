

from typing import Callable, Any
from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
import time
from utils.extra import get_owner_ids
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
                    # Notify only once
                    if not hasattr(context.user_data, 'ban_notified'):
                        if update.effective_message is not None:
                            await update.effective_message.reply_text("You are banned from using commands.")
                        if context.user_data is not None:
                            context.user_data['ban_notified'] = True
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
            context.bot.send_message(target_id, f"You have been banned for {duration//3600 if duration>=3600 else duration//60} {'hours' if duration>=3600 else 'minutes'}.")
        else:
            context.bot.send_message(target_id, "You have been permanently banned.")
    except Exception:
        pass

    # Log to group in proper format
    admin = update.effective_user
    time_str = f"{duration//3600 if duration and duration>=3600 else duration//60 if duration else 'Permanent'} {'hours' if duration and duration>=3600 else 'minutes' if duration else ''}" if duration else 'Permanent'
    msg = (
        f"#BanEvent\n\n"
        f"Target : [{target_id}](tg://user?id={target_id})\n"
        f"Target ID : {target_id}\n"
        f"By : [{admin.first_name}](tg://user?id={admin.id})\n"
        f"Reason : [{reason}]\n"
        f"Time : {time_str}"
    )
    context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)
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
    msg = (
        f"#UnbanEvent\n\n"
        f"Target : [{target_id}](tg://user?id={target_id})\n"
        f"Target ID : {target_id}\n"
        f"By : [{admin.first_name}](tg://user?id={admin.id})\n"
        f"Time : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
    )
    context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)


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
