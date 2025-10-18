from typing import Callable, Any
import logging
from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
import time
from utils.owners import get_owner_ids
from database.db_instance import get_database
from utils.mod_utils import is_mod

logger = logging.getLogger(__name__)
# Ban collection name
BAN_COLLECTION = "bans"
BAN_LOG_CHAT_ID = -1002873117075

# Decorator to protect commands from banned users
def ban_protected(func: Callable[[Update, CallbackContext], Any]) -> Callable[[Update, CallbackContext], Any]:
    async def wrapper(*args, **kwargs):
        update = args[0] if args else None
        context = args[1] if len(args) > 1 else None
        user_id = getattr(update.effective_user, 'id', None)
        if user_id is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("User info missing. Cannot process command.")
            return
        try:
            db = await get_database()
            if db is None:
                logger.error(f"⚠️ Database is None in ban_protected for user {user_id}")
                if update.effective_message is not None:
                    await update.effective_message.reply_text("Database unavailable. Please try again later.")
                return
            
            # Check for ban in database
            ban_doc = await db[BAN_COLLECTION].find_one({"user_id": str(user_id)})
            if ban_doc:
                expiry = ban_doc.get("expiry")
                current_time = int(time.time())
                
                # Check if ban has expired
                if expiry and expiry < current_time:
                    # Ban expired, remove it
                    await db[BAN_COLLECTION].delete_one({"user_id": str(user_id)})
                    logger.info(f"✅ Removed expired ban for user {user_id}")
                else:
                    # User is still banned
                    reason = ban_doc.get("reason", "No reason provided")
                    time_left = ""
                    if expiry:
                        hours_left = (expiry - current_time) // 3600
                        minutes_left = ((expiry - current_time) % 3600) // 60
                        if hours_left > 0:
                            time_left = f"\n⏰ <b>Time remaining:</b> {hours_left}h {minutes_left}m"
                        else:
                            time_left = f"\n⏰ <b>Time remaining:</b> {minutes_left}m"
                    
                    # Notify only once per session
                    if context.user_data is None:
                        context.user_data = {}
                    if not context.user_data.get('ban_notified', False):
                        if update.effective_message is not None:
                            ban_message = (
                                f"🚫 <b>You are banned!</b>\n\n"
                                f"<b>Reason:</b> {reason}"
                                f"{time_left}"
                            )
                            await update.effective_message.reply_text(ban_message, parse_mode=ParseMode.HTML)
                        context.user_data['ban_notified'] = True
                        logger.warning(f"⚠️ Blocked banned user {user_id} from using command")
                    return
        except Exception as e:
            logger.error(f"Error checking ban status for user {user_id}: {e}", exc_info=True)
            if update.effective_message is not None:
                await update.effective_message.reply_text("Error accessing database. Please try again later.")
            return
        return await func(*args, **kwargs)
    return wrapper


# Ban command handler
async def ban_user(update: Update, context: CallbackContext):
    if not update.effective_user or not update.effective_chat:
        return

    user_id = update.effective_user.id
    is_owner = user_id in get_owner_ids()
    is_mod_user = await is_mod(user_id)
    # Only allow owners and mods to ban
    if not (is_owner or is_mod_user):
        if update.effective_message is not None:
            await update.effective_message.reply_text("You are not authorized to ban users.")
        return
    args = context.args
    if not args:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Usage: /bfb <user_id> [reason] [duration]")
        return
    # Validate user_id
    try:
        target_id = int(args[0])
        if target_id <= 0:
            raise ValueError
    except Exception:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Invalid user_id. Please provide a valid numeric user ID.")
        return
    # Prevent banning owner
    if target_id in get_owner_ids():
        if update.effective_message is not None:
            await update.effective_message.reply_text("You cannot ban an owner! Don't even try.")
        return
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
    # Owners can ban without reason, mods must provide reason
    if is_mod_user and not is_owner and not reason:
        if update.effective_message is not None:
            await update.effective_message.reply_text("provide a reason for banning.")
        return
    try:
        db = await get_database()
        if db is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("Database unavailable. Please try again later.")
            return
        await db[BAN_COLLECTION].update_one(
            {"user_id": str(target_id)},  # Store as string for consistency
            {"$set": {
                "user_id": str(target_id),
                "expiry": expiry,
                "reason": reason,
                "banned_by": update.effective_user.id,
                "banned_at": int(time.time())
            }},
            upsert=True
        )
        logger.info(f"✅ Banned user {target_id} - Reason: {reason}, Duration: {duration}s")
    except Exception as e:
        logger.error(f"Error banning user {target_id}: {e}")
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
    try:
        # Try to get the user object to get their first name
        target_user = await context.bot.get_chat(target_id)
        target_display = target_user.first_name
    except Exception:
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
async def unban_user(update: Update, context: CallbackContext):
    if not update.effective_user or not update.effective_chat:
        return
    user_id = update.effective_user.id
    is_owner = user_id in get_owner_ids()
    is_mod_user = await is_mod(user_id)
    # Only allow owners and mods to unban
    if not (is_owner or is_mod_user):
        if update.effective_message is not None:
            await update.effective_message.reply_text("You are not authorized to unban users.")
        return
    args = context.args
    if not args:
        if update.effective_message is not None:
            await update.effective_message.reply_text("Usage: /ubfb <user_id> [reason]")
        return
    target_id = int(args[0])
    reason = ''
    if len(args) > 1:
        reason = ' '.join(args[1:]).strip()
    # Owners can unban without reason, mods must provide reason
    if is_mod_user and not is_owner and not reason:
        if update.effective_message is not None:
            await update.effective_message.reply_text("provide a reason")
        return
    try:
        db = await get_database()
        if db is None:
            if update.effective_message is not None:
                await update.effective_message.reply_text("Database unavailable. Please try again later.")
            return
        # Convert to string for consistency with how we store bans
        await db[BAN_COLLECTION].delete_one({"user_id": str(target_id)})
        logger.info(f"✅ Unbanned user {target_id}")
    except Exception as e:
        logger.error(f"Error unbanning user {target_id}: {e}")
        if update.effective_message is not None:
            await update.effective_message.reply_text("Error accessing database. Please try again later.")
        return
    if update.effective_message is not None:
        await update.effective_message.reply_text(f"User {target_id} unbanned.")
    admin = update.effective_user
    try:
        target_user = await context.bot.get_chat(target_id)
        target_display = target_user.first_name
    except Exception:
        target_display = str(target_id)
    msg = (
        f"<b>#UnbanEvent</b>\n\n"
        f"<b>Target</b> : <a href=\"tg://user?id={target_id}\">{target_display}</a>\n"
        f"<b>Target ID</b> : <code>{target_id}</code>\n"
        f"<b>By</b> : <a href=\"tg://user?id={admin.id}\">{admin.first_name}</a>\n"
        f"<b>Reason</b> : <code>{reason}</code>\n"
        f"<b>Time</b> : <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</code>"
    )
    await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)


async def is_banned(user_id: int) -> bool:
    try:
        db = await get_database()
        if db is None:
            return False
        # Convert to string for consistency
        ban_doc = await db[BAN_COLLECTION].find_one({"user_id": str(user_id)})
        if not ban_doc:
            return False
        expiry = ban_doc.get("expiry")
        if expiry and expiry < int(time.time()):
            await db[BAN_COLLECTION].delete_one({"user_id": str(user_id)})
            return False
        return True
    except Exception:
        return False
