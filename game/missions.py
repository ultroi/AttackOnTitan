from telegram.ext import CommandHandler, CallbackQueryHandler
from game.missions_command import missions_command, missions_callback_handler

def setup_missions_commands(application):
    """
    Register the mission commands with the application.
    
    Args:
        application: The Telegram application instance
    """
    # Register the /missions command
    application.add_handler(CommandHandler("missions", missions_command))
    
    # Register callback query handler for mission-related buttons
    application.add_handler(CallbackQueryHandler(missions_callback_handler, pattern=r"^mission_"))
