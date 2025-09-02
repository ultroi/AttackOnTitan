from typing import Dict, List, Optional, Any
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
from utils.mod_utils import mod_only
from game.dealer_system import show_dealer, test_dealer
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
    if random.random() < 0.1:
        # Show dealer
        await show_dealer(update, context)
        return True  # Signal that dealer was shown
    
    return False  # No dealer was shown

@mod_only
async def dealer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Command to test dealer appearance (mod only)
    """
    if not update.effective_user:
        return
    
    # Show dealer
    await show_dealer(update, context)
