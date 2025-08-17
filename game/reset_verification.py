from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging

logger = logging.getLogger(__name__)

async def reset_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Command to allow users to reset their own verification state if they're stuck.
    Usage: /resetverify
    """
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Get database
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("System error: Database not available.")
        return
    
    # Reset verification flags in user data
    if context.user_data:
        context.user_data["hcaptcha_prompted"] = False
        context.user_data["verified"] = False
        context.user_data["captcha_active"] = False
    
    # Reset verification in database
    try:
        await db.update_player(user_id_str, {
            "hcaptcha_verified": False,
            "hcaptcha_start_time": None,
            "explore_start_time": None
        })
        
        await update.message.reply_text(
            "✅ <b>Verification status has been reset!</b>\n\n"
            "You can now use /explore again normally.\n\n"
            "If you're asked to complete verification, please do so to continue exploring.",
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"User {user_id} reset their own verification state")
    except Exception as e:
        logger.error(f"Failed to reset verification for user {user_id}: {e}")
        await update.message.reply_text(
            "❌ Failed to reset verification. Please try again later."
        )
