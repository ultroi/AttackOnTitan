import psutil
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from game.explore import user_last_explore  
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Live tracking variables 
live_player_activity = {}  
battle_statistics = {
    "total_battles_today": 0,
    "total_explorations_today": 0,
    "peak_concurrent_battles": 0,
    "average_battle_duration": 0
}

def track_player_action(user_id: int, username: str, action: str, details: Optional[Dict] = None):
    """Track player activity in real-time without DB queries"""
    live_player_activity[user_id] = {
        "name": username,
        "action": action,
        "timestamp": datetime.now(),
        "details": details or {}
    }
    logger.info(f"📱 {username} ({user_id}) is now {action}")

def remove_player_activity(user_id: int):
    """Remove player from activity tracking"""
    if user_id in live_player_activity:
        player = live_player_activity[user_id]
        logger.info(f"📱 {player['name']} ({user_id}) finished {player['action']}")
        del live_player_activity[user_id]

def track_battle_end(user_id: int, username: str, result: str = "ended"):
    """Track when a battle ends and briefly show it in activity log"""
    live_player_activity[user_id] = {
        "name": username,
        "action": f"🏁 Battle {result}",
        "timestamp": datetime.now(),
        "details": {"battle_result": result, "status": "ended"}
    }
    logger.info(f"⚔️ {username} ({user_id}) battle {result}")
    
    # Remove after 30 seconds to avoid cluttering the log
    import threading
    def delayed_remove():
        import time
        time.sleep(30)
        if user_id in live_player_activity and live_player_activity[user_id].get("details", {}).get("status") == "ended":
            del live_player_activity[user_id]
    
    threading.Thread(target=delayed_remove, daemon=True).start()

def cleanup_stale_activity(max_age_minutes: int = 10):
    """Clean up stale activity records"""
    current_time = datetime.now()
    stale_users = []
    
    for user_id, activity in live_player_activity.items():
        age = (current_time - activity["timestamp"]).total_seconds() / 60
        if age > max_age_minutes:
            stale_users.append(user_id)
    
    for user_id in stale_users:
        del live_player_activity[user_id]
    
    if stale_users:
        logger.info(f"Cleaned up {len(stale_users)} stale activity records")

