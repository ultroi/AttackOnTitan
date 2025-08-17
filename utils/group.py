from telegram import ChatMemberUpdated, Chat, ChatMember, ChatInviteLink
from telegram.constants import ChatType
from telegram.error import TelegramError

# --- Group Add/Remove Handler ---
async def group_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_channel_id = LOG_CHANNEL_ID
    event = update.my_chat_member or update.chat_member
    if not event:
        return
    chat = event.chat
    user = event.from_user
    old_status = event.old_chat_member.status if hasattr(event, 'old_chat_member') else None
    new_status = event.new_chat_member.status if hasattr(event, 'new_chat_member') else None
    # Only care about groups/supergroups
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return
    # Only care if bot is added/removed/promoted/demoted
    bot_id = (await context.bot.get_me()).id
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
    try:
        await context.bot.send_message(
            chat_id=log_channel_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )
    except Exception as e:
        logger.error(f"Failed to send group update log: {e}")

# --- Register handler (add to your dispatcher setup code) ---
# from telegram.ext import ChatMemberHandler

