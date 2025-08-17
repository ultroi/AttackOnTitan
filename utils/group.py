from telegram import ChatMemberUpdated, Chat, ChatMember, ChatInviteLink, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from html import escape
import logging
from datetime import datetime, timezone
from database.db import Database

# Constants
LOG_CHANNEL_ID = -1002873117075
logger = logging.getLogger(__name__)

# --- Group Add/Remove Handler ---
async def group_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_channel_id = LOG_CHANNEL_ID
    
    # Handle both chat_member updates and regular message updates with member changes
    if update.my_chat_member or update.chat_member:
        event = update.my_chat_member or update.chat_member
        logger.info(f"Group update event received (chat_member): {event}")
        
        chat = event.chat
        user = event.from_user
        old_status = event.old_chat_member.status if hasattr(event, 'old_chat_member') else None
        new_status = event.new_chat_member.status if hasattr(event, 'new_chat_member') else None
    elif update.message and (update.message.new_chat_members or update.message.left_chat_member):
        # Handle through regular message updates
        logger.info(f"Group update event received (message): {update.message}")
        
        chat = update.message.chat
        user = update.message.from_user
        
        # Check if bot was added
        if update.message.new_chat_members:
            bot_id = (await context.bot.get_me()).id
            for member in update.message.new_chat_members:
                if member.id == bot_id:
                    old_status = None
                    new_status = ChatMember.MEMBER
                    break
            else:
                # No bot in new members
                logger.info(f"Bot not in new chat members, skipping")
                return
                
        # Check if bot was removed
        elif update.message.left_chat_member:
            bot_id = (await context.bot.get_me()).id
            if update.message.left_chat_member.id == bot_id:
                old_status = ChatMember.MEMBER
                new_status = None
            else:
                # Not about our bot
                logger.info(f"Left chat member is not our bot, skipping")
                return
        else:
            return
    else:
        logger.info("No event data in group update handler, skipping")
        return
    
    # Detailed logging
    logger.info(f"Group update: chat_id={chat.id}, type={chat.type}, old_status={old_status}, new_status={new_status}")
    
    # Only care about groups/supergroups
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        logger.info(f"Skipping update for non-group chat type: {chat.type}")
        return
    
    # Check if the update is from a message with new_chat_members or left_chat_member
    if update.message and update.message.new_chat_members:
        # Bot is being added to the group
        action = "added"
        was_member = False
        is_member = True
    elif update.message and update.message.left_chat_member:
        # Bot is being removed from the group
        action = "removed"
        was_member = True
        is_member = False
    else:
        # Traditional chat_member update handling
        was_member = old_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        is_member = new_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        
        # If bot is added or removed
        if not was_member and is_member:
            action = "added"
        elif was_member and not is_member:
            action = "removed"
        elif was_member and is_member and old_status != new_status:
            action = f"status changed: {old_status} → {new_status}"
        else:
            return
    # Get group info
    group_title = chat.title or "(no title)"
    group_id = chat.id
    group_type = chat.type
    # Get group link (public username or invite link if admin)
    group_link = None
    if chat.username:
        group_link = f"https://t.me/{chat.username}"
    elif is_member and new_status == ChatMember.ADMINISTRATOR:
        try:
            # Try to get an invite link if bot is admin
            invite_links = await context.bot.get_chat(chat.id).invite_link
            if invite_links:
                group_link = invite_links
        except Exception:
            pass
    # Get member count
    try:
        member_count = await context.bot.get_chat_member_count(chat.id)
    except Exception:
        member_count = "?"
    # Get admin count
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_count = len(admins)
        admin_list = ", ".join([f"<a href='tg://user?id={a.user.id}'>{escape(a.user.full_name)}</a>" for a in admins])
    except Exception:
        admin_count = "?"
        admin_list = "?"
    # Who did the action
    user_link = f"<a href='tg://user?id={user.id}'>{escape(user.full_name)}</a>" if user else "?"
    # Compose message
    msg = (
        f"<b>Bot {action} in group</b>\n"
        f"<b>Group:</b> {escape(group_title)}\n"
        f"<b>ID:</b> <code>{group_id}</code>\n"
        f"<b>Type:</b> {group_type}\n"
        f"<b>By:</b> {user_link}\n"
        f"<b>Members:</b> {member_count}\n"
        f"<b>Admins ({admin_count}):</b> {admin_list}\n"
        f"<b>Group Link:</b> {group_link or 'N/A'}\n"
        f"<b>Status:</b> {old_status} → {new_status}"
    )
    # Store or update group in database
    try:
        db = context.bot_data.get("db")
        if not db:
            # If no database in context, create and initialize one
            from database.db_instance import get_persistent_database
            db = Database()
            await db.init_db()  # Make sure to initialize the database
            logger.info("Created and initialized new database instance for group update")
        
        # Prepare group data
        group_data = {
            "group_id": group_id,
            "title": group_title,
            "type": group_type,
            "link": group_link,
            "username": chat.username,
            "member_count": member_count if isinstance(member_count, int) else 0,
            "admin_count": admin_count if isinstance(admin_count, int) else 0,
            "is_bot_member": is_member,
            "bot_status": new_status,
            "updated_at": datetime.now(timezone.utc),
            "added_by": user.id if user else None,
            "added_by_name": user.full_name if user else None,
        }
        
        # If bot is added, set added_at
        if action == "added":
            group_data["added_at"] = datetime.now(timezone.utc)
        # If bot is removed, set removed_at
        elif action == "removed":
            group_data["removed_at"] = datetime.now(timezone.utc)
            
        # Check if groups collection exists
        if not hasattr(db, 'groups') or db.groups is None:
            logger.warning("Groups collection not initialized, reinitializing database")
            await db.init_db()  # Make sure to initialize the database again
            
        # Double-check groups collection after initialization
        if db.groups is None:
            logger.error("Groups collection still None after reinitialization, cannot update group")
            return
            
        # Use the database function to update the group
        if await db.update_group(group_id, group_data):
            logger.info(f"Group {action} in DB: {group_id} - {group_title}")
        else:
            logger.warning(f"Failed to update group in database, possibly due to connection issue: {group_id} - {group_title}")
    except Exception as db_err:
        logger.error(f"Failed to update group in database: {db_err}")
    
    # Send message to log channel
    try:
        await context.bot.send_message(
            chat_id=log_channel_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )
    except Exception as e:
        logger.error(f"Failed to send group update log: {e}")

