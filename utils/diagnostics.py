import logging
from telegram import Update
from telegram.ext import ContextTypes
from database.db import Database
from database.db_instance import get_persistent_database
import asyncio
import json
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

async def diagnostic_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check database connections and group records"""
    user_id = update.effective_user.id
    # List of allowed admin user IDs
    admin_ids = [5956598856]  # Add your user ID here
    
    if user_id not in admin_ids:
        await update.message.reply_text("Sorry, this command is only for administrators.")
        return
        
    try:
        await update.message.reply_text("Running database diagnostics, please wait...")
        
        # Get DB instance from context if available
        db = context.bot_data.get("db")
        if not db:
            await update.message.reply_text("⚠️ No database in context.bot_data - creating new instance")
            db = Database()
            await db.init_db()
        
        # Check connection
        conn_status = "Connected" if db.db else "Not connected"
        
        # Check if collections are initialized
        collections_status = {
            "players": db.players is not None,
            "characters": db.characters is not None,
            "groups": db.groups is not None
        }
        
        # Create a separate dict for database connection details
        db_details = {}
        try:
            db_details["db_name"] = db.db.name if db.db else "None"
            db_details["available_collections"] = await db.db.list_collection_names() if db.db else []
        except Exception as e:
            db_details["error"] = str(e)
        
        # Check recent group updates (last 24 hours)
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        recent_groups = []
        
        if db.groups:
            # Find groups updated in last 24 hours
            cursor = db.groups.find({"updated_at": {"$gte": yesterday}})
            async for doc in cursor:
                # Remove large fields for display
                if "admin_list" in doc:
                    del doc["admin_list"]
                recent_groups.append(doc)
        
        # Prepare diagnostics result
        result = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "connection_status": conn_status,
            "collections_initialized": collections_status,
            "database_details": db_details,
            "recent_group_updates": len(recent_groups),
            "recent_groups": recent_groups
        }
        
        # Send summary
        await update.message.reply_text(
            f"📊 Database Diagnostics\n"
            f"• Connection: {conn_status}\n"
            f"• Collections initialized: {all(collections_status.values())}\n"
            f"• Recent group updates: {len(recent_groups)}\n\n"
            f"Sending detailed report..."
        )
        
        # Format detailed JSON report
        result_str = json.dumps(result, indent=2, default=str)
        
        # Split if too long
        if len(result_str) > 4000:
            # Send in parts
            for i in range(0, len(result_str), 4000):
                chunk = result_str[i:i+4000]
                await update.message.reply_text(f"```json\n{chunk}\n```", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"```json\n{result_str}\n```", parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Error in diagnostic_db_command: {e}")
        await update.message.reply_text(f"Error running diagnostics: {str(e)}")

async def check_group_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to check if current group is in the database"""
    user_id = update.effective_user.id
    # List of allowed admin user IDs
    admin_ids = [5956598856]  # Add your user ID here
    
    if user_id not in admin_ids:
        await update.message.reply_text("Sorry, this command is only for administrators.")
        return
        
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("This command only works in groups.")
        return
        
    group_id = update.effective_chat.id
    
    try:
        # Get DB instance from context if available
        db = context.bot_data.get("db")
        if not db:
            await update.message.reply_text("⚠️ No database in context.bot_data - creating new instance")
            db = Database()
            await db.init_db()
            
        # Check if group exists in database
        group_record = await db.get_group(group_id)
        
        if group_record:
            # Found the group
            status = group_record.get("bot_status", "unknown")
            updated_at = group_record.get("updated_at", "unknown")
            added_at = group_record.get("added_at", "unknown")
            removed_at = group_record.get("removed_at", "unknown")
            
            await update.message.reply_text(
                f"✅ Group found in database\n"
                f"• Group ID: {group_id}\n"
                f"• Bot status: {status}\n"
                f"• Last updated: {updated_at}\n"
                f"• Added at: {added_at}\n"
                f"• Removed at: {removed_at}\n"
            )
        else:
            # Group not found
            await update.message.reply_text(
                f"❌ Group not found in database\n"
                f"• Group ID: {group_id}\n\n"
                f"I'll add this group to the database now."
            )
            
            # Add the group to the database
            group_data = {
                "group_id": group_id,
                "title": update.effective_chat.title,
                "type": update.effective_chat.type,
                "username": update.effective_chat.username,
                "link": f"https://t.me/{update.effective_chat.username}" if update.effective_chat.username else None,
                "is_bot_member": True,
                "bot_status": "MEMBER",
                "updated_at": datetime.now(timezone.utc),
                "added_at": datetime.now(timezone.utc),
                "added_by": update.effective_user.id,
                "added_by_name": update.effective_user.full_name
            }
            
            if await db.update_group(group_id, group_data):
                await update.message.reply_text("✅ Group successfully added to database!")
            else:
                await update.message.reply_text("❌ Failed to add group to database.")
            
    except Exception as e:
        logger.error(f"Error in check_group_record: {e}")
        await update.message.reply_text(f"Error checking group record: {str(e)}")
