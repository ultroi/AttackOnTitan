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

def clean_html_entities(text):
    """
    Clean and fix common HTML tag issues in text content.
    This fixes issues with zero-width spaces in closing tags and other common problems.
    """
    if not text:
        return text
        
    # First, balance HTML tags to prevent "unexpected end tag" errors
    # Count opening and closing tags for each type
    supported_tags = ['code', 'b', 'i', 'u', 's', 'strike', 'em', 'strong', 'pre', 'a']
    tag_counts = {tag: {'open': 0, 'close': 0} for tag in supported_tags}
    
    # Clean opening tags pattern
    open_pattern = re.compile(r'<([a-z]+)[^>]*>', re.IGNORECASE)
    # Clean closing tags pattern
    close_pattern = re.compile(r'</([a-z]+)[^>]*>', re.IGNORECASE)
    
    # Count tags
    for match in open_pattern.finditer(text):
        tag = match.group(1).lower()
        if tag in tag_counts:
            tag_counts[tag]['open'] += 1
            
    for match in close_pattern.finditer(text):
        tag = match.group(1).lower()
        if tag in tag_counts:
            tag_counts[tag]['close'] += 1
    
    # Fix malformed closing tags by replacing any weird versions of </tag> with proper ones
    # This regex finds any invisible character that might appear between < and /, between / and the tag name,
    # or within the tag name itself in closing tags
    text = re.sub(r'<([​\u200B\u200C\u200D\u2060\uFEFF]*)/?([​\u200B\u200C\u200D\u2060\uFEFF]*)(code|b|i|u|s|strike|em|strong|pre|a)([​\u200B\u200C\u200D\u2060\uFEFF]*)>', r'</\3>', text)
    
    # Fix tags with no content (which can cause issues)
    text = re.sub(r'<(code|b|i|u|s|strike|em|strong|pre)></\1>', '', text)
    
    # Fix improperly nested tags
    text = re.sub(r'(<[^>]+>)(<[^>]+>)(</[^>]+>)(</[^>]+>)', r'\1\2\3\4', text)
    
    # Fix any remaining problematic characters that might be in the text
    # Zero-width spaces within tags but not between < and / can be removed
    text = re.sub(r'<([a-z]+)([​\u200B\u200C\u200D\u2060\uFEFF]*)(>)', r'<\1\3', text)

    # More aggressive cleanup for closing tags with zero-width spaces
    text = re.sub(r'<\s*/?\s*([​\u200B\u200C\u200D\u2060\uFEFF]*)(b|i|u|s|code|pre|em|strong|strike|a)([​\u200B\u200C\u200D\u2060\uFEFF]*)\s*>', r'</\2>', text)
    
    # Balance tags - add missing closing tags or remove extra ones
    for tag, counts in tag_counts.items():
        # If more opening than closing tags, add missing closing tags at the end
        if counts['open'] > counts['close']:
            text += f"</{tag}>" * (counts['open'] - counts['close'])
        # If more closing than opening tags, remove the first extra closing tags
        elif counts['close'] > counts['open']:
            extra_closings = counts['close'] - counts['open']
            for _ in range(extra_closings):
                close_tag_pattern = re.compile(f'</\\s*{tag}\\s*>', re.IGNORECASE)
                match = close_tag_pattern.search(text)
                if match:
                    text = text[:match.start()] + text[match.end():]
    
    return text

