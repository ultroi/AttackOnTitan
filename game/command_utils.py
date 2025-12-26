from typing import Optional, Tuple, Any
from telegram import Update
from telegram.ext import ContextTypes
from database.db_instance import get_persistent_database
import logging

logger = logging.getLogger(__name__)

async def ensure_db(context: ContextTypes.DEFAULT_TYPE):
    """Return an initialized database instance. If missing in bot_data, create and init it."""
    db = context.bot_data.get("db")
    if db is None:
        logger.debug("No db in bot_data - creating persistent DB instance")
        db = get_persistent_database()
        await db.init_db()
        context.bot_data["db"] = db
    return db

async def parse_target_user(update: Update, args: Tuple[str, ...], default_to_sender: bool = True) -> Optional[int]:
    """Common logic to parse a target user id from command args or replied message.

    Returns integer user id or None if not resolved.
    """
    message = update.message or update.effective_message
    # prefer replied message's author
    if message.reply_to_message and getattr(message.reply_to_message, "from_user", None):
        return message.reply_to_message.from_user.id

    # look at args for an id or username
    if args:
        first = args[0]
        # try integer id
        try:
            return int(first)
        except Exception:
            # username unsupported here; future enhancement: resolve username
            return None

    if default_to_sender and message and message.from_user:
        return message.from_user.id

    return None

async def fetch_player(db, user_id: Any, raise_on_missing: bool = False):
    user_id_str = str(user_id)
    player = await db.get_player(user_id_str)
    if not player and raise_on_missing:
        raise ValueError(f"Player with id {user_id_str} not found")
    return player

async def fetch_character(db, user_id: Any, name: str, raise_on_missing: bool = False):
    char = await db.get_character(str(user_id), name)
    if not char and raise_on_missing:
        raise ValueError(f"Character '{name}' for user {user_id} not found")
    return char

async def send_reply(update: Update, text: str, **kwargs):
    message = update.message or update.effective_message
    await message.reply_text(text, **kwargs)

async def send_log(context: ContextTypes.DEFAULT_TYPE, channel: int, text: str, **kwargs):
    await context.bot.send_message(chat_id=channel, text=text, **kwargs)
