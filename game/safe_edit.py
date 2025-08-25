import logging
from telegram import Message
from telegram.error import BadRequest
import re
import time
import asyncio
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Global throttling mechanism to prevent edit spam
_last_edit_time: Dict[str, float] = {}
_edit_counters: Dict[str, Dict[str, Any]] = {}
_edit_locks: Dict[str, asyncio.Lock] = {}

def clean_html_entities(text: str) -> str:
    if not text:
        return text

    # Allowed tags in Telegram HTML
    allowed_tags = {"b", "strong", "i", "em", "u", "ins", "s", "strike",
                    "del", "code", "pre", "a"}

    # 1. Remove unsupported tags
    text = re.sub(
        r"</?([a-zA-Z0-9]+)(\s[^>]*)?>",
        lambda m: m.group(0) if m.group(1).lower() in allowed_tags else "",
        text
    )

    # 2. Remove zero-width spaces inside tags
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)

    # 3. Balance some tags
    for tag in ["code", "b", "i", "u", "s", "pre"]:
        opens = len(re.findall(fr"<{tag}[^>]*>", text))
        closes = len(re.findall(fr"</{tag}>", text))
        if opens > closes:
            text += f"</{tag}>" * (opens - closes)
        elif closes > opens:
            text = re.sub(fr"</{tag}>", "", text, count=(closes - opens))

    return text

async def safe_edit_message_text(message: Message, text: str, reply_markup=None, parse_mode=None):
    global _last_edit_time, _edit_counters, _edit_locks

    try:
        if not message:
            logger.warning("Cannot edit: message is None")
            return False

        message_id = message.message_id
        chat_id = message.chat_id
        message_key = f"{chat_id}_{message_id}"

        current_time = time.time()

        if message_key not in _edit_locks:
            _edit_locks[message_key] = asyncio.Lock()

        if _edit_locks[message_key].locked():
            logger.debug(f"Throttled edit for message {message_key}: another edit in progress")
            return True

        async with _edit_locks[message_key]:
            if message_key not in _edit_counters:
                _edit_counters[message_key] = {"count": 0, "last_reset": current_time}

            if current_time - _edit_counters[message_key]["last_reset"] > 3:
                _edit_counters[message_key] = {"count": 0, "last_reset": current_time}

            _edit_counters[message_key]["count"] += 1

            last_edit = _last_edit_time.get(message_key, 0)
            min_interval = 0.8
            if _edit_counters[message_key]["count"] > 3:
                excessive_edits = _edit_counters[message_key]["count"] - 3
                min_interval += min(3.0, excessive_edits * 0.7)

            if current_time - last_edit < min_interval:
                if _edit_counters[message_key]["count"] > 10:
                    logger.debug(f"Ignoring edit for {message_key}: too many edits")
                    return True
                wait_time = min_interval - (current_time - last_edit)
                await asyncio.sleep(wait_time)

            # Clean HTML if required
            if parse_mode == "HTML" and text:
                text = clean_html_entities(text)

            # Always add invisible character at the end (safe place)
            if text:
                text = text.rstrip() + "\u200B"

            kwargs = {"text": text}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode

            _last_edit_time[message_key] = time.time()
            await message.edit_text(**kwargs)
            return True

    except BadRequest as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str:
            logger.debug("Message not modified despite randomization")
            return True
        elif "can't parse entities" in error_str:
            logger.warning(f"Parse error: {e}")
            try:
                plain_text = re.sub(r"<[^>]+>", "", text)
                plain_text = plain_text.rstrip() + "\u200B"
                await message.edit_text(text=plain_text, reply_markup=reply_markup)
                return True
            except Exception as plain_err:
                logger.debug(f"Plain fallback failed: {plain_err}")
            return True
        else:
            logger.warning(f"Error editing message: {e}")
            return True
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")
        return True

async def safe_edit_message_caption(message: Message, caption: str, reply_markup=None, parse_mode=None):
    try:
        if not message:
            logger.warning("Cannot edit caption: message is None")
            return False

        if parse_mode == "HTML" and caption:
            caption = clean_html_entities(caption)

        if caption:
            caption = caption.rstrip() + "\u200B"

        kwargs = {"caption": caption}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode

        await message.edit_caption(**kwargs)
        return True

    except BadRequest as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str:
            return False
        elif "can't parse entities" in error_str:
            logger.warning(f"Parse error in caption: {e}")
            return False
        else:
            logger.warning(f"Error editing caption: {e}")
            return False
    except Exception as e:
        logger.warning(f"Unexpected error editing caption: {e}")
        return False
