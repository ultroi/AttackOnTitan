from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from .travel_map import TRAVEL_MAP
from game.map_system import MAP_IMAGE_URL
from utils.ban_utils import ban_protected
from game.explore import _reply_error

import logging
logger = logging.getLogger(__name__)

@ban_protected
async def travel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
     # Only allow in private chat
    if update.effective_chat and update.effective_chat.type != "private":
        await _reply_error(update, "This command can only be used in private chat.")
        return
    db = context.bot_data.get("db")
    user_id = str(update.effective_user.id)
    player = await db.get_player(user_id)
    travel = getattr(player, "travel", {})
    location = getattr(player, "location", "Unknown")
    # If already traveling, show progress and block new travel
    if travel.get("in_progress"):
        msg = (
            f"You are currently travelling towards <b>{travel['direction']}</b> "
            f"({travel['from']} → {travel['to']}) [{travel['progress']}/{travel['required']} explores]"
        )
        keyboard = [[InlineKeyboardButton("❌ Cancel Travel", callback_data="cancel_travel")]]
        try:
            await update.message.reply_photo(
                MAP_IMAGE_URL,
                caption=msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return
    # Show available directions from current location
    if location not in TRAVEL_MAP:
        await update.message.reply_text(f"No travel options from {location}.")
        return
    directions = TRAVEL_MAP[location]
    keyboard = [
        [InlineKeyboardButton(dir, callback_data=f"travel_{dir}")] for dir in directions.keys()
    ]
    caption = (
        f"<b>Current Location:</b> {location}\nChoose a direction to travel:"
    )
    try:
        await update.message.reply_photo(
            MAP_IMAGE_URL,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await update.message.reply_text(
            caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def handle_travel_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"[HANDLER] handle_travel_direction got callback: {query.data}")
    await query.answer()
    db = context.bot_data.get("db")
    user_id = str(query.from_user.id)
    player = await db.get_player(user_id)
    location = getattr(player, "location", "Unknown")
    if location not in TRAVEL_MAP:
        if query.message and query.message.photo:
            await query.edit_message_caption(f"No travel options from {location}.")
        else:
            await query.edit_message_text(f"No travel options from {location}.")
        return
    directions = TRAVEL_MAP[location]
    dir_selected = query.data.replace("travel_", "")
    if dir_selected not in directions:
        if query.message and query.message.photo:
            await query.edit_message_caption("Invalid direction.")
        else:
            await query.edit_message_text("Invalid direction.")
        return
    to, required = directions[dir_selected]
    travel_state = {
        "in_progress": True,
        "direction": dir_selected,
        "from": location,
        "to": to,
        "progress": 0,
        "required": required
    }
    await db.update_player(user_id, {"travel": travel_state})
    msg = f"You started travelling towards <b>{dir_selected}</b> ({location} → {to}) [0/{required} explores]"
    if query.message and query.message.photo:
        await query.edit_message_caption(msg, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)

async def handle_cancel_travel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"[HANDLER] handle_cancel_travel got callback: {query.data}")
    await query.answer()
    db = context.bot_data.get("db")
    user_id = str(query.from_user.id)
    player = await db.get_player(user_id)
    travel = getattr(player, "travel", {})
    
    if not travel.get("in_progress"):
        msg = "You are not currently traveling."
        try:
            if query.message and query.message.photo:
                await query.edit_message_caption(caption=msg, parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
        return
    
    # Cancel travel: clear travel state, keep location as where travel started
    await db.update_player(user_id, {"travel": {}})
    msg = f"Travel cancelled. You remain at <b>{travel.get('from', player.location)}</b>."
    
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=msg, parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.message.chat_id, text=msg, parse_mode=ParseMode.HTML)