import psutil
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from game.explore import user_last_explore  
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Live tracking variables 
live_player_activity = {}  # {user_id: {"name": "username", "action": "exploring", "timestamp": datetime, "details": {}}}
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
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            "rss_mb": round(memory_info.rss / 1024 / 1024, 2),  # Resident Set Size
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2),  # Virtual Memory Size
            "percent": round(process.memory_percent(), 2)
        }
    
    def get_battle_stats(self) -> Dict[str, int]:
        """Get active battle statistics"""
        from game.battle_system import active_battles  # Local import to avoid circular import
        return {
            "active_battles": len(active_battles),
            "memory_per_battle_kb": round(
                (psutil.Process().memory_info().rss / 1024) / max(len(active_battles), 1), 2
            )
        }
    
    def get_system_load(self) -> Dict[str, Any]:
        """Get system CPU and load information"""
        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "load_average": psutil.getloadavg() if hasattr(psutil, 'getloadavg') else [0, 0, 0]
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
        """Get live player activity without DB queries"""
        from game.battle_system import active_battles  # Local import to avoid circular import
        current_time = datetime.now()
        active_players = []
        
        # Get players currently in battles
        for user_id, battle in active_battles.items():
            player_info = live_player_activity.get(user_id, {})
            character_name = battle.character.name if battle.character else "Unknown"
            titan_name = battle.titan.name if battle.titan else "Unknown"
            
            active_players.append({
                "user_id": user_id,
                "username": player_info.get("name", "Unknown"),
                "action": "🔥 In Battle",
                "character": character_name,
                "vs_titan": titan_name,
                "titan_hp": f"{battle.titan_hp}/{battle.titan.max_hp}",
                "char_hp": f"{battle.character_hp}/{battle.character.stats.HP}",
                "turn": battle.turn,
                "gas": battle.gas,
                "duration": (current_time - player_info.get("timestamp", current_time)).total_seconds() if user_id in live_player_activity else 0
            })
        
        # Get other active players (exploring, etc.)
        for user_id, activity in live_player_activity.items():
            if user_id not in active_battles:
                duration = (current_time - activity["timestamp"]).total_seconds()
                # Remove stale activities (older than 5 minutes)
                if duration < 300:
                    active_players.append({
                        "user_id": user_id,
                        "username": activity["name"],
                        "action": activity["action"],
                        "details": activity.get("details", {}),
                        "duration": duration
                    })
        
        return {
            "total_active": len(active_players),
            "in_battle": len(active_battles),
            "exploring": len([p for p in active_players if "exploring" in p.get("action", "").lower()]),
            "ended": len([p for p in active_players if p.get("action", "").startswith("🏁")]),
            "players": active_players
        }
    
    def get_formatted_live_status(self) -> str:
        """Get beautifully formatted live player status"""
        stats = self.get_live_player_stats()
        memory = self.get_memory_usage()
        system = self.get_system_load()
        
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
            for player in stats['players'][:15]:  # Show max 15 players
                duration_str = f"{int(player.get('duration', 0))}s"
                
                if player['action'] == "🔥 In Battle":
                    status += f"   ⚔️ <b>{player['username']}</b> ({player['user_id']})\n"
                    status += f"      🗡️ {player['character']} vs {player['vs_titan']}\n"
                    status += f"      ❤️ {player['char_hp']} | 🛡️ {player['titan_hp']} | Turn {player['turn']}\n"
                    status += f"      ⛽ Gas: {player['gas']} | ⏱️ {duration_str}\n\n"
                else:
                    status += f"   🔸 <b>{player['username']}</b> ({player['user_id']})\n"
                    status += f"      {player['action']} | ⏱️ {duration_str}\n\n"
            
            if len(stats['players']) > 15:
                status += f"   ... and {len(stats['players']) - 15} more players\n"
        else:
            status += f"😴 <i>No active players right now</i>\n"
        
        return status

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
    try:
        status = resource_monitor.get_formatted_live_status()
        dashboard_url = "https://attackontitan-j5yh.onrender.com/dashboard"
        status += f"\n\n<b>🔗 Live Dashboard:</b> <a href='{dashboard_url}'>Open Dashboard</a>"
        await update.message.reply_text(status, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error in /monitor command: {e}")
        await update.message.reply_text("Failed to fetch live monitor status.")
