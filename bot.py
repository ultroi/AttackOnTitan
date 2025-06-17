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



async def initialize_bot():
    """Initialize the bot with all handlers."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("explore", explore))
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
