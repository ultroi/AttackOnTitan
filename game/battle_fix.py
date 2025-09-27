import logging
from typing import Dict, Any, Optional
import asyncio
from datetime import datetime, timezone
from database.models import Character, Player

logger = logging.getLogger(__name__)

class BattleSystemFix:

    @staticmethod
    async def handle_battle_end_fixed(db, battle, user_id, player_obj, update_tasks):
        try:
            if not hasattr(db, 'batch_update_player') or not callable(getattr(db, 'batch_update_player', None)):
                logger.warning("Database object missing batch_update_player method, using fallback")

                # Create a fallback implementation
                async def batch_update_player_fallback(user_id: str, update_data: Dict[str, Any]) -> bool:
                    """
                    Fallback implementation for batch_update_player when the method is missing.
                    """
                    try:
                        update_data["updated_at"] = datetime.now(timezone.utc)

                        result = await db.players.update_one(
                            {"user_id": user_id},
                            {"$set": update_data}
                        )

                        # Update cache if it exists
                        if hasattr(db, 'CACHE_ENABLED') and db.CACHE_ENABLED:
                            cache_key = f"player_{user_id}"
                            if hasattr(db, 'PLAYER_CACHE') and cache_key in db.PLAYER_CACHE:
                                cached_player = db.PLAYER_CACHE[cache_key]["player"]
                                for key, value in update_data.items():
                                    if hasattr(cached_player, key):
                                        setattr(cached_player, key, value)
                                import time
                                db.PLAYER_CACHE[cache_key]["timestamp"] = time.time()

                        return result.modified_count > 0
                    except Exception as e:
                        logger.error(f"Fallback batch_update_player failed: {e}")
                        return False

                # Add the fallback method to db object for this session
                setattr(db, 'batch_update_player', batch_update_player_fallback)

            # Now the function should work as expected
            return True

        except Exception as e:
            logger.error(f"Error in battle_fix: {e}")
            return False

async def apply_battle_fixes(db):
    # Check if batch_update_player exists
    if not hasattr(db, 'batch_update_player'):
        logger.info("Adding batch_update_player method to Database class")

        async def batch_update_player(self, user_id: str, update_data: Dict[str, Any]) -> bool:
            """
            Batch update multiple player fields at once for better performance.
            """
            try:
                import time
                start = time.perf_counter()

                update_data["updated_at"] = datetime.now(timezone.utc)

                result = await self.players.update_one(
                    {"user_id": user_id},
                    {"$set": update_data}
                )

                # Update cache if it exists
                if hasattr(self, 'CACHE_ENABLED') and self.CACHE_ENABLED:
                    cache_key = f"player_{user_id}"
                    if hasattr(self, 'PLAYER_CACHE') and cache_key in self.PLAYER_CACHE:
                        cached_player = self.PLAYER_CACHE[cache_key]["player"]
                        for key, value in update_data.items():
                            if hasattr(cached_player, key):
                                setattr(cached_player, key, value)
                        self.PLAYER_CACHE[cache_key]["timestamp"] = time.time()

                elapsed = (time.perf_counter() - start) * 1000
                logger.info(f"batch_update_player query time: {elapsed:.2f} ms")
                return result.modified_count > 0

            except Exception as e:
                logger.error(f"Failed to batch update player: {e}")
                return False

        # Add the method to the Database class
        setattr(db.__class__, 'batch_update_player', batch_update_player)

        return True
    return False