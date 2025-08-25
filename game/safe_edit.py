import logging
from telegram import Message
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

async def safe_edit_message_text(message: Message, text: str, reply_markup=None, parse_mode=None):
    
    try:
        # Check if message is None or missing required attributes
        if not message or not hasattr(message, 'text'):
            logger.warning("Cannot edit: message is None or missing text attribute")
            return False
            
        # Add invisible character to ensure message is always different
        # Uses zero-width space character to make the message different without visible changes
        if '\u200B' not in text:
            # Add at a random position to ensure uniqueness
            import random
            pos = random.randint(0, max(0, len(text)-1))
            text = text[:pos] + '\u200B' + text[pos:]
                
        # Create kwargs dynamically
        kwargs = {"text": text}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
            
        await message.edit_text(**kwargs)
        return True
        
    except BadRequest as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str:
            # This shouldn't happen with our zero-width space trick, but just in case
            logger.debug(f"Message not modified despite randomization: {e}")
            return False
        elif "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query error
            logger.debug(f"Query expired or invalid: {e}")
            return False
        elif "message to edit not found" in error_str:
            logger.debug(f"Message to edit not found: {e}")
            return False
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing message: {e}")
            return False  # Return False instead of raising to prevent crashes
            
    except Exception as e:
        logger.warning(f"Unexpected error editing message: {e}")
        return False  # Return False instead of raising to prevent crashes

async def safe_edit_message_caption(message: Message, caption: str, reply_markup=None, parse_mode=None):
    try:
        # Check if message is None or missing required attributes
        if not message or not hasattr(message, 'caption'):
            logger.warning("Cannot edit caption: message is None or missing caption attribute")
            return False
        
        # Add invisible character to ensure caption is always different
        # Uses zero-width space character to make the caption different without visible changes
        if caption and '\u200B' not in caption:
            # Add at a random position to ensure uniqueness
            import random
            pos = random.randint(0, max(0, len(caption)-1))
            caption = caption[:pos] + '\u200B' + caption[pos:]
                
        # Create kwargs dynamically
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
            # This shouldn't happen with our zero-width space trick, but just in case
            logger.debug(f"Caption not modified despite randomization: {e}")
            return False
        elif "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query error
            logger.debug(f"Query expired or invalid: {e}")
            return False
        elif "message to edit not found" in error_str:
            logger.debug(f"Message to edit not found: {e}")
            return False
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing caption: {e}")
            return False  # Return False instead of raising to prevent crashes
            
    except Exception as e:
        logger.warning(f"Unexpected error editing caption: {e}")
        return False  # Return False instead of raising to prevent crashes
