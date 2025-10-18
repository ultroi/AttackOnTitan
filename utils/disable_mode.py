from telegram import Update
from telegram.ext import ContextTypes
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod
from database.db_instance import get_database

DISABLE_COLLECTION = "disabled_commands_list"

# Add a command to the disabled list
async def disable_command_db(command: str):
    db = await get_database()
    if db is None:
        raise Exception("Database not available")
    await db[DISABLE_COLLECTION].update_one(
        {"_id": "disabled_commands"},
        {"$addToSet": {"commands": command}},
        upsert=True
    )

# Remove a command from the disabled list
async def enable_command_db(command: str):
    db = await get_database()
    if db is None:
        raise Exception("Database not available")
    await db[DISABLE_COLLECTION].update_one(
        {"_id": "disabled_commands"},
        {"$pull": {"commands": command}},
        upsert=True
    )

# Get the list of disabled commands
async def get_disabled_commands_db():
    db = await get_database()
    if db is None:
        return []
    doc = await db[DISABLE_COLLECTION].find_one({"_id": "disabled_commands"})
    return doc.get("commands", []) if doc else []


# Decorator to protect specific commands
def disable_protected(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        user_id = getattr(update.effective_user, "id", 0) or 0
        command = None
        if hasattr(update, "message") and update.message and update.message.text:
            command = update.message.text.split()[0].lstrip("/").split("@")[0].lower()
        if command:
            try:
                disabled = await get_disabled_commands_db()
            except Exception as e:
                logger.error(f"⚠️ Failed to get disabled commands: {e}")
                # Continue anyway, don't block the command
                disabled = []
            if command in disabled:
                owner_ids = get_owner_ids()
                mod = False
                try:
                    mod = await is_mod(int(user_id))
                except Exception:
                    pass
                if int(user_id) not in owner_ids and not mod:
                    if update.effective_message:
                        await update.effective_message.reply_text(f"/{command} command is currently disabled by an admin.")
                    return
        return await func(update, context, *args, **kwargs)
    return wrapper


# Command handler to disable a specific command
async def disable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_ids = get_owner_ids()
    user_id = getattr(update.effective_user, "id", None)
    if user_id not in owner_ids:
        await update.effective_message.reply_text("Only owners can disable commands.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /disablecmd <command>")
        return
    cmd = context.args[0].lower().lstrip("/")
    await disable_command_db(cmd)
    await update.effective_message.reply_text(f"/{cmd} command disabled.")

# Command handler to enable a specific command
async def enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_ids = get_owner_ids()
    user_id = getattr(update.effective_user, "id", None)
    if user_id not in owner_ids:
        await update.effective_message.reply_text("Only owners can enable commands.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /enablecmd <command>")
        return
    cmd = context.args[0].lower().lstrip("/")
    await enable_command_db(cmd)
    await update.effective_message.reply_text(f"/{cmd} command enabled for all users.")
