# bank_command.py

import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.models import Player, BankAccount
from game.bank_system import BankSystem, BANK_OPEN_FEE, PENALTY_BASE


async def get_player_and_dependencies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A helper function to get db, bank_system, and player objects."""
    db = context.bot_data.get("db")
    if not db:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ Database not initialized. Please try again later.")
        return None, None, None

    bank_system = BankSystem(db)
    user_id = str(update.effective_user.id) if hasattr(update, "effective_user") and update.effective_user is not None and hasattr(update.effective_user, "id") else None
    if user_id is None:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ Internal error: user not found.")
        return None, None, None
    player = await db.get_player(user_id) 

    if not player:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ You don't have a character yet. Use /start to create one.")
        return None, None, None

    return db, bank_system, player



## --- Main Command Handlers ---
async def handle_bank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows bank account info or provides an option to open one."""
    db, bank_system, player = await get_player_and_dependencies(update, context)
    if not player:
        return

    if player.level < 15:
        await update.message.reply_text(
            f"You must be at least <b>Level 15</b> to interact with the <b>Central Bank</b>.\n",
            parse_mode='HTML'
        )
        return

    account = await db.get_bank_account(player.user_id)

    if account and account.opened:
        # Get central bank stats
        stats = await bank_system.get_central_bank_stats()

        # Get top 3 richest players
        top_players_list = []
        for i, p in enumerate(stats['top_3_richest']):
            player_obj = await db.get_player(p['user_id'])
            first_name = player_obj.name.split()[0] if player_obj and player_obj.name else p['user_id']
            top_players_list.append(f"👑 <b>#{i+1}</b>: {first_name}")
        top_players_str = '\n'.join(top_players_list) if top_players_list else "No players with accounts yet."

        # Your balance
        info = bank_system.get_player_bank_info(account)

        caption = (
            f"🏦 <b>Central Bank Account Summary</b>\n"
            f"────────────────────\n"
            f"<b>🏰 Total Bank Reserves:</b>\n"
            f"🔹 Marks: <code>{stats['total_reserve']['marks']}</code>\n"
            f"🔸 Valor: <code>{stats['total_reserve']['valor']}</code>\n"
            f"💎 Crystal: <code>{stats['total_reserve']['crystal']}</code>\n"
            f"\n"
            f"<b>👤 Your Account Balance:</b>\n"
            f"🔹 Marks: <code>{info['marks']}</code>\n"
            f"🔸 Valor: <code>{info['valor']}</code>\n"
            f"💎 Crystal: <code>{info['crystal']}</code>\n"
            f"\n"
            f"🏅 <b>Top 3 Richest Players</b>:\n"
            f"{top_players_str}"
        )
        await update.message.reply_photo(
            photo="https://i.ibb.co/FqBX9JMp/image.jpg",
            caption=caption,
            parse_mode='HTML'
        )

    else:
        # Account not yet opened
        msg = (
            f"🏦 <b>Welcome to the Central Bank!</b>\n"
            f"Secure your wealth with us and access exclusive features.\n\n"
            f"📜 <b>Opening Fee:</b>\n"
            f"🔹 Marks: <code>{BANK_OPEN_FEE['marks']}</code>\n"
            f"🔸 Valor: <code>{BANK_OPEN_FEE['valor']}</code>\n"
            f"💎 Crystal: <code>{BANK_OPEN_FEE['crystal']}</code>\n\n"
            f"🔓 Click the button below to pay and activate your account."
        )
        keyboard = [
            [InlineKeyboardButton("💳 Pay & Open Account", callback_data="bank_open_account")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='HTML')

async def handle_deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles depositing currency into the bank. Usage: /deposit [currency] [amount]"""
    db, bank_system, player = await get_player_and_dependencies(update, context)
    if not player:
        return

    # --- Argument validation ---
    if not hasattr(context, "args") or context.args is None or not isinstance(context.args, list) or len(context.args) != 2:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("Usage: `/deposit <currency> <amount>`\nExample: `/deposit marks 5000`", parse_mode='Markdown')
        return

    currency = context.args[0].lower()
    amount_str = context.args[1]

    if currency not in ['marks', 'valor', 'crystal']:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text(f"❌ Invalid currency. Use `marks`, `valor`, or `crystal`.", parse_mode='Markdown')
        return

    if not amount_str.isdigit() or int(amount_str) <= 0:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ Amount must be a positive number.")
        return
    amount = int(amount_str)

    # --- Execution ---
    if db is None or bank_system is None or player is None:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ Internal error: missing dependencies.")
        return

    account = await db.get_bank_account(player.user_id) if hasattr(db, "get_bank_account") else None
    if not account or not getattr(account, "opened", False):
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ You need to open a bank account first. Use the /bank command.")
        return

    # The bank_system.deposit method now handles the full transaction
    if hasattr(bank_system, "deposit"):
        status_message = await bank_system.deposit(player, account, currency, amount)
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text(status_message)
    else:
        if hasattr(update, "message") and update.message is not None:
            await update.message.reply_text("❌ Internal error: deposit method not found.")


async def handle_withdrawal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles withdrawing currency from the bank. Usage: /withdraw [currency] [amount]"""
    db, bank_system, player = await get_player_and_dependencies(update, context)
    if not player:
        return

    # --- Argument validation ---
    if len(context.args) != 2:
        await update.message.reply_text("Usage: `/withdraw <currency> <amount>`\nExample: `/withdraw marks 5000`", parse_mode='Markdown')
        return

    currency = context.args[0].lower()
    amount_str = context.args[1]

    if currency not in ['marks', 'valor', 'crystal']:
        await update.message.reply_text(f"❌ Invalid currency. Use `marks`, `valor`, or `crystal`.", parse_mode='Markdown')
        return

    if not amount_str.isdigit() or int(amount_str) <= 0:
        await update.message.reply_text("❌ Amount must be a positive number.")
        return
    amount = int(amount_str)

    # --- Execution ---
    account = await db.get_bank_account(player.user_id)
    if not account or not account.opened:
        await update.message.reply_text("❌ You need to open a bank account first. Use the /bank command.")
        return

    # The bank_system.withdrawal method now handles the full transaction
    status_message = await bank_system.withdrawal(player, account, currency, amount)
    await update.message.reply_text(status_message)





## --- Callback Query Handler for Buttons ---

async def handle_open_bank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Open Account' button press."""
    query = update.callback_query
    await query.answer() # Acknowledge the button press

    db = context.bot_data.get("db")
    bank_system = BankSystem(db)
    user_id = str(query.from_user.id)
    player = await db.get_player(user_id)

    # Call the open_bank function which returns a status message
    status_message = await bank_system.open_bank(player)

    # Edit the original message to show the result
    await query.edit_message_text(text=status_message, parse_mode='Markdown')