from typing import Dict, List, Optional, Any
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
from utils.mod_utils import mod_only
from game.dealer_system import show_dealer
import random
import logging
import time

logger = logging.getLogger(__name__)

@maintenance_protected
@ban_protected
async def explore_dealer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Random chance to encounter a dealer when exploring
    """
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # 10% chance for dealer to appear during explore
    if random.random() < 0.05:
        # Clean up any existing titan before showing dealer
        try:
            from database.db import Database
            db = context.bot_data.get("db")
            if db:
                existing_titan = await db.get_titan(user_id_str)
                if existing_titan:
                    await db.delete_titan(user_id_str)
                    logger.info(f"Cleaned up existing titan for user {user_id_str} due to dealer encounter")
        except Exception as e:
            logger.error(f"Error cleaning up existing titan for dealer: {e}")
        
        # Show dealer
        await show_dealer(update, context)
        return True  # Signal that dealer was shown
    
    return False  # No dealer was shown


