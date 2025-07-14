from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected

MAP_IMAGE_URL = "https://i.ibb.co/ccGtnVsT/IMG-20250704-133117-724.jpg"


@maintenance_protected
@ban_protected
async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data.get("db")
    user_id = str(update.effective_user.id)
    player = await db.get_player(user_id)
    travel = getattr(player, "travel", {})
    location = getattr(player, "location", "Unknown")

    if travel.get("in_progress"):
        travel_status = (
            f"You are currently travelling towards *{travel['direction']}* "
            f"({travel['from']} → {travel['to']}) [{travel['progress']}/{travel['required']} explores]"
        )
    else:
        travel_status = f"You are currently at: *{location}*"

    caption = (
        "*Attack on Titan World Map*\n"
        + travel_status
    )

    try:
        await update.message.reply_photo(
            MAP_IMAGE_URL,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(
            f"{caption}\n\n_Image could not be sent._",
            parse_mode=ParseMode.MARKDOWN
        )
