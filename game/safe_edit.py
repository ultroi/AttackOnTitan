"""
Safe message editing utility functions to prevent "message is not modified" errors.
"""
import logging
from telegram import Message
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

async def safe_edit_message_text(message: Message, text: str, reply_markup=None, parse_mode=None):
    """
    Safely edit message text, handling the "message is not modified" error gracefully.
    
    Args:
        message: The Telegram message to edit
        text: The new text content
        reply_markup: Optional reply markup (InlineKeyboardMarkup)
        parse_mode: Optional parse mode (HTML, Markdown, etc)
        
    Returns:
        True if the message was edited, False if it was identical or couldn't be edited
    """
    try:
        # Check if content is identical (basic check)
        if message.text == text:
            # Check if markup is also identical
            existing_markup = message.reply_markup
            if (existing_markup is None and reply_markup is None) or \
               (existing_markup is not None and reply_markup is not None and 
                existing_markup.to_dict() == reply_markup.to_dict()):
                # Both text and markup are identical - don't attempt edit
                logger.debug(f"Skipping edit: message content and markup unchanged")
                return False
                
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
            # Just log at debug level
            logger.debug(f"Message not modified: {e}")
            return False
        elif "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query error
            logger.debug(f"Query expired or invalid: {e}")
            return False
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing message: {e}")
            raise
            
    except Exception as e:
        logger.warning(f"Unexpected error editing message: {e}")
        raise

async def safe_edit_message_caption(message: Message, caption: str, reply_markup=None, parse_mode=None):
    """
    Safely edit message caption, handling the "message is not modified" error gracefully.
    
    Args:
        message: The Telegram message to edit
        caption: The new caption content
        reply_markup: Optional reply markup (InlineKeyboardMarkup)
        parse_mode: Optional parse mode (HTML, Markdown, etc)
        
    Returns:
        True if the message was edited, False if it was identical or couldn't be edited
    """
    try:
        # Check if content is identical (basic check)
        if message.caption == caption:
            # Check if markup is also identical
            existing_markup = message.reply_markup
            if (existing_markup is None and reply_markup is None) or \
               (existing_markup is not None and reply_markup is not None and 
                existing_markup.to_dict() == reply_markup.to_dict()):
                # Both caption and markup are identical - don't attempt edit
                logger.debug(f"Skipping edit: caption and markup unchanged")
                return False
                
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
            # Just log at debug level
            logger.debug(f"Caption not modified: {e}")
            return False
        elif "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query error
            logger.debug(f"Query expired or invalid: {e}")
            return False
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing caption: {e}")
            raise
            
    except Exception as e:
        logger.warning(f"Unexpected error editing caption: {e}")
        raise
