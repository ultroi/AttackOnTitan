import logging
import asyncio
from telegram import Update
from utils.ban_utils import ban_protected
from game.explore import _reply_error
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.maintenance import maintenance_protected

logger = logging.getLogger(__name__)

@ban_protected
@maintenance_protected
async def buy_command(update: Update, context):
                try:
                    if not update.effective_user or not update.message:
                        if update.message:
                            await update.message.reply_text("User or message information not available.")
                        return
                    args = context.args
                    if len(args) != 2:
                        # Show available exchange options
                        help_text = (
                            "Usage: /buy <currency_type> <amount>\n\n"
                            "Available exchange options:\n"
                            "• /buy gas <amount> - Buy gas with marks (4:1)\n"
                            "• /buy valor <amount> - Buy valor with marks (10,000:1)\n"
                            "• /buy crystal <amount> - Buy crystal with valor (200:1)"
                        )
                        await update.message.reply_text(help_text)
                        return
                    
                    currency_type = args[0].lower()
                    try:
                        amount = int(args[1])
                    except ValueError:
                        await update.message.reply_text("Amount must be an integer.")
                        return
                    
                    # Validate currency type
                    valid_types = ["gas", "valor", "crystal"]
                    if currency_type not in valid_types:
                        await update.message.reply_text(
                            f"Invalid currency type. Available options: {', '.join(valid_types)}"
                        )
                        return
                        
                    shop_system = context.bot_data["shop_system"]
                    user_id = str(update.effective_user.id)
                    result = await shop_system.buy_currency(context, user_id, currency_type, amount)
                    await update.message.reply_text(result)
                except Exception as e:
                    logger.error(f"Error in buy_command: {e}")
                    if update.message:
                        await update.message.reply_text(f"Error processing buy command: {str(e)}")



@maintenance_protected
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
    target_first_name = getattr(target_user, 'first_name', 'Unknown')
    target_first_name_clickable = f'<a href="tg://user?id={target_id}">{getattr(target_user, "first_name", "Unknown")}</a>'

    # Block giving to bots
    if getattr(target_user, 'is_bot', False):
        await update.message.reply_text("You cannot give resources to a bot.")
        return
    # Block giving to yourself
    if user_id == target_id:
        await update.message.reply_text("You cannot give resources to yourself.")
        return

    # Check for active battle
    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None

    if active_battles_lock:
        async with active_battles_lock:
            if user_id_str in active_battles or target_id_str in active_battles:
                who = first_name if user_id_str in active_battles else target_first_name
                await update.message.reply_text(
                    f"<a href=\"tg://user?id={user_id if user_id_str in active_battles else target_id}\">{who}</a> is currently battling !!", parse_mode=ParseMode.HTML)
                return
    else:
        if user_id_str in active_battles or target_id_str in active_battles:
            who = first_name if user_id_str in active_battles else target_first_name
            await update.message.reply_text(
                f"<a href=\"tg://user?id={user_id if user_id_str in active_battles else target_id}\">{who}</a> is currently battling !!", parse_mode=ParseMode.HTML)
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

    # Fetch sender and receiver in parallel
    sender_task = asyncio.create_task(db.get_player(user_id_str))
    receiver_task = asyncio.create_task(db.get_player(target_id_str)) if target_id_str else None
    sender = await sender_task
    receiver = await receiver_task if receiver_task else None
    if not sender or not receiver:
        await update.message.reply_text("Both users must have profiles.")
        return
    # Level check: Only allow giving if sender is at least level 10
    if getattr(sender, 'level', 1) < 10:
        await update.message.reply_text("You must be at least level 10 to give resources to others.")
        return
    if getattr(sender, item, 0) < amount:
        await update.message.reply_text(f"You don't have enough {item}.")
        return
    # Update both balances in parallel
    update_sender = asyncio.create_task(db.update_player(user_id_str, {item: getattr(sender, item, 0) - amount}))
    update_receiver = asyncio.create_task(db.update_player(target_id_str, {item: getattr(receiver, item, 0) + amount}))
    await asyncio.gather(update_sender, update_receiver)

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
    await update.message.reply_text(f"Successfully gave {amount} {item} to {target_first_name_clickable}.", parse_mode=ParseMode.HTML)
    await context.bot.send_message(GIVE_LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)


