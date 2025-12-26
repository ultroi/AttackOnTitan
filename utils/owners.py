from typing import Callable, Any
from telegram import Update
from telegram.ext import ContextTypes
import functools

def get_owner_ids():
    return OWNER_IDS

OWNER_IDS = [6439532660, 5845254367]  

def is_owner(func: Callable[[Update, ContextTypes.DEFAULT_TYPE], Any]) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Any]:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user:
            return
        
        user_id = update.effective_user.id
        
        # Check if user is an owner
        if user_id in get_owner_ids():
            return await func(update, context, *args, **kwargs)
        else:
            if update.effective_message:
                await update.effective_message.reply_text("This command is only available for owners.")
            return
    return wrapper
