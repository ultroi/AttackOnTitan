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

    keyboard = [
        [InlineKeyboardButton("👤 User Only", callback_data="broadcast_location_users")],
        [InlineKeyboardButton("👥 Group Only", callback_data="broadcast_location_groups")],
        [InlineKeyboardButton("🌐 Both", callback_data="broadcast_location_both")],
        [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "<b>⚠️ Please select where to broadcast the following message:</b>\n\n"
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
    broadcast_location = context.chat_data.get('broadcast_location')
    if not message_to_broadcast or not broadcast_location:
        await query.edit_message_text("❌ Error: Broadcast message or location not found. Please try again.")
        return

    # Edit the original message to show the broadcast is starting
    await query.edit_message_text("🚀 Broadcast in progress... I will send a report when it's done.")

    # Set the cooldown timestamp
    context.bot_data[LAST_BROADCAST_TIME_KEY] = datetime.now()

    # Run the broadcast in the background
    db = context.bot_data.get("db")
    admin_chat_id = update.effective_chat.id
    asyncio.create_task(_send_broadcast(admin_chat_id, message_to_broadcast, broadcast_location, db, context, query.message.message_id))


@is_owner
async def broadcast_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the location selection for broadcast.
    """
    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    if not message_to_broadcast:
        await query.edit_message_text("❌ Error: Broadcast message not found. Please try again.")
        return

    # Parse the location from callback data
    callback_data = query.data
    if callback_data == "broadcast_cancel":
        # Clear stored data and cancel
        context.chat_data.pop('broadcast_message', None)
        await query.edit_message_text("❌ Broadcast cancelled.")
        return

    location_map = {
        "broadcast_location_users": "users",
        "broadcast_location_groups": "groups", 
        "broadcast_location_both": "both"
    }
    
    broadcast_location = location_map.get(callback_data)
    if not broadcast_location:
        await query.edit_message_text("❌ Error: Invalid location selection.")
        return

    # Store the location choice
    context.chat_data['broadcast_location'] = broadcast_location

    # Show confirmation button
    keyboard = [[InlineKeyboardButton("✅ Confirm Broadcast", callback_data="confirm_broadcast")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    location_text = {
        "users": "👤 Users Only",
        "groups": "👥 Groups Only", 
        "both": "🌐 Both Users and Groups"
    }[broadcast_location]

    await query.edit_message_text(
        f"<b>⚠️ Confirm broadcast to {location_text}:</b>\n\n"
        f"{message_to_broadcast}",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


async def _send_broadcast(admin_chat_id: int, message: str, broadcast_location: str, db: Database, context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
    """
    Fetches users/groups and sends them the broadcast message.
    Runs as a non-blocking background task.
    """
    if not db:
        logger.error("Broadcast failed: Database not available.")
        await context.bot.send_message(admin_chat_id, "❌ Broadcast failed: Database connection is missing.")
        return

    targets = []
    
    # Get targets based on location
    if broadcast_location in ["users", "both"]:
        try:
            all_users = await db.get_all_players()
            user_ids = [user.user_id for user in all_users]
            targets.extend([("user", user_id) for user_id in user_ids])
        except Exception as e:
            logger.error(f"Broadcast failed: Could not fetch users from DB. Error: {e}")
            await context.bot.send_message(admin_chat_id, "❌ Broadcast failed: Could not fetch users from the database.")
            return
    
    if broadcast_location in ["groups", "both"]:
        try:
            all_groups = await db.get_all_groups()
            group_ids = [group.get("group_id") for group in all_groups if group.get("group_id")]
            targets.extend([("group", group_id) for group_id in group_ids])
        except Exception as e:
            logger.error(f"Broadcast failed: Could not fetch groups from DB. Error: {e}")
            await context.bot.send_message(admin_chat_id, "❌ Broadcast failed: Could not fetch groups from the database.")
            return

    if not targets:
        await context.bot.send_message(admin_chat_id, "❌ No targets found for broadcast.")
        return

    success_count = 0
    failure_count = 0
    total_count = len(targets)

    # Update message with initial progress
    try:
        await context.bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=message_id,
            text=f"📡 <b>Broadcasting in Progress</b>\nCompleted: 0/{total_count}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not update initial progress message: {e}")

    for i, (target_type, target_id) in enumerate(targets):
        try:
            await context.bot.send_message(chat_id=target_id, text=message, parse_mode=ParseMode.HTML)
            success_count += 1
        except Forbidden:
            # User/group has blocked the bot
            failure_count += 1
            logger.warning(f"Broadcast failed for {target_type} {target_id}: Bot was blocked.")
        except BadRequest:
            # Chat not found or other issue
            failure_count += 1
            logger.warning(f"Broadcast failed for {target_type} {target_id}: Chat not found or bad request.")
        except Exception as e:
            failure_count += 1
            logger.error(f"Broadcast failed for {target_type} {target_id}: {e}")

        # Update progress every 10 messages or at the end
        if (i + 1) % 10 == 0 or i == total_count - 1:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=message_id,
                    text=f"📡 <b>Broadcasting in Progress</b>\nCompleted: {i+1}/{total_count}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not update progress message: {e}")

        # Sleep for a short duration to avoid hitting rate limits
        await asyncio.sleep(0.1)

    # Send final report
    location_text = {
        "users": "👤 Users Only",
        "groups": "👥 Groups Only", 
        "both": "🌐 Both Users and Groups"
    }[broadcast_location]

    report_message = (
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"<b>Location:</b> {location_text}\n"
        f"<b>Message:</b> {message}\n\n"
        f"📊 <b>Results:</b>\n"
        f"✅ Sent successfully: {success_count}\n"
        f"❌ Failed to send: {failure_count}\n"
        f"📈 Total targets: {total_count}"
    )
    
    try:
        await context.bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=message_id,
            text=report_message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        # If edit fails, send as new message
        await context.bot.send_message(admin_chat_id, report_message, parse_mode=ParseMode.HTML)
