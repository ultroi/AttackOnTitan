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

    # --- Broadcast Type Selection ---
    # Store the message temporarily for the callback handler
    context.chat_data['broadcast_message'] = message_to_broadcast

    keyboard = [
        [InlineKeyboardButton("� Simple Broadcast", callback_data="broadcast_type_simple")],
        [InlineKeyboardButton("🗳️ Vote Broadcast", callback_data="broadcast_type_vote")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "<b>⚠️ Select Broadcast Type:</b>\n\n"
        f"{message_to_broadcast}\n\n"
        "<b>Simple Broadcast:</b> Send message to all users\n"
        "<b>Vote Broadcast:</b> Send message with voting options",
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
    broadcast_type = context.chat_data.get('broadcast_type', 'simple')
    vote_options = context.chat_data.get('vote_options', [])
    
    if not message_to_broadcast or not broadcast_location:
        await query.edit_message_text("❌ Error: Broadcast message or location not found. Please try again.")
        return

    # Edit the original message to show the broadcast is starting
    broadcast_name = "Vote Broadcast" if broadcast_type == "vote" else "Broadcast"
    await query.edit_message_text(f"🚀 {broadcast_name} in progress... I will send a report when it's done.")

    # Set the cooldown timestamp
    context.bot_data[LAST_BROADCAST_TIME_KEY] = datetime.now()

    # Run the broadcast in the background
    db = context.bot_data.get("db")
    admin_chat_id = update.effective_chat.id
    
    if broadcast_type == "vote":
        asyncio.create_task(_send_vote_broadcast(admin_chat_id, message_to_broadcast, broadcast_location, vote_options, db, context, query.message.message_id))
    else:
        asyncio.create_task(_send_broadcast(admin_chat_id, message_to_broadcast, broadcast_location, db, context, query.message.message_id))


@is_owner
async def broadcast_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the broadcast type selection.
    """
    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    if not message_to_broadcast:
        await query.edit_message_text("❌ Error: Broadcast message not found. Please try again.")
        return

    # Parse the type from callback data
    callback_data = query.data
    if callback_data == "broadcast_type_simple":
        broadcast_type = "simple"
    elif callback_data == "broadcast_type_vote":
        broadcast_type = "vote"
    else:
        await query.edit_message_text("❌ Error: Invalid broadcast type selection.")
        return

    # Store the type choice
    context.chat_data['broadcast_type'] = broadcast_type

    # Show location selection
    keyboard = [
        [InlineKeyboardButton("👤 User Only", callback_data="broadcast_location_users")],
        [InlineKeyboardButton("👥 Group Only", callback_data="broadcast_location_groups")],
        [InlineKeyboardButton("🌐 Both", callback_data="broadcast_location_both")],
        [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    type_text = "📢 Simple Broadcast" if broadcast_type == "simple" else "🗳️ Vote Broadcast"

    await query.edit_message_text(
        f"<b>⚠️ {type_text} - Select Location:</b>\n\n"
        f"{message_to_broadcast}",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


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
    broadcast_type = context.chat_data.get('broadcast_type')
    if not message_to_broadcast or not broadcast_type:
        await query.edit_message_text("❌ Error: Broadcast message or type not found. Please try again.")
        return

    # Parse the location from callback data
    callback_data = query.data
    if callback_data == "broadcast_cancel":
        # Clear stored data and cancel
        context.chat_data.pop('broadcast_message', None)
        context.chat_data.pop('broadcast_type', None)
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

    if broadcast_type == "vote":
        # For vote broadcasts, show vote options setup
        keyboard = [
            [InlineKeyboardButton("✅ Yes/No", callback_data="vote_options_yesno")],
            [InlineKeyboardButton("📊 Custom Options", callback_data="vote_options_custom")],
            [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        location_text = {
            "users": "👤 Users Only",
            "groups": "👥 Groups Only", 
            "both": "🌐 Both Users and Groups"
        }[broadcast_location]

        await query.edit_message_text(
            f"<b>🗳️ Vote Broadcast to {location_text}:</b>\n\n"
            f"{message_to_broadcast}\n\n"
            "<b>Select Vote Options:</b>",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    else:
        # For simple broadcasts, show confirmation
        keyboard = [[InlineKeyboardButton("✅ Confirm Broadcast", callback_data="confirm_broadcast")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        location_text = {
            "users": "👤 Users Only",
            "groups": "👥 Groups Only", 
            "both": "🌐 Both Users and Groups"
        }[broadcast_location]

        await query.edit_message_text(
            f"<b>⚠️ Confirm Simple Broadcast to {location_text}:</b>\n\n"
            f"{message_to_broadcast}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )


@is_owner
async def vote_options_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    broadcast_location = context.chat_data.get('broadcast_location')
    broadcast_type = context.chat_data.get('broadcast_type')
    if not all([message_to_broadcast, broadcast_location, broadcast_type]):
        await query.edit_message_text("❌ Error: Broadcast data not found. Please try again.")
        return

    callback_data = query.data
    if callback_data == "vote_options_yesno":
        # Store vote options and show confirmation
        context.chat_data['vote_options'] = ["✅ Yes", "❌ No"]
        vote_text = "Yes/No"
    elif callback_data == "vote_options_custom":
        # Show custom options count keyboard
        keyboard = [
            [InlineKeyboardButton("2 Options", callback_data="custom_count_2"),
             InlineKeyboardButton("3 Options", callback_data="custom_count_3")],
            [InlineKeyboardButton("4 Options", callback_data="custom_count_4"),
             InlineKeyboardButton("5 Options", callback_data="custom_count_5")],
            [InlineKeyboardButton("❌ Cancel", callback_data="custom_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"<b>📊 Custom Vote Options Setup</b>\n\n"
            f"{message_to_broadcast}\n\n"
            f"<b>How many voting options do you want?</b>\n"
            f"Choose 2-5 options for your custom poll:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
    else:
        await query.edit_message_text("❌ Error: Invalid vote options selection.")
        return

    # Show confirmation for vote broadcast
    keyboard = [[InlineKeyboardButton("✅ Confirm Vote Broadcast", callback_data="confirm_broadcast")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    location_text = {
        "users": "👤 Users Only",
        "groups": "👥 Groups Only", 
        "both": "🌐 Both Users and Groups"
    }.get(broadcast_location, "Unknown")

    await query.edit_message_text(
        f"<b>🗳️ Confirm Vote Broadcast to {location_text}:</b>\n\n"
        f"{message_to_broadcast}\n\n"
        f"<b>Vote Options:</b> {vote_text}",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


@is_owner
async def custom_options_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the custom options count selection.
    """
    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    broadcast_location = context.chat_data.get('broadcast_location')
    broadcast_type = context.chat_data.get('broadcast_type')
    if not all([message_to_broadcast, broadcast_location, broadcast_type]):
        await query.edit_message_text("❌ Error: Broadcast data not found. Please try again.")
        return

    callback_data = query.data
    if callback_data == "custom_cancel":
        context.chat_data.pop('broadcast_message', None)
        context.chat_data.pop('broadcast_type', None)
        context.chat_data.pop('broadcast_location', None)
        await query.edit_message_text("❌ Custom options setup cancelled.")
        return

    # Parse count from callback
    try:
        count = int(callback_data.split('_')[2])  # custom_count_2, custom_count_3, etc.
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Error: Invalid option count selection.")
        return

    if not 2 <= count <= 5:
        await query.edit_message_text("❌ Error: Invalid option count. Must be between 2-5.")
        return

    # Store count and initialize options collection
    context.chat_data['custom_options_count'] = count
    context.chat_data['custom_options_collected'] = []
    context.chat_data['custom_current_option'] = 1

    # Ask for first option
    await query.edit_message_text(
        f"📝 <b>Custom Options Setup</b>\n\n"
        f"Please reply with <b>Option 1</b> of {count}:\n\n"
        f"<i>Example: Yes, I agree</i>",
        parse_mode=ParseMode.HTML
    )


@is_owner
async def collect_custom_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles collecting individual custom options via text messages.
    """
    if not update.effective_user or not update.message or not update.message.text:
        return

    # Check if we're in custom options collection mode
    if 'custom_current_option' not in context.chat_data:
        return

    current_option = context.chat_data.get('custom_current_option', 0)
    total_count = context.chat_data.get('custom_options_count', 0)
    collected_options = context.chat_data.get('custom_options_collected', [])

    if current_option > total_count:
        return  # Collection complete

    # Validate option text
    option_text = update.message.text.strip()
    if not option_text:
        await update.message.reply_text("❌ Option cannot be empty. Please try again.")
        return

    if len(option_text) > 50:
        await update.message.reply_text("❌ Option too long (max 50 characters). Please try again.")
        return

    # Add emoji if not present
    if not any(char in option_text for char in ['👍', '👎', '✅', '❌', '🤔', '❤️', '🔥', '⭐']):
        option_text = f"📌 {option_text}"

    # Store the option
    collected_options.append(option_text)
    context.chat_data['custom_options_collected'] = collected_options
    context.chat_data['custom_current_option'] = current_option + 1

    if current_option < total_count:
        # Ask for next option
        await update.message.reply_text(
            f"✅ <b>Option {current_option} saved:</b> {option_text}\n\n"
            f"Please reply with <b>Option {current_option + 1}</b> of {total_count}:",
            parse_mode=ParseMode.HTML
        )
    else:
        # All options collected, show confirmation
        context.chat_data['vote_options'] = collected_options
        
        message_to_broadcast = context.chat_data.get('broadcast_message')
        broadcast_location = context.chat_data.get('broadcast_location')
        
        location_text = {
            "users": "👤 Users Only",
            "groups": "👥 Groups Only", 
            "both": "🌐 Both Users and Groups"
        }[broadcast_location]

        keyboard = [[InlineKeyboardButton("✅ Confirm Custom Vote Broadcast", callback_data="confirm_broadcast")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        options_list = "\n".join(f"• {opt}" for opt in collected_options)
        
        await update.message.reply_text(
            f"🎉 <b>All Custom Options Collected!</b>\n\n"
            f"<b>Vote Options ({len(collected_options)}):</b>\n{options_list}\n\n"
            f"<b>Location:</b> {location_text}\n"
            f"<b>Message:</b> {message_to_broadcast}\n\n"
            f"Ready to broadcast?",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

        # Clean up temporary data
        context.chat_data.pop('custom_options_count', None)
        context.chat_data.pop('custom_current_option', None)
        context.chat_data.pop('custom_options_collected', None)
    query = update.callback_query
    if not query or not context.chat_data:
        return

    await query.answer()

    message_to_broadcast = context.chat_data.get('broadcast_message')
    broadcast_location = context.chat_data.get('broadcast_location')
    broadcast_type = context.chat_data.get('broadcast_type')
    if not all([message_to_broadcast, broadcast_location, broadcast_type]):
        await query.edit_message_text("❌ Error: Broadcast data not found. Please try again.")
        return

    callback_data = query.data
    if callback_data == "broadcast_cancel":
        # Clear stored data and cancel
        context.chat_data.pop('broadcast_message', None)
        context.chat_data.pop('broadcast_type', None)
        context.chat_data.pop('broadcast_location', None)
        await query.edit_message_text("❌ Broadcast cancelled.")
        return

    if callback_data == "vote_options_yesno":
        # Store vote options
        context.chat_data['vote_options'] = ["✅ Yes", "❌ No"]
        vote_text = "Yes/No"
    elif callback_data == "vote_options_custom":
        # Start custom options collection
        keyboard = [
            [InlineKeyboardButton("2 Options", callback_data="custom_count_2"),
             InlineKeyboardButton("3 Options", callback_data="custom_count_3")],
            [InlineKeyboardButton("4 Options", callback_data="custom_count_4"),
             InlineKeyboardButton("5 Options", callback_data="custom_count_5")],
            [InlineKeyboardButton("❌ Cancel", callback_data="custom_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"<b>📊 Custom Vote Options Setup</b>\n\n"
            f"{message_to_broadcast}\n\n"
            f"<b>How many voting options do you want?</b>\n"
            f"Choose 2-5 options for your custom poll:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return  # Don't proceed to confirmation yet
    else:
        await query.edit_message_text("❌ Error: Invalid vote options selection.")
        return

    # Show confirmation for vote broadcast
    keyboard = [[InlineKeyboardButton("✅ Confirm Vote Broadcast", callback_data="confirm_broadcast")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    location_text = {
        "users": "👤 Users Only",
        "groups": "👥 Groups Only", 
        "both": "🌐 Both Users and Groups"
    }.get(broadcast_location, "Unknown")

    await query.edit_message_text(
        f"<b>🗳️ Confirm Vote Broadcast to {location_text}:</b>\n\n"
        f"{message_to_broadcast}\n\n"
        f"<b>Vote Options:</b> {vote_text}",
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
    user_names = {}
    
    # Get targets based on location
    if broadcast_location in ["users", "both"]:
        try:
            all_users = await db.get_all_players()
            for user in all_users:
                user_id = user.user_id
                targets.append(("user", user_id))
                user_names[user_id] = user.name
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
    sent_users = []

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
        user_name = user_names.get(target_id, f"User {target_id}") if target_type == "user" else f"Group {target_id}"
        
        try:
            await context.bot.send_message(chat_id=target_id, text=message, parse_mode=ParseMode.HTML)
            success_count += 1
            sent_users.append(f"✅ {user_name}")
        except Forbidden:
            # User/group has blocked the bot
            failure_count += 1
            sent_users.append(f"❌ {user_name} (blocked)")
            logger.warning(f"Broadcast failed for {target_type} {target_id}: Bot was blocked.")
        except BadRequest:
            # Chat not found or other issue
            failure_count += 1
            sent_users.append(f"❌ {user_name} (not found)")
            logger.warning(f"Broadcast failed for {target_type} {target_id}: Chat not found or bad request.")
        except Exception as e:
            failure_count += 1
            sent_users.append(f"❌ {user_name} (error)")
            logger.error(f"Broadcast failed for {target_type} {target_id}: {e}")

        # Update progress with recent users
        if (i + 1) % 5 == 0 or i == total_count - 1:
            recent_users = sent_users[-10:]  # Show last 10 users
            progress_text = f"📡 <b>Broadcasting in Progress</b>\nCompleted: {i+1}/{total_count}\n\n"
            progress_text += "<b>Recent Status:</b>\n" + "\n".join(recent_users[-5:])  # Show last 5
            
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=message_id,
                    text=progress_text,
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


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles vote button presses from users.
    """
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except BadRequest:
        # Query is too old, can't respond
        return

    current_vote = context.bot_data.get('current_vote')
    if not current_vote:
        # Can't answer old queries, just return
        return

    callback_data = query.data
    if not callback_data or not callback_data.startswith("vote_"):
        return

    vote_index = int(callback_data.split("_")[1])
    if vote_index >= len(current_vote['options']):
        await query.answer("Invalid vote option.")
        return

    # Record the vote
    user = query.from_user
    user_name = user.first_name or user.username or f"User {user.id}"
    
    # Initialize user votes if not exists
    if 'user_votes' not in current_vote:
        current_vote['user_votes'] = {}
    
    # Check if user already voted
    if user.id in current_vote['user_votes']:
        old_vote = current_vote['user_votes'][user.id]
        current_vote['counts'][f"vote_{old_vote}"] -= 1
    
    # Record new vote
    current_vote['user_votes'][user.id] = vote_index
    current_vote['counts'][callback_data] += 1

    # Update the admin message with live vote counts
    admin_chat_id = current_vote['admin_chat_id']
    message_id = current_vote['message_id']
    
    vote_results = []
    total_votes = sum(current_vote['counts'].values())
    
    for i, option in enumerate(current_vote['options']):
        count = current_vote['counts'][f"vote_{i}"]
        percentage = (count / total_votes * 100) if total_votes > 0 else 0
        vote_results.append(f"{option}: {count} ({percentage:.1f}%)")
    
    live_update = (
        f"🗳️ <b>Vote Broadcast Active!</b>\n\n"
        f"<b>Message:</b> {current_vote['message']}\n\n"
        f"📊 <b>Live Vote Results:</b>\n"
        f"{' | '.join(vote_results)}\n\n"
        f"🗳️ Total Votes: {total_votes}\n\n"
        f"<i>Last vote by: {user_name}</i>"
    )
    
    try:
        await context.bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=message_id,
            text=live_update,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not update live vote results: {e}")

    # Confirm vote to user
    await query.answer(f"You voted for: {current_vote['options'][vote_index]}")

    # Edit the user's message to show thank you
    try:
        await query.edit_message_text(
            text="✅ <b>Thanks for Your Feedback!</b>\n\nYour vote has been recorded.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not edit vote message for user {user.id}: {e}")


async def _send_vote_broadcast(admin_chat_id: int, message: str, broadcast_location: str, vote_options: list, db: Database, context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
    """
    Fetches users/groups and sends them the vote broadcast message with inline keyboard.
    Runs as a non-blocking background task.
    """
    if not db:
        logger.error("Vote broadcast failed: Database not available.")
        await context.bot.send_message(admin_chat_id, "❌ Vote broadcast failed: Database connection is missing.")
        return

    targets = []
    user_names = {}
    
    # Get targets based on location
    if broadcast_location in ["users", "both"]:
        try:
            all_users = await db.get_all_players()
            for user in all_users:
                user_id = user.user_id
                targets.append(("user", user_id))
                user_names[user_id] = user.name
        except Exception as e:
            logger.error(f"Vote broadcast failed: Could not fetch users from DB. Error: {e}")
            await context.bot.send_message(admin_chat_id, "❌ Vote broadcast failed: Could not fetch users from the database.")
            return
    
    if broadcast_location in ["groups", "both"]:
        try:
            all_groups = await db.get_all_groups()
            group_ids = [group.get("group_id") for group in all_groups if group.get("group_id")]
            targets.extend([("group", group_id) for group_id in group_ids])
        except Exception as e:
            logger.error(f"Vote broadcast failed: Could not fetch groups from DB. Error: {e}")
            await context.bot.send_message(admin_chat_id, "❌ Vote broadcast failed: Could not fetch groups from the database.")
            return

    if not targets:
        await context.bot.send_message(admin_chat_id, "❌ No targets found for vote broadcast.")
        return

    success_count = 0
    failure_count = 0
    total_count = len(targets)
    sent_users = []

    # Create vote keyboard
    keyboard = []
    for i, option in enumerate(vote_options):
        keyboard.append([InlineKeyboardButton(option, callback_data=f"vote_{i}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Initialize vote tracking
    vote_counts = {f"vote_{i}": 0 for i in range(len(vote_options))}
    context.bot_data['current_vote'] = {
        'message': message,
        'options': vote_options,
        'counts': vote_counts,
        'admin_chat_id': admin_chat_id,
        'message_id': message_id
    }

    # Update message with initial progress
    try:
        await context.bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=message_id,
            text=f"🗳️ <b>Vote Broadcasting in Progress</b>\nCompleted: 0/{total_count}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not update initial progress message: {e}")

    for i, (target_type, target_id) in enumerate(targets):
        user_name = user_names.get(target_id, f"User {target_id}") if target_type == "user" else f"Group {target_id}"
        
        try:
            await context.bot.send_message(
                chat_id=target_id, 
                text=message, 
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            success_count += 1
            sent_users.append(f"✅ {user_name}")
        except Forbidden:
            # User/group has blocked the bot
            failure_count += 1
            sent_users.append(f"❌ {user_name} (blocked)")
            logger.warning(f"Vote broadcast failed for {target_type} {target_id}: Bot was blocked.")
        except BadRequest:
            # Chat not found or other issue
            failure_count += 1
            sent_users.append(f"❌ {user_name} (not found)")
            logger.warning(f"Vote broadcast failed for {target_type} {target_id}: Chat not found or bad request.")
        except Exception as e:
            failure_count += 1
            sent_users.append(f"❌ {user_name} (error)")
            logger.error(f"Vote broadcast failed for {target_type} {target_id}: {e}")

        # Update progress with recent users
        if (i + 1) % 5 == 0 or i == total_count - 1:
            recent_users = sent_users[-10:]  # Show last 10 users
            progress_text = f"🗳️ <b>Vote Broadcasting in Progress</b>\nCompleted: {i+1}/{total_count}\n\n"
            progress_text += "<b>Recent Status:</b>\n" + "\n".join(recent_users[-5:])  # Show last 5
            
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=message_id,
                    text=progress_text,
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
        f"✅ <b>Vote Broadcast Complete!</b>\n\n"
        f"<b>Location:</b> {location_text}\n"
        f"<b>Message:</b> {message}\n"
        f"<b>Vote Options:</b> {', '.join(vote_options)}\n\n"
        f"📊 <b>Results:</b>\n"
        f"✅ Sent successfully: {success_count}\n"
        f"❌ Failed to send: {failure_count}\n"
        f"📈 Total targets: {total_count}\n\n"
        f"<b>Live Vote Tracking Active!</b>"
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
