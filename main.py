import os
import logging
import asyncio
import random
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from database.db_instance import initialize_database, get_database
from game.character_system import (
    start_character_selection,
    profile,
    show_character_profile,
    add_to_team,
    save_team,
    manage_team,
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
    show_team,
    back_to_selection
)
from game.explore import (
    explore,
    active_battles,
    handle_battle_start
)
from telegram.constants import ParseMode
from game.callback_handlers import button_callback

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = await get_database()
    logger.info(f"Start command triggered for user_id {user_id}")
    player = await db.get_player(user_id)
    
    if player:
        logger.info(f"Player found for user_id {user_id}: {player.name}")
        if db.players.find_one({"user_id": user_id}):
            await update.message.reply_text(
                "You have already started your journey! Use /explore to explore the world of Attack on Titan."
            )
        else:
            await start_character_selection(update, context)
    else:
        await start_character_selection(update, context)

async def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")
    if update.message:
        #show error message and reason of error 
        await update.message.reply_text(f"Error: {context.error}")
    else:
        await update.callback_query.message.reply_text(f"Error: {context.error}")


# STRICT OWNER VERIFICATION
OWNERS = {5956598856, 5845254367}
ADMIN_LOG_CHANNEL = -1002848899456

async def owner_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Immediate reset by owner — no confirmation"""
    user = update.effective_user
    message = update.message 

    # Silent ignore if not owner
    if user.id not in OWNERS:
        await message.reply_text("U not owner, Baka !! 😾")
        return

    target_id = int(context.args[0])

    # Optional reason
    reason = " ".join(context.args[1:]).strip()
    


    # Database deletion
    db_instance = await get_database()
    player_result = await db_instance.players.delete_one({"user_id": target_id})
    char_result = await db_instance.characters.delete_many({"user_id": target_id})

    # Attempt to fetch user info for logging
    try:
        target_user = await context.bot.get_chat(target_id)
        target_name = f"<a href='tg://user?id={target_user.id}'>{target_user.first_name}</a>"
    except:
        target_name = f"`{target_id}`"

    executor_name = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    # Send log to audit channel
    log_msg = (
        f"☢️ <b>RESET INITIATED</b>☢️\n\n"
        f"👤 <b>Target:</b> {target_name}\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
        f"🛡️ <b>By:</b> {executor_name}\n"
    )
    if reason:
        log_msg += f"\n📌 Reason: <code>{reason}</code>"

    try:
        await context.bot.send_message(ADMIN_LOG_CHANNEL, log_msg, parse_mode="HTML")
    except:
        pass

    # Notify target if possible
    try:
        await context.bot.send_message(
            target_id,
            "⚠️ Your account has been resetted.\n"
        )
    except:
        pass



async def initialize_bot():
    """Initialize the bot with all handlers."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("explore", explore))
    application.add_handler(CommandHandler("nuke", owner_reset))
    application.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
    application.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))  # Show character details
    application.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))  # Confirm character selection
    application.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))  
    application.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
    application.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
    application.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
    application.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))  # New handler for adding to team
    application.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
    application.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    return application

async def main():
    try:
        logger.info("Initializing database...")
        await initialize_database()
        logger.info("Database initialized successfully")
        
        application = await initialize_bot()
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
