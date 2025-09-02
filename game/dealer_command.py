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

# Track last dealer appearance times for users
user_last_dealer: Dict[str, float] = {}

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
    
    # Check for cooldown (no more than one dealer per 3 hours)
    current_time = time.time()
    last_dealer_time = user_last_dealer.get(user_id_str, 0)
    
    if current_time - last_dealer_time < 10800:  # 3 hours in seconds
        return
    
    # 10% chance for dealer to appear during explore
    if random.random() < 0.1:
        # Mark dealer appearance time
        user_last_dealer[user_id_str] = current_time
        
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
