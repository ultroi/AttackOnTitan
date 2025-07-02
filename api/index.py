import os
import logging
import asyncio
from flask import Flask, request, Response, jsonify
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

if not SECRET_TOKEN:
    SECRET_TOKEN = TOKEN.split(":")[1]

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Global bot application
bot_app = None

async def initialize_bot():
    """Initialize the Telegram bot application"""
    global bot_app
    
    if bot_app is None:
        try:
            # Create bot application
            bot_app = Application.builder().token(TOKEN).build()
            
            # Import handlers (with error handling for missing modules)
            try:
                from game.character_system import (
                    show_character_selection, profile, show_character_profile,
                    add_to_team, save_team, clear_team, manage_team,
                    show_character_details, confirm_character_selection,
                    create_character, show_team, back_to_selection,
                    show_equipment, show_achievements, explore_map, fill_gas
                )
                from game.explore import explore
                from game.callback_handlers import button_callback
                
                # Add command handlers
                bot_app.add_handler(CommandHandler("start", show_character_selection))
                bot_app.add_handler(CommandHandler("profile", profile))
                bot_app.add_handler(CommandHandler("explore", explore))
                
                # Add callback handlers
                bot_app.add_handler(CallbackQueryHandler(button_callback))
                bot_app.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
                bot_app.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
                bot_app.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))
                bot_app.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))
                bot_app.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
                bot_app.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
                bot_app.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
                bot_app.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
                bot_app.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
                bot_app.add_handler(CallbackQueryHandler(clear_team, pattern="^clear_team$"))
                bot_app.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
                bot_app.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
                bot_app.add_handler(CallbackQueryHandler(show_equipment, pattern="^show_equipment$"))
                bot_app.add_handler(CallbackQueryHandler(show_achievements, pattern="^show_achievements$"))
                bot_app.add_handler(CallbackQueryHandler(explore_map, pattern="^explore_map$"))
                bot_app.add_handler(CallbackQueryHandler(fill_gas, pattern="^fill_gas$"))
                
                logger.info("All game handlers loaded successfully")
                
            except ImportError as e:
                logger.warning(f"Some game modules not found: {e}")
                
                # Basic fallback handlers
                async def start_command(update: Update, context):
                    await update.message.reply_text("🎮 Attack on Titan Bot is running!")
                
                async def default_callback(update: Update, context):
                    query = update.callback_query
                    await query.answer("Feature not available yet!")
                
                bot_app.add_handler(CommandHandler("start", start_command))
                bot_app.add_handler(CallbackQueryHandler(default_callback))
            
            # Add shop handler
            try:
                from game.shop_system import ShopSystem
                
                async def shop_command(update: Update, context):
                    if not update.effective_user or not update.message:
                        return
                    shop_system = ShopSystem()
                    await shop_system.check_daily_refresh()
                    message, reply_markup = await shop_system.show_shop(update.effective_user.id)
                    await update.message.reply_text(
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                
                bot_app.add_handler(CommandHandler("shop", shop_command))
                logger.info("Shop handler loaded successfully")
                
            except ImportError:
                logger.warning("Shop system not available")
            
            # Add performance monitoring
            try:
                from utils.performance_monitor import performance_command, performance_callback_handler
                bot_app.add_handler(CommandHandler("performance", performance_command))
                bot_app.add_handler(CallbackQueryHandler(performance_callback_handler, pattern="^perf_"))
                logger.info("Performance monitoring loaded successfully")
            except ImportError:
                logger.warning("Performance monitoring not available")
            
            # Error handler
            async def error_handler(update: object, context):
                logger.error(f"Update {update} caused error {context.error}")
                if isinstance(update, Update) and update.effective_message:
                    try:
                        await update.effective_message.reply_text(
                            "⚠️ An error occurred. Please try again later."
                        )
                    except Exception:
                        pass
            
            bot_app.add_error_handler(error_handler)
            
            # Initialize the application
            await bot_app.initialize()
            logger.info("Bot application initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    return bot_app

@app.route('/')
def index():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "message": "Attack on Titan Bot is running on Vercel",
        "bot_token": "✓ Configured" if TOKEN else "✗ Missing"
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook updates"""
    # Verify secret token
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning("Invalid secret token received")
        return Response(status=403)
    
    try:
        # Get update data
        json_data = request.get_json()
        if not json_data:
            logger.error("No JSON data received")
            return Response(status=400)
        
        # Process update in new event loop
        async def process_update():
            try:
                app_instance = await initialize_bot()
                update = Update.de_json(json_data, app_instance.bot)
                if update:
                    await app_instance.process_update(update)
                    logger.info(f"Successfully processed update: {update.update_id}")
                else:
                    logger.error("Failed to parse update from JSON")
            except Exception as e:
                logger.error(f"Error processing update: {e}")
                raise
        
        # Run the async function
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(process_update())
        except Exception as e:
            logger.error(f"Error in event loop: {e}")
            return Response(status=500)
        finally:
            loop.close()
        
        return Response(status=200)
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    """Set webhook URL for the bot"""
    try:
        # Get webhook URL from request or construct it
        if request.method == 'POST':
            data = request.get_json() or {}
            webhook_url = data.get('url')
        else:
            webhook_url = f"https://{request.host}/webhook"
        
        if not webhook_url:
            return jsonify({"status": "error", "message": "No webhook URL provided"}), 400
        
        # Set webhook using requests (synchronous)
        import requests
        
        set_webhook_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
        payload = {
            'url': webhook_url,
            'secret_token': SECRET_TOKEN,
            'allowed_updates': ['message', 'callback_query']
        }
        
        response = requests.post(set_webhook_url, json=payload)
        result = response.json()
        
        if result.get('ok'):
            logger.info(f"Webhook set successfully to {webhook_url}")
            return jsonify({
                "status": "success",
                "message": f"Webhook set to {webhook_url}",
                "telegram_response": result
            })
        else:
            logger.error(f"Failed to set webhook: {result}")
            return jsonify({
                "status": "error", 
                "message": "Failed to set webhook",
                "telegram_response": result
            }), 500
            
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook_info')
def webhook_info():
    """Get current webhook information"""
    try:
        import requests
        
        url = f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
        response = requests.get(url)
        result = response.json()
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

# This is the WSGI application that Vercel will use
application = app

if __name__ == "__main__":
    # For local testing
    app.run(debug=True, port=5000)
