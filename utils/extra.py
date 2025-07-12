import logging
from telegram import Update
from utils.ban_utils import ban_protected
from game.explore import _reply_error
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

@ban_protected
async def buy_command(update: Update, context):
                try:
                    if not update.effective_user or not update.message:
                        if update.message:
                            await update.message.reply_text("User or message information not available.")
                        return
                    args = context.args
                    if len(args) != 2:
                        await update.message.reply_text("Usage: /buy <currency_type> <amount>")
                        return
                    currency_type = args[0]
                    try:
                        amount = int(args[1])
                    except ValueError:
                        await update.message.reply_text("Amount must be an integer.")
                        return
                    shop_system = context.bot_data["shop_system"]
                    user_id = str(update.effective_user.id)
                    result = await shop_system.buy_currency(context, user_id, currency_type, amount)
                    await update.message.reply_text(result)
                except Exception as e:
                    logger.error(f"Error in buy_command: {e}")
                    if update.message:
                        await update.message.reply_text(f"Error processing buy command: {str(e)}")




@ban_protected
async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /give command to share resources."""
    if not update.effective_user or not update.message or not update.message.reply_to_message:
        await _reply_error(update, "You must reply to a user's message to give resources.")
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)
    target_msg = update.message.reply_to_message
    target_user = getattr(target_msg, 'from_user', None)
    if not target_user:
        await _reply_error(update, "Target user not found.")
        return
    target_id = getattr(target_user, 'id', None)
    target_id_str = str(target_id) if target_id else None
    first_name = update.effective_user.first_name or "Player"
    target_first_name = f'<a href="tg://user?id={target_id}">{getattr(target_user, "first_name", "Unknown")}</a>'

    # Check for active battle
    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None

    if active_battles_lock:
        async with active_battles_lock:
            if user_id_str in active_battles:
                await update.message.reply_text(
                    f"<a href=\"tg://user?id={user_id}\">{first_name}</a> is currently battling !!", parse_mode=ParseMode.HTML)
                return
    else:
        if user_id_str in active_battles:
            await update.message.reply_text(
                f"<a href=\"tg://user?id={user_id}\">{first_name}</a> is currently battling !!", parse_mode=ParseMode.HTML)
            return

    # Parse command args
    args = context.args if context.args else []
    if len(args) < 2:
        await update.message.reply_text("Usage: /give <amount> <item>")
        return
    try:
        amount = int(args[0])
    except Exception:
        await update.message.reply_text("Invalid amount.")
        return
    item = args[1].lower() if isinstance(args[1], str) else ''
    allowed_items = ["crystal", "valor", "marks", "gas"]
    if item not in allowed_items:
        await update.message.reply_text(f"You can only give: {', '.join(allowed_items)}")
        return

    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Database unavailable.")
        return

    # Deduct from sender, add to receiver
    sender = await db.get_player(user_id_str)
    receiver = await db.get_player(target_id_str) if target_id_str else None
    if not sender or not receiver:
        await update.message.reply_text("Both users must have profiles.")
        return
    if getattr(sender, item, 0) < amount:
        await update.message.reply_text(f"You don't have enough {item}.")
        return
    await db.update_player(user_id_str, {item: getattr(sender, item, 0) - amount})
    await db.update_player(target_id_str, {item: getattr(receiver, item, 0) + amount})

    # Log the transaction
    log_msg = (
        f"<b>#GiveEvent</b>\n\n"
        f"<b>From</b>: <a href=\"tg://user?id={user_id}\">{first_name}</a>\n"
        f"<b>From ID</b>: <code>{user_id_str}</code>\n"
        f"<b>To</b>: <a href=\"tg://user?id={target_id}\">{target_first_name}</a>\n"
        f"<b>To ID</b>: <code>{target_id_str}</code>\n"
        f"<b>Item</b>: <code>{item}</code>\n"
        f"<b>Amount</b>: <code>{amount}</code>"
    )
    GIVE_LOG_CHAT_ID = -1002686338026
    await update.message.reply_text(f"Successfully gave {amount} {item} to {target_first_name}.")
    await context.bot.send_message(GIVE_LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)