@maintenance_protected
@ban_protected
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /broadcast command to send messages to all users and groups."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Check if user is owner (you can modify this check based on your owner system)
    # For now, let's assume owners are hardcoded or checked via database
    try:
        # Get database
        db = context.bot_data.get("db")
        if not db:
            await update.message.reply_text("Database unavailable.")
            return

        # Check if user is owner (you might have an owners list or check user level/role)
        # For now, let's check if user_id is in a predefined list or has high level
        from utils.owners import get_owner_ids
        owner_ids = get_owner_ids()

        # Alternative: check if user has high level or specific role
        player = await db.get_player(str(user_id))
        is_owner = (user_id in owner_ids or
                   (player and getattr(player, 'level', 0) >= 100))  # High level users

        if not is_owner:
            await update.message.reply_text("❌ You don't have permission to use broadcast.")
            return

        # Check if command has arguments or is a reply
        if not context.args and not (update.message.reply_to_message):
            help_text = (
                "📢 <b>Broadcast Command</b>\n\n"
                "Usage:\n"
                "• Reply to a message: /broadcast\n"
                "• With text: /broadcast &lt;message&gt;\n\n"
                "This will forward the message to all users and groups where the bot is present."
            )
            await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
            return

        # Get the message to broadcast
        broadcast_message = None
        if update.message.reply_to_message:
            # Use the replied message
            broadcast_message = update.message.reply_to_message
        elif context.args:
            # Create a new message with the text
            broadcast_text = " ".join(context.args)
            # Send the text as a message from the bot
            broadcast_message = await context.bot.send_message(
                chat_id=user_id,
                text=broadcast_text,
                parse_mode=ParseMode.HTML
            )

        if not broadcast_message:
            await update.message.reply_text("❌ No message to broadcast.")
            return

        # Run broadcast in background to avoid blocking other commands
        import asyncio
        asyncio.create_task(run_broadcast(context, broadcast_message, user_id, update))

    except Exception as e:
        logger.error(f"Error in broadcast_command: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def run_broadcast(context, broadcast_message, user_id, update):
    """Run the actual broadcast process in background with batching"""
    try:
        db = context.bot_data.get("db")
        if not db:
            await update.message.reply_text("Database unavailable.")
            return

        # Get all players from database
        all_players_cursor = db.players.find({})
        user_ids = []
        async for player_doc in all_players_cursor:
            if 'user_id' in player_doc:
                user_ids.append(str(player_doc['user_id']))

        # Get all groups from database
        all_groups_cursor = db.groups.find({})
        group_ids = []
        async for group_doc in all_groups_cursor:
            if '_id' in group_doc:
                group_ids.append(str(group_doc['_id']))

        # Remove duplicates to prevent spam
        user_ids = list(set(user_ids))
        group_ids = list(set(group_ids))

        total_users = len(user_ids)
        total_groups = len(group_ids)
        total_targets = total_users + total_groups

        logger.info(f"Broadcasting to {total_users} users and {total_groups} groups")

        # Send immediate confirmation
        await update.message.reply_text(
            f"📢 <b>Broadcast Started!</b>\n\n"
            f"🎯 <b>Targets:</b> {total_users} users, {total_groups} groups\n"
            f"⏱️ <b>Status:</b> Processing in background...\n\n"
            f"<i>You can continue using other commands.</i>",
            parse_mode=ParseMode.HTML
        )

        # Process in smaller batches to avoid overwhelming the bot
        batch_size = 10  # Process 10 at a time
        users_broadcasted = 0
        groups_broadcasted = 0
        errors = 0

        # Process users in batches
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            for uid in batch:
                try:
                    await context.bot.forward_message(
                        chat_id=uid,
                        from_chat_id=broadcast_message.chat_id,
                        message_id=broadcast_message.message_id
                    )
                    users_broadcasted += 1
                except Exception as e:
                    errors += 1
                    logger.warning(f"Failed to broadcast to user {uid}: {e}")

            # Longer delay between batches
            await asyncio.sleep(1.0)  # 1 second between batches

        # Process groups in batches
        for i in range(0, len(group_ids), batch_size):
            batch = group_ids[i:i + batch_size]
            for gid in batch:
                try:
                    await context.bot.forward_message(
                        chat_id=gid,
                        from_chat_id=broadcast_message.chat_id,
                        message_id=broadcast_message.message_id
                    )
                    groups_broadcasted += 1
                except Exception as e:
                    errors += 1
                    logger.warning(f"Failed to broadcast to group {gid}: {e}")

            # Longer delay between batches
            await asyncio.sleep(1.0)  # 1 second between batches

        # Send completion report
        report = (
            f"📢 <b>Broadcast Complete!</b>\n\n"
            f"✅ <b>Users:</b> {users_broadcasted}/{total_users}\n"
            f"✅ <b>Groups:</b> {groups_broadcasted}/{total_groups}\n"
            f"❌ <b>Errors:</b> {errors}\n"
            f"📊 <b>Total:</b> {users_broadcasted + groups_broadcasted}/{total_targets}"
        )

        await update.message.reply_text(report, parse_mode=ParseMode.HTML)

        # Log the broadcast
        log_msg = (
            f"📢 <b>Broadcast Completed</b>\n\n"
            f"<b>By:</b> <code>{user_id}</code>\n"
            f"<b>Users:</b> {users_broadcasted}/{total_users}\n"
            f"<b>Groups:</b> {groups_broadcasted}/{total_groups}\n"
            f"<b>Errors:</b> {errors}"
        )

        # Send to log channel if available
        try:
            await context.bot.send_message(
                chat_id=-1002873117075, 
                text=log_msg,
                parse_mode=ParseMode.HTML
            )
        except:
            pass

    except Exception as e:
        logger.error(f"Broadcast failed: {e}")
        await update.message.reply_text(f"❌ Broadcast failed: {str(e)}")