async def safe_edit_message_text(message: Message, text: str, reply_markup=None, parse_mode=None):
    global _last_edit_time, _edit_counters, _edit_locks
    
    try:
        # Check if message is None or missing required attributes
        if not message or not hasattr(message, 'text'):
            logger.warning("Cannot edit: message is None or missing text attribute")
            return False
        
        # Get message identifier
        message_id = message.message_id
        chat_id = message.chat_id
        message_key = f"{chat_id}_{message_id}"
        
        # Apply anti-spam throttling
        current_time = time.time()
        
        # Initialize message-specific lock if needed
        if message_key not in _edit_locks:
            _edit_locks[message_key] = asyncio.Lock()
            
        # If another edit is already in progress, don't allow concurrent edits and return silently
        # This is a critical fix to prevent creating multiple messages during button spam
        if _edit_locks[message_key].locked():
            logger.debug(f"Throttled edit for message {message_key}: another edit in progress")
            return True  # Return True to indicate "handled" (prevents fallback to sending new messages)
            
        # Acquire lock for this message
        async with _edit_locks[message_key]:
            # Initialize or get edit counter
            if message_key not in _edit_counters:
                _edit_counters[message_key] = {"count": 0, "last_reset": current_time}
                
            # Check if we need to reset counter (over 3 seconds since last reset)
            if current_time - _edit_counters[message_key]["last_reset"] > 3:
                _edit_counters[message_key] = {"count": 0, "last_reset": current_time}
                
            # Increment edit counter
            _edit_counters[message_key]["count"] += 1
            
            # Rate limit if too many edits
            last_edit = _last_edit_time.get(message_key, 0)
            min_interval = 0.8
            
            # Apply aggressive throttling for spam protection
            if _edit_counters[message_key]["count"] > 3:
                # Add progressively longer delays based on edit count
                excessive_edits = _edit_counters[message_key]["count"] - 3
                additional_delay = min(3.0, excessive_edits * 0.7)  # Max 3s additional delay, more aggressive
                min_interval += additional_delay
                
            # If trying to edit too quickly, apply throttling
            if current_time - last_edit < min_interval:
                # For extreme spam (more than 10 edits), just silently ignore
                if _edit_counters[message_key]["count"] > 10:
                    logger.debug(f"Ignoring edit for message {message_key}: too many edits in short time")
                    return True  # Return True to prevent fallback to new messages
                
                # Normal throttling with wait
                wait_time = min_interval - (current_time - last_edit)
                logger.debug(f"Throttling edit for message {message_key}: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                
            # Pre-check and sanitize text before HTML cleaning
            if text and parse_mode == "HTML":
                # First try to detect and log obviously malformed HTML before cleaning
                if "</" in text and not "<" in text[:text.find("</")] or text.count("<") != text.count(">"):
                    logger.debug(f"Potentially malformed HTML detected before cleaning: {text[:100]}...")
                
                # Clean HTML tags if parse_mode is HTML
                original_text = text
                text = clean_html_entities(text)
                
                # If the text has significantly changed, log it for debugging
                if len(text) != len(original_text) and abs(len(text) - len(original_text)) > 10:
                    logger.debug(f"HTML cleaning changed text length significantly: {len(original_text)} -> {len(text)}")
            
            # Add multiple invisible characters to ensure message is always different
            # Uses zero-width space character to make the message different without visible changes
            import random
            if '\u200B' not in text:
                # Add at multiple random positions to ensure uniqueness
                for _ in range(2):  # Add 2 zero-width spaces
                    pos = random.randint(0, max(0, len(text)-1))
                    text = text[:pos] + '\u200B' + text[pos:]
            else:
                # Add one more zero-width space at a random position
                pos = random.randint(0, max(0, len(text)-1))
                text = text[:pos] + '\u200B' + text[pos:]
                    
            # Create kwargs dynamically
            kwargs = {"text": text}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode
                
            # Update last edit time
            _last_edit_time[message_key] = time.time()
                
            # Perform edit
            await message.edit_text(**kwargs)
            return True
        
    except BadRequest as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str:
            # This shouldn't happen with our zero-width space trick, but just in case
            # Return True to avoid fallback to new message creation - critical fix for button spam issue
            logger.debug(f"Message not modified despite randomization: {e}")
            return True
        elif "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query error - still return True to prevent new message creation
            logger.debug(f"Query expired or invalid: {e}")
            return True
        elif "message to edit not found" in error_str:
            logger.debug(f"Message to edit not found: {e}")
            return True  # Return True to prevent fallback
        elif "can't parse entities" in error_str:
            # Special case for entity parsing errors, which are often caused by invisible characters
            logger.warning(f"Error editing message: {e}")
            # Log the problematic text for debugging
            if parse_mode == "HTML":
                # Try to identify exactly what part of the HTML is problematic
                # Check for common issues
                tag_issues = []
                if text.count("<") != text.count(">"):
                    tag_issues.append(f"Unbalanced tags: {text.count('<')} opening vs {text.count('>')} closing brackets")
                
                # Check for unclosed or unopened tags
                html_tags = ["b", "i", "u", "s", "code", "pre", "em", "strong", "strike", "a"]
                for tag in html_tags:
                    opening = text.count(f"<{tag}")  # Approximate count of opening tags
                    closing = text.count(f"</{tag}")  # Approximate count of closing tags
                    if opening != closing:
                        tag_issues.append(f"Unbalanced {tag} tags: {opening} opening vs {closing} closing")
                
                # Try a fallback approach without HTML
                logger.debug(f"Problematic HTML: {text[:200]}...")
                if tag_issues:
                    logger.debug(f"HTML issues detected: {tag_issues}")
                
                # If there are HTML issues, try to fall back to plain text (strip all HTML)
                if tag_issues and "<" in text and ">" in text:
                    try:
                        # Try again with no parse_mode as fallback
                        plain_text = re.sub(r'<[^>]+>', '', text)  # Simple HTML tag removal
                        logger.debug(f"Attempting fallback with plain text (stripped HTML)")
                        await message.edit_text(text=plain_text, reply_markup=reply_markup)
                        return True
                    except Exception as plain_err:
                        logger.debug(f"Fallback plain text also failed: {plain_err}")
            
            return True  # Return True to prevent fallback
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing message: {e}")
            return True  # Return True to prevent fallback to new message creation
            
    except Exception as e:
        logger.warning(f"Unexpected error editing message: {e}")
        return True  # Return True to prevent fallback to new message creation

async def safe_edit_message_caption(message: Message, caption: str, reply_markup=None, parse_mode=None):
    try:
        # Check if message is None or missing required attributes
        if not message or not hasattr(message, 'caption'):
            logger.warning("Cannot edit caption: message is None or missing caption attribute")
            return False
        
        # Clean HTML tags if parse_mode is HTML
        if parse_mode == "HTML" and caption:
            caption = clean_html_entities(caption)
        
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
        elif "can't parse entities" in error_str:
            # Special case for entity parsing errors, which are often caused by invisible characters
            logger.warning(f"Error editing caption: {e}")
            # Log the problematic text for debugging
            if parse_mode == "HTML":
                logger.debug(f"Problematic HTML caption: {caption}")
            return False
        else:
            # Log other errors at warning level
            logger.warning(f"Error editing caption: {e}")
            return False  # Return False instead of raising to prevent crashes
            
    except Exception as e:
        logger.warning(f"Unexpected error editing caption: {e}")
        return False  # Return False instead of raising to prevent crashes
