"""
Memory cleanup utilities to prevent memory leaks from unbounded dictionaries.
This module provides periodic cleanup for all global caches and tracking dicts.
"""

import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

async def run_global_memory_cleanup():
    """
    Run all memory cleanup tasks periodically.
    This should be called every 5 minutes from the main application.
    """
    try:
        # Clean dashboard sessions and verification codes
        from utils.fastapi_dashboard import (
            cleanup_expired_sessions, 
            cleanup_expired_verification_codes,
            active_sessions,
            verification_codes
        )
        sessions_cleaned = cleanup_expired_sessions()
        codes_cleaned = cleanup_expired_verification_codes()
        
        # Clean monitor activity
        from utils.monitor import cleanup_stale_activity, live_player_activity
        activity_cleaned = cleanup_stale_activity(max_age_minutes=10)
        
        # Clean explore caches
        from game.explore import (
            cleanup_cache,
            cleanup_locks,
            cleanup_timeout_tasks,
            user_cache,
            user_explore_locks,
            user_timeout_tasks
        )
        cleanup_cache()
        cleanup_locks()
        cleanup_timeout_tasks()
        
        # Clean mission command cooldowns
        try:
            from game.missions_command import cleanup_mission_cooldowns, mission_command_cooldowns
            mission_cleaned = cleanup_mission_cooldowns()
        except ImportError:
            mission_cleaned = 0
        
        # Clean PVP button cooldowns
        try:
            from game.pvp_system import cleanup_pvp_cooldowns, pvp_button_cooldowns
            pvp_cleaned = cleanup_pvp_cooldowns()
        except ImportError:
            pvp_cleaned = 0
        
        total_cleaned = sessions_cleaned + codes_cleaned + activity_cleaned + mission_cleaned + pvp_cleaned
        
        if total_cleaned > 0:
            logger.info(
                f"🧹 Memory cleanup: sessions={sessions_cleaned}, "
                f"codes={codes_cleaned}, activity={activity_cleaned}, "
                f"missions={mission_cleaned}, pvp={pvp_cleaned}"
            )
            logger.debug(
                f"Memory snapshot: "
                f"active_sessions={len(active_sessions)}, "
                f"verification_codes={len(verification_codes)}, "
                f"live_activity={len(live_player_activity)}, "
                f"user_cache={len(user_cache)}, "
                f"explore_locks={len(user_explore_locks)}, "
                f"timeout_tasks={len(user_timeout_tasks)}, "
                f"mission_cooldowns={len(mission_command_cooldowns)}, "
                f"pvp_cooldowns={len(pvp_button_cooldowns)}"
            )
        
        return total_cleaned
        
    except ImportError as e:
        logger.warning(f"Could not import cleanup modules: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error during memory cleanup: {e}", exc_info=True)
        return 0

async def start_memory_cleanup_scheduler():
    """
    Start a background task that runs memory cleanup every 5 minutes.
    This should be called during application startup.
    """
    try:
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                await run_global_memory_cleanup()
            except asyncio.CancelledError:
                logger.info("Memory cleanup scheduler stopped")
                break
            except Exception as e:
                logger.error(f"Memory cleanup scheduler error: {e}", exc_info=True)
                await asyncio.sleep(10)  # Wait before retry
    except asyncio.CancelledError:
        logger.debug("Memory cleanup scheduler cancelled")

def schedule_memory_cleanup(application):
    """
    Schedule memory cleanup to run periodically.
    Call this from main.py after application is initialized.
    """
    try:
        cleanup_task = asyncio.create_task(start_memory_cleanup_scheduler())
        return cleanup_task
    except Exception as e:
        logger.error(f"Failed to schedule memory cleanup: {e}")
        return None