class ResourceMonitor:
    """Monitor system resources and bot performance"""
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get current memory usage statistics"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                "rss_mb": round(memory_info.rss / 1024 / 1024, 2),  # Resident Set Size
                "vms_mb": round(memory_info.vms / 1024 / 1024, 2),  # Virtual Memory Size
                "percent": round(process.memory_percent(), 2)
            }
        except Exception as e:
            logger.error(f"Error getting memory usage: {e}")
            return {
                "rss_mb": 0,
                "vms_mb": 0, 
                "percent": 0
            }
    
    def get_battle_stats(self) -> Dict[str, int]:
        """Get active battle statistics"""
        try:
            # Local import to avoid circular import
            from game.battle_system import active_battles
            
            num_battles = 0
            try:
                num_battles = len(active_battles)
            except:
                # In case active_battles is not iterable
                num_battles = 0
                
            # Calculate memory per battle with error handling
            memory_per_battle = 0
            try:
                process_memory = psutil.Process().memory_info().rss / 1024
                memory_per_battle = round(process_memory / max(num_battles, 1), 2)
            except:
                memory_per_battle = 0
                
            return {
                "active_battles": num_battles,
                "memory_per_battle_kb": memory_per_battle
            }
        except Exception as e:
            logger.error(f"Error getting battle stats: {e}")
            return {
                "active_battles": 0,
                "memory_per_battle_kb": 0
            }
    
    def get_system_load(self) -> Dict[str, Any]:
        """Get system CPU and load information"""
        try:
            # Get CPU percentage with a short interval
            cpu_percent = 0
            try:
                cpu_percent = psutil.cpu_percent(interval=0.1)
            except:
                cpu_percent = 0
                
            # Get load average if available
            load_avg = [0, 0, 0]
            try:
                if hasattr(psutil, 'getloadavg'):
                    load_avg = psutil.getloadavg()
            except:
                load_avg = [0, 0, 0]
                
            return {
                "cpu_percent": cpu_percent,
                "load_average": load_avg
            }
        except Exception as e:
            logger.error(f"Error getting system load: {e}")
            return {
                "cpu_percent": 0,
                "load_average": [0, 0, 0]
            }
    
    def log_performance_stats(self):
        """Log performance statistics"""
        try:
            memory = self.get_memory_usage()
            battles = self.get_battle_stats()
            system = self.get_system_load()
            
            logger.info(f"📊 Performance Stats:")
            logger.info(f"   Memory: {memory['rss_mb']}MB ({memory['percent']}%)")
            logger.info(f"   Active Battles: {battles['active_battles']}")
            logger.info(f"   CPU: {system['cpu_percent']}%")
            
        except Exception as e:
            logger.error(f"Failed to log performance stats: {e}")

    def get_live_player_stats(self) -> Dict[str, Any]:
        """Get live player activity without DB queries, deduplicated by user_id"""
        from game.battle_system import active_battles  # Local import to avoid circular import
        current_time = datetime.now()
        player_map = {}

        # Prefer battle info if present
        for user_id, battle in active_battles.items():
            try:
                player_info = live_player_activity.get(user_id, {})
                
                # Safely access character and titan properties
                character_name = "Unknown"
                titan_name = "Unknown"
                titan_hp = "?/?"
                char_hp = "?/?"
                
                # Check if battle has character attribute and it's not None
                if hasattr(battle, 'character') and battle.character:
                    character_name = getattr(battle.character, 'name', "Unknown")
                
                # Check if battle has titan attribute and it's not None
                if hasattr(battle, 'titan') and battle.titan:
                    titan_name = getattr(battle.titan, 'name', "Unknown")
                    
                    # Safely get titan HP
                    if hasattr(battle, 'titan_hp') and hasattr(battle.titan, 'max_hp'):
                        titan_hp = f"{battle.titan_hp}/{battle.titan.max_hp}"
                
                # Safely get character HP
                if hasattr(battle, 'character_hp') and hasattr(battle, 'character') and hasattr(battle.character, 'stats') and hasattr(battle.character.stats, 'HP'):
                    char_hp = f"{battle.character_hp}/{battle.character.stats.HP}"
                
                # Get battle turn and gas with default values if not present
                turn = getattr(battle, 'turn', 0)
                gas = getattr(battle, 'gas', 0)
                
                player_map[user_id] = {
                    "user_id": user_id,
                    "username": player_info.get("name", "Unknown"),
                    "action": "🔥 In Battle",
                    "character": character_name,
                    "vs_titan": titan_name,
                    "titan_hp": titan_hp,
                    "char_hp": char_hp,
                    "turn": turn,
                    "gas": gas,
                    "duration": (current_time - player_info.get("timestamp", current_time)).total_seconds() if user_id in live_player_activity else 0
                }
            except Exception as e:
                # If there's an error processing this battle, log it and skip
                logger.error(f"Error processing battle data for user {user_id}: {e}")
                continue

        # Add other active players (exploring, etc.) if not already present
        try:
            for user_id, activity in live_player_activity.items():
                if user_id not in player_map:
                    # Make sure activity has a timestamp
                    if "timestamp" not in activity:
                        continue
                        
                    try:
                        duration = (current_time - activity["timestamp"]).total_seconds()
                    except Exception as e:
                        logger.error(f"Error calculating duration for user {user_id}: {e}")
                        duration = 0
                        
                    # Remove stale activities (older than 5 minutes)
                    if duration < 300:
                        player_map[user_id] = {
                            "user_id": user_id,
                            "username": activity.get("name", "Unknown"),
                            "action": activity.get("action", "Unknown"),
                            "details": activity.get("details", {}),
                            "duration": duration
                        }
        except Exception as e:
            logger.error(f"Error processing live player activity: {e}")
        
        try:
            # Convert to list and compute statistics
            active_players = list(player_map.values())
            
            # Safely count exploring and ended players
            exploring_count = 0
            ended_count = 0
            
            for p in active_players:
                try:
                    action = p.get("action", "").lower()
                    if "exploring" in action:
                        exploring_count += 1
                    elif p.get("action", "").startswith("🏁"):
                        ended_count += 1
                except:
                    continue
            
            return {
                "total_active": len(active_players),
                "in_battle": len(active_battles),
                "exploring": exploring_count,
                "ended": ended_count,
                "players": active_players
            }
            
        except Exception as e:
            logger.error(f"Error finalizing player statistics: {e}")
            # Return safe fallback data
            return {
                "total_active": 0,
                "in_battle": 0,
                "exploring": 0,
                "ended": 0,
                "players": []
            }
    
    def get_formatted_live_status(self) -> str:
        """Get beautifully formatted live player status"""
        try:
            # Gather all stats with error handling
            try:
                stats = self.get_live_player_stats()
            except Exception as e:
                logger.error(f"Error getting live player stats: {e}")
                stats = {
                    "total_active": 0,
                    "in_battle": 0,
                    "exploring": 0,
                    "ended": 0,
                    "players": []
                }
                
            try:
                memory = self.get_memory_usage()
            except Exception as e:
                logger.error(f"Error getting memory usage: {e}")
                memory = {
                    "rss_mb": "?", 
                    "vms_mb": "?", 
                    "percent": "?"
                }
                
            try:
                system = self.get_system_load()
            except Exception as e:
                logger.error(f"Error getting system load: {e}")
                system = {
                    "cpu_percent": "?", 
                    "load_average": [0, 0, 0]
                }
            
            status = f"🤖 <b>AoT Bot Live Monitor</b> 🤖\n"
            status += f"⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>\n\n"
            
            # System stats
            status += f"🖥️ <b>System Status:</b>\n"
            status += f"   💾 Memory: {memory['rss_mb']}MB ({memory['percent']}%)\n"
            status += f"   🔥 CPU: {system['cpu_percent']}%\n\n"
            
            # Player activity summary
            status += f"👥 <b>Player Activity:</b>\n"
            status += f"   🎮 Total Active: {stats['total_active']}\n"
            status += f"   ⚔️ In Battle: {stats['in_battle']}\n"
            status += f"   🗺️ Exploring: {stats['exploring']}\n\n"
            
            if stats['players']:
                status += f"📋 <b>Live Players:</b>\n"
                # Safely process player information
                for player in stats['players'][:15]:  # Show max 15 players
                    try:
                        duration_str = f"{int(player.get('duration', 0))}s"
                        username = player.get('username', 'Unknown')
                        user_id = player.get('user_id', '?')
                        action = player.get('action', 'Unknown')
                        
                        if action == "🔥 In Battle":
                            status += f"   ⚔️ <b>{username}</b> ({user_id})\n"
                            status += f"      🗡️ {player.get('character', 'Unknown')} vs {player.get('vs_titan', 'Unknown')}\n"
                            status += f"      ❤️ {player.get('char_hp', '?/?')} | 🛡️ {player.get('titan_hp', '?/?')} | Turn {player.get('turn', '?')}\n"
                            status += f"      ⛽ Gas: {player.get('gas', '?')} | ⏱️ {duration_str}\n\n"
                        else:
                            status += f"   🔸 <b>{username}</b> ({user_id})\n"
                            status += f"      {action} | ⏱️ {duration_str}\n\n"
                    except Exception as e:
                        # Skip any player that causes an error
                        logger.error(f"Error processing player for status: {e}")
                        continue
                
                if len(stats['players']) > 15:
                    status += f"   ... and {len(stats['players']) - 15} more players\n"
            else:
                status += f"😴 <i>No active players right now</i>\n"
            
            return status
            
        except Exception as e:
            logger.error(f"Critical error in get_formatted_live_status: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Return a minimal status message that won't break
            return (
                "🤖 <b>AoT Bot Live Monitor</b> 🤖\n\n"
                "⚠️ <b>Error generating status</b>\n"
                "There was a problem retrieving the current game status.\n\n"
                f"⏰ <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
            )

# Global monitor instance
resource_monitor = ResourceMonitor()

def get_system_health_stats() -> Dict[str, Any]:
    """Get comprehensive system health statistics"""
    try:
        from game.battle_system import active_battles  # Local import to avoid circular import
        from game.explore import user_last_explore
        current_time = datetime.now()
        
        # Count active elements
        active_count = len(active_battles)
        explore_count = len(user_last_explore)
        activity_count = len(live_player_activity)
        
        # Calculate memory usage
        memory = psutil.Process().memory_info()
        memory_mb = round(memory.rss / 1024 / 1024, 2)
        
        # Check for potential issues
        warnings = []
        if active_count > 100:
            warnings.append(f"High active battles: {active_count}")
        if explore_count > 1000:
            warnings.append(f"High explore records: {explore_count}")
        if memory_mb > 500:
            warnings.append(f"High memory usage: {memory_mb}MB")
        
        return {
            "timestamp": current_time.isoformat(),
            "memory_mb": memory_mb,
            "active_battles": active_count,
            "explore_records": explore_count,
            "activity_records": activity_count,
            "warnings": warnings,
            "health_status": "warning" if warnings else "healthy"
        }
    except Exception as e:
        logger.error(f"Error getting system health stats: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "health_status": "error",
            "error": str(e)
        }

async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram command handler for /monitor - sends live status and dashboard link."""
    if not update or not hasattr(update, 'message'):
        logger.error(f"Invalid update object in monitor_command")
        return
        
    try:
        # Try to get formatted status with error handling
        try:
            status = resource_monitor.get_formatted_live_status()
        except Exception as e:
            logger.error(f"Error getting formatted status: {e}")
            import traceback
            logger.error(traceback.format_exc())
            status = "⚠️ <b>Error fetching live status</b>\n"
            status += "There was a problem getting the current game status."
            
        # Use the same domain as where the bot is hosted
        dashboard_url = "https://attackontitangamebot.onrender.com/dashboard"
        
        # Make dashboard link more prominent
        status += f"\n\n<b>🔗 Live Dashboard:</b> <a href='{dashboard_url}'>Click here to access the Dashboard</a>"
        
        await update.message.reply_text(status, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error in /monitor command: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Try to send a minimal response
        try:
            dashboard_url = "https://attackontitangamebot.onrender.com/dashboard"
            error_msg = f"❌ <b>Error:</b> Failed to fetch live monitor status.\n\n<b>🔗 Dashboard:</b> <a href='{dashboard_url}'>Access the Dashboard here</a>"
            await update.message.reply_text(error_msg, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as send_error:
            logger.error(f"Failed to send error message: {send_error}")
