"""
Handles the /broadcast command for sending messages to all users.
This command is owner-only and includes a confirmation step, cooldown, and non-blocking execution.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from database.db import Database
from utils.owners import is_owner

logger = logging.getLogger(__name__)

# Cooldown period for the broadcast command to prevent spam
BROADCAST_COOLDOWN = timedelta(minutes=10)
LAST_BROADCAST_TIME_KEY = "last_broadcast_time"


@is_owner
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Initiates a broadcast. Only available to bot owners.
    Usage: /broadcast <message> or reply to a message with /broadcast
    """
    if not update.effective_user or not update.message:
        return

    # --- Cooldown Check ---
    last_broadcast_time = context.bot_data.get(LAST_BROADCAST_TIME_KEY)
    if last_broadcast_time and (datetime.now() - last_broadcast_time) < BROADCAST_COOLDOWN:
        time_remaining = BROADCAST_COOLDOWN - (datetime.now() - last_broadcast_time)
        await update.message.reply_text(
            f"⏳ Broadcast is on cooldown. Please wait {time_remaining.seconds // 60} more minutes."
        )
        return

    # --- Message Content Check ---
    if update.message.reply_to_message:
        # Use the replied-to message as the broadcast content
        message_to_broadcast = update.message.reply_to_message.text or update.message.reply_to_message.caption
    else:
        # Use text after the command
        message_to_broadcast = update.message.text.partition(' ')[2]
        
    if not message_to_broadcast:
        await update.message.reply_text(
            "Please provide a message to broadcast or reply to a message.\n\n"
            "<b>Usage:</b> /broadcast <i>Your message here</i> or reply to any message with /broadcast",
            parse_mode=ParseMode.HTML
        )
        return

    # Convert message to bold format
    message_to_broadcast = f"<b>{message_to_broadcast}</b>"

    # --- Confirmation Step ---
    # Store the message temporarily for the callback handler
    context.chat_data['broadcast_message'] = message_to_broadcast

    keyboard = [[InlineKeyboardButton("✅ Confirm Broadcast", callback_data="confirm_broadcast")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "<b>⚠️ Please confirm you want to broadcast the following message to all users:</b>\n\n"
        f"{message_to_broadcast}",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


@is_owner
async def confirm_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the confirmation button press for the broadcast.
    """
    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    if not message_to_broadcast:
        await query.edit_message_text("❌ Error: Broadcast message not found. Please try again.")
        return

    # Edit the original message to show the broadcast is starting
    await query.edit_message_text("🚀 Broadcast in progress... I will send a report when it's done.")

    # Set the cooldown timestamp
    context.bot_data[LAST_BROADCAST_TIME_KEY] = datetime.now()

    # Run the broadcast in the background
    db = context.bot_data.get("db")
    admin_chat_id = update.effective_chat.id
    asyncio.create_task(_send_broadcast(admin_chat_id, message_to_broadcast, db, context))


async def _send_broadcast(admin_chat_id: int, message: str, db: Database, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fetches all users and sends them the broadcast message.
    Runs as a non-blocking background task.
    """
    if not db:
        logger.error("Broadcast failed: Database not available.")
        await context.bot.send_message(admin_chat_id, "❌ Broadcast failed: Database connection is missing.")
        return

    try:
        all_users = await db.get_all_players()
        user_ids = [user.user_id for user in all_users]
    except Exception as e:
        logger.error(f"Broadcast failed: Could not fetch users from DB. Error: {e}")
        await context.bot.send_message(admin_chat_id, "❌ Broadcast failed: Could not fetch users from the database.")
        return

    success_count = 0
    failure_count = 0

    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML)
            success_count += 1
        except Forbidden:
            # User has blocked the bot
            failure_count += 1
            logger.warning(f"Broadcast failed for user {user_id}: Bot was blocked.")
        except BadRequest:
            # Chat not found or other issue
            failure_count += 1
            logger.warning(f"Broadcast failed for user {user_id}: Chat not found or bad request.")
        except Exception as e:
            failure_count += 1
            logger.error(f"Broadcast failed for user {user_id}: {e}")

        # Sleep for a short duration to avoid hitting rate limits
        await asyncio.sleep(0.1)

    # Send final report to the admin
    report_message = (
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"Sent to: {success_count} users\n"
        f"Failed for: {failure_count} users"
    )
    await context.bot.send_message(admin_chat_id, report_message, parse_mode=ParseMode.HTML)
