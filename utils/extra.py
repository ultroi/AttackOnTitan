import logging
from telegram import Update


logger = logging.getLogger(__name__)


async def buy_command(update: Update, context):
                try:
                    if not update.effective_user or not update.message:
                        if update.message:
                            await update.message.reply_text("User or message information not available.")
                        return
                    args = context.args
                    if len(args) != 2:
                        await update.message.reply_text("Usage: /buy <currency_type> <amount>")
                        return
                    currency_type = args[0]
                    try:
                        amount = int(args[1])
                    except ValueError:
                        await update.message.reply_text("Amount must be an integer.")
                        return
                    shop_system = context.bot_data["shop_system"]
                    user_id = str(update.effective_user.id)
                    result = await shop_system.buy_currency(context, user_id, currency_type, amount)
                    await update.message.reply_text(result)
                except Exception as e:
                    logger.error(f"Error in buy_command: {e}")
                    if update.message:
                        await update.message.reply_text(f"Error processing buy command: {str(e)}")

def get_owner_id():
    return 5956598856

OWNER_IDS = [5956598856, 5845254367]  # List of all owner IDs



