import os
import random
import datetime
import time
import asyncio
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from database.models import Character, Player, Titan, Equipment, CharacterStats, generate_titan_name, generate_titan_hp, generate_titan_xp
from database.schemas import Ability
from database.characters import get_character_data
from database.db_instance import get_database
import logging
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from collections import OrderedDict

# ===== BOUNDED CACHE IMPLEMENTATION =====
class BoundedCache:
    """Memory-efficient bounded cache with LRU eviction"""
    def __init__(self, max_size: int = 256):
        self.max_size = max_size
        self.cache = OrderedDict()
    
    def get(self, key: str):
        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    
    def set(self, key: str, value):
        # FIX: Check size BEFORE adding to prevent overshoot
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            # Evict oldest BEFORE adding if at capacity
            if len(self.cache) >= self.max_size:
                oldest_key = next(iter(self.cache))
                self.cache.pop(oldest_key)
        
        self.cache[key] = value
    
    def clear(self):
        self.cache.clear()
    
    def size(self):
        return len(self.cache)
    
    def cleanup_expired(self, current_time: float, ttl: int) -> int:
        """Remove expired entries and return count removed"""
        expired_keys = [
            k for k, v in self.cache.items()
            if current_time - v.get("timestamp", 0) > ttl
        ]
        
        for key in expired_keys:
            self.cache.pop(key, None)
        
        return len(expired_keys)

# Use bounded caches instead of unbounded dictionaries
PLAYER_CACHE = BoundedCache(max_size=128)
CHARACTER_CACHE = BoundedCache(max_size=256)
PLAYER_CACHE_TTL = 600  
CHARACTER_CACHE_TTL = 600  

# FIX: Add TTL tracking for titan cache instead of unbounded dict
_titan_cache = {}
_titan_cache_expiry = {}
TITAN_CACHE_TTL = 300  
MAX_TITAN_CACHE_SIZE = 100  

# FIX: Track background tasks for cleanup
_background_tasks = set()

PLAYER_CACHE_LOCK = asyncio.Lock()
CACHE_ENABLED = True

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def sanitize_player_data(player_data: Dict[str, Any]) -> Dict[str, Any]:
    if 'daily_explores' in player_data:
        if not isinstance(player_data['daily_explores'], dict):
            logger.warning(f"Corrupted daily_explores found for user {player_data.get('user_id', 'unknown')}. Resetting to {{}}.")
            player_data['daily_explores'] = {}
        else:
            sanitized_explores = {}
            for date_str, count in player_data['daily_explores'].items():
                if isinstance(count, dict):
                    if 'count' in count:
                        count = count['count']
                    else:
                        logger.warning(f"Invalid daily_explores entry format for user {player_data.get('user_id', 'unknown')}: {count}")
                        continue
                
                if isinstance(date_str, str) and isinstance(count, int) and count >= 0:
                    sanitized_explores[date_str] = count
                else:
                    logger.warning(f"Invalid daily_explores entry for user {player_data.get('user_id', 'unknown')}: date_str={date_str}, count={count}")
            player_data['daily_explores'] = sanitized_explores
    return player_data

# FIX: Add cleanup functions
def _cleanup_titan_cache():
    """Remove expired titans from cache"""
    current_time = time.time()
    expired_users = [
        user_id for user_id, expiry_time in _titan_cache_expiry.items()
        if current_time > expiry_time
    ]
    
    for user_id in expired_users:
        _titan_cache.pop(user_id, None)
        _titan_cache_expiry.pop(user_id, None)
    
    if expired_users:
        logger.debug(f"Cleaned {len(expired_users)} expired titan cache entries")
    
    return len(expired_users)

def _cleanup_background_tasks():
    """Remove completed background tasks"""
    completed = [task for task in _background_tasks if task.done()]
    for task in completed:
        _background_tasks.discard(task)
    
    if completed:
        logger.debug(f"Cleaned {len(completed)} completed background tasks")
    
    return len(completed)

async def _periodic_cache_cleanup():
    """Run periodically to clean all caches (should be called from main app)"""
    current_time = time.time()
    
    # Clean titan cache
    titan_cleaned = _cleanup_titan_cache()
    
    # Clean player cache
    player_cleaned = PLAYER_CACHE.cleanup_expired(current_time, PLAYER_CACHE_TTL)
    
    # Clean character cache
    char_cleaned = CHARACTER_CACHE.cleanup_expired(current_time, CHARACTER_CACHE_TTL)
    
    # Clean background tasks
    task_cleaned = _cleanup_background_tasks()
    
    if titan_cleaned + player_cleaned + char_cleaned + task_cleaned > 0:
        logger.info(
            f"Cache cleanup: titans={titan_cleaned}, "
            f"players={player_cleaned}, chars={char_cleaned}, tasks={task_cleaned}"
        )

class Database:
    def __init__(self):
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.characters = None
        self.players = None
        self.titans = None
        self.equipment = None
        self.shop_purchases = None
        self.shop_purchases_collection = None
        self.bank_accounts = None
        self.bans = None
        self.groups = None
        self.stats = None

    async def init_db(self, db: AsyncIOMotorDatabase):
        """Initialize database collections"""
        self.db = db
        self.characters = db.characters
        self.players = db.players
        self.titans = db.titans
        self.equipment = db.equipment
        self.shop_purchases = db.shop_purchases
        self.shop_purchases_collection = db.shop_purchases
        self.bank_accounts = db.bank_accounts
        self.bans = db.bans
        self.groups = db.groups
        self.stats = db.stats
        logger.info("Database collections initialized successfully")
        logger.info("Caches will be populated on-demand for optimal memory usage")
        
        # FIX: Start periodic cleanup task
        asyncio.create_task(self._run_periodic_cleanup())
    
    async def _run_periodic_cleanup(self):
        """Background task to clean caches every 5 minutes"""
        try:
            while True:
                await asyncio.sleep(300)  # Every 5 minutes
                await _periodic_cache_cleanup()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")
    
    async def prewarm_caches(self):
        """Pre-warm caches - already done in init_db, this method exists for compatibility"""
        pass
    
    async def _prewarm_character_cache(self, user_id: str, character_name: str):
        """Pre-warm a specific character cache"""
        try:
            cache_key = f"character_{user_id}_{character_name}"
            cached_data = CHARACTER_CACHE.get(cache_key)
            if not cached_data:
                character_data = await self.characters.find_one({
                    "user_id": str(user_id),
                    "name": character_name
                }, {
                    "user_id": 1, "name": 1, "character_type": 1, "current_hp": 1, "level": 1,
                    "xp": 1, "gas": 1, "equipped_weapon": 1, "stats": 1, "max_gas": 1
                })
                
                if character_data:
                    character = Character(**character_data)
                    CHARACTER_CACHE.set(cache_key, {
                        "character": character,
                        "timestamp": time.time()
                    })
        except Exception as e:
            logger.debug(f"Error pre-warming character {character_name}: {e}")

    # --- Banking System Methods ---
    async def get_bank_account(self, user_id: str):
        try:
            doc = await self.bank_accounts.find_one({"user_id": user_id})
            from database.models import BankAccount
            return BankAccount(**doc) if doc else None
        except Exception as e:
            logger.error(f"Failed to get bank account: {e}")
            return None

    async def save_bank_account(self, account):
        try:
            account_dict = account.dict() if hasattr(account, 'dict') else account
            account_dict["updated_at"] = datetime.now(timezone.utc)
            await self.bank_accounts.update_one(
                {"user_id": account.user_id},
                {"$set": account_dict},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Failed to save bank account: {e}")

    async def get_all_bank_accounts(self):
        try:
            cursor = self.bank_accounts.find({})
            docs = await cursor.to_list(None)
            from database.models import BankAccount
            return [BankAccount(**doc) for doc in docs]
        except Exception as e:
            logger.error(f"Failed to get all bank accounts: {e}")
            return []

    # Player operations
    async def create_player(self, user_id: str, username: str, name: str, referral_code: str = None, referred_by: str = None) -> Player:
        try:
            import time
            start = time.perf_counter()
            if not referral_code:
                referral_code = str(user_id)
            player = Player(
                user_id=str(user_id),
                username=username,
                name=name,
                level=1,
                xp=0,
                total_xp=0,
                gas=1000,
                valor=0,
                crystal=0,
                marks=0,
                explore_count=0,
                owned_characters=[],
                team=[],
                referral_code=referral_code,
                referred_by=referred_by,
                referral_count=0,
                referral_milestones={}
            )
            logger.info(f"Creating player: {player}")
            await self.players.insert_one(player.dict())
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"create_player query time: {elapsed:.2f} ms")
            return player
        except Exception as e:
            logger.error(f"Failed to create player: {e}")
            raise

    async def get_player(self, user_id: str) -> Optional[Player]:
        try:
            import time
            start = time.perf_counter()

            # Check cache first if enabled
            if CACHE_ENABLED:
                cache_key = f"player_{user_id}"
                current_time = time.time()
                
                # Get from cache if available and not expired
                cached_data = PLAYER_CACHE.get(cache_key)
                if cached_data and current_time - cached_data["timestamp"] < PLAYER_CACHE_TTL:
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.debug(f"get_player cache hit, time: {elapsed:.2f} ms")
                    return cached_data["player"]
            
            # Not in cache or expired, query database
            if self.players is None:
                raise ConnectionError("Database not initialized. Call init_db() first.")

            player_data = await self.players.find_one({"user_id": user_id}, {
                "user_id": 1, "username": 1, "name": 1, "level": 1, "xp": 1, "total_xp": 1,
                "gas": 1, "crystal": 1, "valor": 1, "marks": 1, "explore_count": 1,
                "owned_characters": 1, "location": 1, "travel": 1, "daily_explores": 1,
                "unlocked_areas": 1, "team": 1, "shop_refresh_date": 1, "shop_refresh_count": 1,
                "hcaptcha_verified": 1, "hcaptcha_start_time": 1, "explore_start_time": 1, "last_explore_time": 1,
                "inventory": 1, "referral_code": 1, "referred_by": 1, "referral_count": 1, "referral_milestones": 1,
                "missions": 1, "pvp_wins": 1, "pvp_losses": 1, "battle_rating": 1,
                "pvp_matches": 1, "tax_history": 1, "guild_id": 1, "daily_streak": 1, "last_daily_claim": 1,
                "double_exp_end": 1, "completed_quests": 1, "mission14_area_counts": 1, "created_at": 1, "updated_at": 1
            })
            
            elapsed = (time.perf_counter() - start) * 1000
            logger.debug(f"get_player db query time: {elapsed:.2f} ms")
            
            # Create a Player object
            player = None
            if player_data:
                # Sanitize player data before creating Player object
                player_data = sanitize_player_data(player_data)

                # Convert team members to proper TeamMember objects if present
                if "team" in player_data and player_data["team"]:
                    from database.models import TeamMember
                    player_data["team"] = [
                        TeamMember(**member) if isinstance(member, dict) else member
                        for member in player_data["team"]
                    ]
                player = Player(**player_data)
                
                # Cache the player if enabled
                if CACHE_ENABLED:
                    cache_key = f"player_{user_id}"
                    PLAYER_CACHE.set(cache_key, {
                        "player": player,
                        "timestamp": time.time()
                    })
                    
            return player
        except (PyMongoError, ConnectionError) as e:
            logger.error(f"Failed to get player: {e}")
            raise

    async def update_player(self, user_id: str, update_data: Dict) -> Optional[Player]:
        try:
            import time
            start = time.perf_counter()
            
            # Ensure XP and total_xp are never negative
            if 'xp' in update_data:
                update_data['xp'] = max(0, update_data['xp'])
            if 'total_xp' in update_data:
                update_data['total_xp'] = max(0, update_data['total_xp'])
            if 'team' in update_data:
                update_data['team'] = [
                    {"character_name": member.character_name, "position": member.position} 
                    if hasattr(member, 'character_name') else 
                    (member.dict() if hasattr(member, 'dict') else member)
                    for member in update_data['team']
                ]
            
            # Check if this is a time-tracking update that can be non-critical
            is_time_tracking_update = len(update_data) == 1 and (
                'last_explore_time' in update_data
            )
            
            # For time tracking updates, use faster update path
            if is_time_tracking_update:
                update_data["updated_at"] = datetime.now(timezone.utc)
                
                # Update cache immediately if it exists for instant access
                if CACHE_ENABLED:
                    cache_key = f"player_{user_id}"
                    cached_data = PLAYER_CACHE.get(cache_key)
                    if cached_data:
                        player = cached_data["player"]
                        for key, value in update_data.items():
                            setattr(player, key, value)
                        PLAYER_CACHE.set(cache_key, {
                            "player": player,
                            "timestamp": time.time()
                        })
                
                await self.players.update_one(
                    {"user_id": str(user_id)},
                    {"$set": update_data}
                )
                
                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(f"update_player (time update): {elapsed:.2f} ms")
                
                if CACHE_ENABLED:
                    cache_key = f"player_{user_id}"
                    cached_data = PLAYER_CACHE.get(cache_key)
                    if cached_data:
                        return cached_data["player"]
                else:
                    return await self.get_player(str(user_id))
            
            # Check if this is a small update
            is_minor_update = len(update_data) <= 2 and all(key in [
                'explore_start_time', 'hcaptcha_start_time', 'hcaptcha_verified',
                'last_explore_time', 'location', 'travel'
            ] for key in update_data.keys())
            
            # For minor updates
            if is_minor_update:
                update_data["updated_at"] = datetime.now(timezone.utc)
                await self.players.update_one(
                    {"user_id": str(user_id)},
                    {"$set": update_data}
                )
                
                if CACHE_ENABLED:
                    cache_key = f"player_{user_id}"
                    cached_data = PLAYER_CACHE.get(cache_key)
                    if cached_data:
                        if "missions" in update_data:
                            fresh_player_data = await self.players.find_one({"user_id": str(user_id)})
                            if fresh_player_data:
                                fresh_player = Player(**fresh_player_data)
                                PLAYER_CACHE.set(cache_key, {
                                    "player": fresh_player,
                                    "timestamp": time.time()
                                })
                        else:
                            player = cached_data["player"]
                            for key, value in update_data.items():
                                if hasattr(player, key):
                                    setattr(player, key, value)
                            PLAYER_CACHE.set(cache_key, {
                                "player": player,
                                "timestamp": time.time()
                            })
                
                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(f"update_player (minor): {elapsed:.2f} ms")
                return await self.get_player(str(user_id))
            else:
                # For major updates
                update_data["updated_at"] = datetime.now(timezone.utc)
                result = await self.players.find_one_and_update(
                    {"user_id": str(user_id)},
                    {"$set": update_data},
                    return_document=True
                )
                
                # Update cache
                if CACHE_ENABLED and result:
                    player = Player(**result)
                    cache_key = f"player_{user_id}"
                    PLAYER_CACHE.set(cache_key, {
                        "player": player,
                        "timestamp": time.time()
                    })
                    
                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(f"update_player (major): {elapsed:.2f} ms")
                return Player(**result) if result else None
                
        except Exception as e:
            logger.error(f"Failed to update player: {e}")
            raise
            
    def invalidate_character_cache(self, user_id: str, character_name: str):
        if CACHE_ENABLED:
            cache_key = f"character_{user_id}_{character_name}"
            if cache_key in CHARACTER_CACHE.cache:
                CHARACTER_CACHE.cache.pop(cache_key, None)
                logger.debug(f"Invalidated character cache for {character_name}")
                return True
        return False
        
    def invalidate_player_cache(self, user_id: str):
        """Invalidate player cache for a specific user"""
        if CACHE_ENABLED:
            cache_key = f"player_{user_id}"
            if cache_key in PLAYER_CACHE.cache:
                PLAYER_CACHE.cache.pop(cache_key, None)
                logger.debug(f"Invalidated player cache for user {user_id}")
                return True
        return False

    def invalidate_all_character_caches(self, user_id: str):
        """Invalidate all character caches for a specific user"""
        if CACHE_ENABLED:
            keys_to_remove = []
            for cache_key in list(CHARACTER_CACHE.cache.keys()):
                if cache_key.startswith(f"character_{user_id}_"):
                    keys_to_remove.append(cache_key)

            for cache_key in keys_to_remove:
                CHARACTER_CACHE.cache.pop(cache_key, None)
                logger.debug(f"Invalidated character cache: {cache_key}")

            if keys_to_remove:
                logger.debug(f"Cleared {len(keys_to_remove)} character cache entries for user {user_id}")
            return len(keys_to_remove) > 0
        return False

    def invalidate_battle_caches(self, user_id: str):
        if not CACHE_ENABLED:
            return False

        # Clear all character caches for this user
        keys_to_remove = []
        for cache_key in list(CHARACTER_CACHE.cache.keys()):
            if cache_key.startswith(f"character_{user_id}_"):
                keys_to_remove.append(cache_key)

        for cache_key in keys_to_remove:
            CHARACTER_CACHE.cache.pop(cache_key, None)

        # Clear player cache for this user
        player_cache_key = f"player_{user_id}"
        if player_cache_key in PLAYER_CACHE.cache:
            PLAYER_CACHE.cache.pop(player_cache_key, None)

        # Clear titan cache for this user
        self.invalidate_titan_cache(user_id)

        if len(keys_to_remove) > 0:
            logger.debug(f"Cleared {len(keys_to_remove) + 2} battle-related cache entries for user {user_id}")

        return len(keys_to_remove) > 0
        
    def invalidate_titan_cache(self, user_id: str):
        """FIX: Also remove from expiry dict"""
        if user_id in _titan_cache:
            del _titan_cache[user_id]
            _titan_cache_expiry.pop(user_id, None)
            logger.debug(f"Invalidated titan cache for user {user_id}")
            return True
        return False

    async def save_player(self, player: Player) -> Optional[Player]:
        """Save (update) the player document in the database."""
        try:
            player.updated_at = datetime.now(timezone.utc)
            player_dict = player.dict() if hasattr(player, 'dict') else player.__dict__
            
            if 'team' in player_dict and player_dict['team']:
                player_dict['team'] = [
                    {"character_name": member.character_name, "position": member.position} 
                    if hasattr(member, 'character_name') else 
                    (member if isinstance(member, dict) else member)
                    for member in player_dict['team']
                ]
                
            result = await self.players.find_one_and_update(
                {"user_id": str(player.user_id)},
                {"$set": player_dict},
                return_document=True
            )
            return Player(**result) if result else None
        except Exception as e:
            logger.error(f"Failed to save player: {e}")
            raise

    async def get_player_characters(self, user_id: str) -> List[Character]:
        try:
            cursor = self.characters.find({"user_id": str(user_id)})
            characters = await cursor.to_list(None)
            return [Character(**char) for char in characters]
        except Exception as e:
            logger.error(f"Failed to get player characters: {e}")
            raise

    async def add_character_to_player(self, user_id: str, character_name: str) -> bool:
        try:
            result = await self.players.update_one(
                {"user_id": str(user_id)},
                {"$addToSet": {"owned_characters": character_name}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to add character to player: {e}")
            raise

    async def delete_player(self, user_id: str):
        """Delete a player by user_id"""
        try:
            await self.players.delete_one({"user_id": str(user_id)})
            logger.info(f"Deleted player with user_id: {user_id}")
        except Exception as e:
            logger.error(f"Failed to delete player: {e}")
            raise
            
    async def get_all_players(self) -> List[Player]:
        """Get all players from the database"""
        try:
            import time
            start = time.perf_counter()
            cursor = self.players.find({})
            players_data = await cursor.to_list(None)
            
            from database.models import TeamMember
            players = []
            for player_data in players_data:
                player_data = sanitize_player_data(player_data)
                
                if "team" in player_data and player_data["team"]:
                    player_data["team"] = [
                        TeamMember(**member) if isinstance(member, dict) else member
                        for member in player_data["team"]
                    ]
                try:
                    players.append(Player(**player_data))
                except Exception as e:
                    logger.error(f"Failed to create Player object for user {player_data.get('user_id', 'unknown')}: {e}")
                    continue
            
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"get_all_players query time: {elapsed:.2f} ms")
            return players
        except Exception as e:
            logger.error(f"Failed to get all players: {e}")
            return []

    # Character operations
    async def create_character(self, user_id: str, name: str, character_type: str, current_hp: int) -> Character:
        """Create a new character"""
        try:
            char_data = get_character_data(character_type)
            if not char_data:
                raise ValueError(f"Character type {character_type} not found")
            
            stats_obj = char_data.base_stats
            if hasattr(stats_obj, 'dict'):
                stats_dict = stats_obj.dict()
            else:
                stats_dict = stats_obj
            
            character = Character(
                user_id=user_id,
                name=name,
                character_type=character_type,
                current_hp=current_hp,
                level=1,
                xp=0,
                total_xp=0,
                stats=stats_dict, 
                gas=5000,
                max_gas=5000,
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={}
            )
            character.unlock_abilities()
            character_dict = character.dict()
            
            for ability_type in ['active_abilities', 'passive_abilities', 'ultimate_abilities']:
                if ability_type in character_dict:
                    for ability in character_dict[ability_type]:
                        if 'unlocked' not in ability:
                            ability['unlocked'] = ability.get('is_unlocked', False)
                        if 'is_unlocked' not in ability:
                            ability['is_unlocked'] = ability.get('unlocked', False)
            
            await self.characters.insert_one(character_dict)
            await self.add_character_to_player(user_id, name)
            return character
        except (PyMongoError, ValueError) as e:
            logger.error(f"Failed to create character: {e}")
            raise

    async def get_character(self, user_id: str, character_name: str) -> Optional[Character]:
        try:
            cache_key = f"character_{user_id}_{character_name}"
            current_time = time.time()
            
            if CACHE_ENABLED:
                cached_data = CHARACTER_CACHE.get(cache_key)
                if cached_data and current_time - cached_data["timestamp"] < CHARACTER_CACHE_TTL:
                    logger.debug(f"get_character cache hit for {character_name}")
                    return cached_data["character"]
            
            character_data = await self.characters.find_one({
                "user_id": str(user_id),
                "name": character_name
            }, {
                "user_id": 1, "name": 1, "character_type": 1, "current_hp": 1, "level": 1,
                "xp": 1, "gas": 1, "equipped_weapon": 1, "stats": 1, "max_gas": 1
            })
            
            if not character_data:
                character_data = await self.characters.find_one({
                    "user_id": str(user_id),
                    "name": {"$regex": f"^{character_name}$", "$options": "i"}
                }, {
                    "user_id": 1, "name": 1, "character_type": 1, "current_hp": 1, "level": 1,
                    "xp": 1, "gas": 1, "equipped_weapon": 1, "stats": 1, "max_gas": 1
                })
            
            if character_data:
                character = Character(**character_data)
                
                if CACHE_ENABLED:
                    CHARACTER_CACHE.set(cache_key, {
                        "character": character,
                        "timestamp": time.time()
                    })
                return character
            return None
        except Exception as e:
            logger.error(f"Failed to get character: {e}", exc_info=True)
            raise

    async def update_character(self, character: Character) -> Character:
        try:
            import time
            start = time.perf_counter()

            character.updated_at = datetime.now(timezone.utc)
            character_dict = character.dict()

            if 'passive_abilities' in character_dict:
                for ability in character_dict['passive_abilities']:
                    ability['unlocked'] = ability.get('is_unlocked', False)

            is_critical_update = any(key in character_dict for key in [
                'current_hp', 'gas', 'level', 'xp'
            ])

            if is_critical_update:
                result = await self.characters.find_one_and_update(
                    {
                        "user_id": character.user_id,
                        "name": character.name
                    },
                    {"$set": character_dict},
                    return_document=True
                )

                if CACHE_ENABLED and result:
                    cache_key = f"character_{character.user_id}_{character.name}"
                    CHARACTER_CACHE.set(cache_key, {
                        "character": Character(**result),
                        "timestamp": time.time()
                    })

                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(f"update_character (critical): {elapsed:.2f} ms")
                return Character(**result) if result else character
            else:
                await self.characters.update_one(
                    {
                        "user_id": character.user_id,
                        "name": character.name
                    },
                    {"$set": character_dict}
                )

                if CACHE_ENABLED:
                    cache_key = f"character_{character.user_id}_{character.name}"
                    CHARACTER_CACHE.set(cache_key, {
                        "character": character,
                        "timestamp": time.time()
                    })

                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(f"update_character (non-critical): {elapsed:.2f} ms")
                return character

        except Exception as e:
            logger.error(f"Failed to update character: {e}")
            raise

    async def batch_update_character(self, user_id: str, character_name: str, update_data: Dict) -> bool:
        """Batch update multiple character fields at once for better performance"""
        try:
            import time
            start = time.perf_counter()

            update_data["updated_at"] = datetime.now(timezone.utc)

            result = await self.characters.update_one(
                {"user_id": user_id, "name": character_name},
                {"$set": update_data}
            )

            if CACHE_ENABLED:
                cache_key = f"character_{user_id}_{character_name}"
                cached_data = CHARACTER_CACHE.get(cache_key)
                if cached_data:
                    cached_character = cached_data["character"]
                    for key, value in update_data.items():
                        if hasattr(cached_character, key):
                            setattr(cached_character, key, value)
                    CHARACTER_CACHE.set(cache_key, {
                        "character": cached_character,
                        "timestamp": time.time()
                    })

            elapsed = (time.perf_counter() - start) * 1000
            logger.debug(f"batch_update_character: {elapsed:.2f} ms")
            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Failed to batch update character: {e}")
            return False

    async def batch_update_player(self, user_id: str, update_data: Dict) -> bool:
        """Batch update multiple player fields at once for better performance"""
        try:
            import time
            start = time.perf_counter()

            update_data["updated_at"] = datetime.now(timezone.utc)
            
            serialized_update = {}
            for key, value in update_data.items():
                if isinstance(value, dict):
                    if key == "daily_explores":
                        serialized_update[key] = value
                    else:
                        serialized_dict = {}
                        for k, v in value.items():
                            if hasattr(v, 'dict'):
                                serialized_dict[k] = v.dict()
                            else:
                                serialized_dict[k] = v
                        serialized_update[key] = serialized_dict
                elif isinstance(value, list):
                    serialized_list = []
                    for item in value:
                        if hasattr(item, 'dict'):
                            serialized_list.append(item.dict())
                        elif isinstance(item, dict):
                            serialized_list.append(item)
                        else:
                            serialized_list.append(item)
                    serialized_update[key] = serialized_list
                elif hasattr(value, 'dict'):
                    serialized_update[key] = value.dict()
                else:
                    serialized_update[key] = value

            result = await self.players.update_one(
                {"user_id": str(user_id)},
                {"$set": serialized_update},
                upsert=False  
            )

            if CACHE_ENABLED:
                cache_key = f"player_{user_id}"
                cached_data = PLAYER_CACHE.get(cache_key)
                if cached_data:
                    cached_player = cached_data["player"]
                    for key, value in update_data.items():
                        if hasattr(cached_player, key):
                            setattr(cached_player, key, value)
                    PLAYER_CACHE.set(cache_key, {
                        "player": cached_player,
                        "timestamp": time.time()
                    })

            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > 250:
                logger.warning(f"batch_update_player: {elapsed:.2f} ms")
            else:
                logger.debug(f"batch_update_player: {elapsed:.2f} ms")
            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Failed to batch update player: {e}")
            return False

    async def get_character_abilities(self, user_id: str, character_name: str) -> Dict[str, List[Ability]]:
        try:
            character = await self.get_character(str(user_id), character_name)
            if not character:
                return {"active": [], "passive": [], "ultimate": []}
            return {
                "active": character.active_abilities,
                "passive": character.passive_abilities,
                "ultimate": character.ultimate_abilities
            }
        except Exception as e:
            logger.error(f"Failed to get character abilities: {e}")
            raise

    async def get_character_fresh(self, user_id: str, character_name: str) -> Optional[Character]:
        """Get character directly from database, bypassing cache for critical operations"""
        try:
            character_data = await self.characters.find_one({
                "user_id": str(user_id),
                "name": character_name
            }, {
                "user_id": 1, "name": 1, "character_type": 1, "current_hp": 1, "level": 1,
                "xp": 1, "gas": 1, "equipped_weapon": 1, "stats": 1, "max_gas": 1
            })
            
            if character_data:
                character = Character(**character_data)
                
                if CACHE_ENABLED:
                    cache_key = f"character_{user_id}_{character_name}"
                    CHARACTER_CACHE.set(cache_key, {
                        "character": character,
                        "timestamp": time.time()
                    })
                return character
            return None
        except Exception as e:
            logger.error(f"Failed to get fresh character: {e}")
            raise

    async def generate_titan(self, player_level: int, unlocked_areas: List[str], user_id: str = None) -> Optional[Titan]:
        if player_level < 8:
            difficulty = "Easy"
        elif player_level < 15:
            difficulty = "Normal"
        else:
            difficulty = "Hard"

        level = max(1, player_level + random.randint(-2, 2))

        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(level, difficulty)
        abilities = []
        now = datetime.now(timezone.utc)
        titan = Titan(
            name=name,
            level=level,
            max_hp=max_hp,
            abilities=abilities,
            created_at=now,
            difficulty=difficulty,
            spawn_areas=unlocked_areas or [],
            drop_table={},
            xp_reward=generate_titan_xp(level, difficulty),
            min_level_requirement=level
        )
        
        if user_id and user_id in _titan_cache:
            cached_titan = _titan_cache[user_id]
            if cached_titan.name == titan.name:
                name = generate_titan_name(difficulty)
                attempts = 0
                while name == cached_titan.name and attempts < 3:
                    name = generate_titan_name(difficulty)
                    attempts += 1
                titan.name = name
                
        return titan
        
    async def generate_multiple_titans(self, player_level: int, unlocked_areas: List[str], count: int = 3) -> List[Titan]:
        """Generate multiple titans at once for better performance"""
        titans = []
        try:
            if player_level < 8:
                difficulty = "Easy"
            elif player_level < 15:
                difficulty = "Normal"
            else:
                difficulty = "Hard"
                
            now = datetime.now(timezone.utc)
            
            for _ in range(count):
                level = max(1, player_level + random.randint(-2, 2))
                
                name = generate_titan_name(difficulty)
                max_hp = generate_titan_hp(level, difficulty)
                
                titan = Titan(
                    name=name,
                    level=level,
                    max_hp=max_hp,
                    abilities=[],
                    created_at=now,
                    difficulty=difficulty,
                    spawn_areas=unlocked_areas or [],
                    drop_table={},
                    xp_reward=generate_titan_xp(level, difficulty),
                    min_level_requirement=level
                )
                titans.append(titan)
        except Exception as e:
            logger.error(f"Error generating multiple titans: {e}")
            
        return titans

    async def create_new_titan(self, level: int, difficulty: str, spawn_areas: List[str]) -> Titan:
        """Create a new titan with specified parameters"""
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(level, difficulty)
        xp_reward = generate_titan_xp(level, difficulty)
        now = datetime.now(timezone.utc)
        
        titan = Titan(
            name=name,
            level=level,
            max_hp=max_hp,
            abilities=[],
            created_at=now,
            difficulty=difficulty,
            spawn_areas=spawn_areas,
            drop_table={},
            xp_reward=xp_reward,
            min_level_requirement=level
        )
        return titan
    
    # FIX: Add TTL-aware titan cache methods
    def _update_titan_cache(self, user_id: str, titan: Titan):
        """Update titan cache with TTL tracking"""
        _titan_cache[user_id] = titan
        _titan_cache_expiry[user_id] = time.time() + TITAN_CACHE_TTL
        
        # Enforce max size
        if len(_titan_cache) > MAX_TITAN_CACHE_SIZE:
            # Remove oldest entries
            oldest_key = min(
                (k for k in _titan_cache if k not in ["reserved"]),
                key=lambda k: _titan_cache_expiry.get(k, 0),
                default=None
            )
            if oldest_key:
                _titan_cache.pop(oldest_key, None)
                _titan_cache_expiry.pop(oldest_key, None)
    
    def _get_titan_cache(self, user_id: str) -> Optional[Titan]:
        """Get titan from cache if not expired"""
        if user_id not in _titan_cache:
            return None
        
        current_time = time.time()
        if current_time > _titan_cache_expiry.get(user_id, 0):
            # Expired - remove it
            _titan_cache.pop(user_id, None)
            _titan_cache_expiry.pop(user_id, None)
            return None
        
        return _titan_cache[user_id]
    
    async def store_titan(self, user_id: str, titan: Titan):
        """Optimized titan storage with TTL caching"""
        # FIX: Use TTL-aware cache update
        self._update_titan_cache(user_id, titan)
        
        # Store in database as background task
        task = asyncio.create_task(self._background_store_titan(user_id, titan))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)  # Auto-cleanup on completion
        
    async def _background_store_titan(self, user_id: str, titan: Titan):
        """Background task to store titan data without blocking the main thread"""
        try:
            titan_doc = titan.dict() if hasattr(titan, 'dict') else titan.__dict__
            titan_doc["user_id"] = user_id
            titan_doc["updated_at"] = datetime.now(timezone.utc)
            
            essential_titan_doc = {
                "user_id": titan_doc["user_id"],
                "name": titan_doc["name"],
                "level": titan_doc["level"],
                "max_hp": titan_doc["max_hp"],
                "abilities": titan_doc["abilities"] if "abilities" in titan_doc else [],
                "difficulty": titan_doc["difficulty"],
                "xp_reward": titan_doc["xp_reward"],
                "updated_at": titan_doc["updated_at"],
                "created_at": titan_doc.get("created_at", datetime.now(timezone.utc)),
                "drop_table": titan_doc.get("drop_table", {}),
                "min_level_requirement": titan_doc.get("min_level_requirement", titan_doc["level"]),
                "spawn_areas": titan_doc.get("spawn_areas", ["Trost District"])
            }
            
            await self.titans.update_one(
                {"user_id": user_id},
                {"$set": essential_titan_doc},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Background titan storage failed: {e}")

    async def get_titan(self, user_id: str) -> Optional[Titan]:
        """Get titan with TTL-aware cache"""
        # FIX: Use TTL-aware cache getter
        cached_titan = self._get_titan_cache(user_id)
        if cached_titan:
            return cached_titan
            
        # If not in cache or expired, get from database
        titan_data = await self.titans.find_one(
            {"user_id": user_id},
            {
                "user_id": 1, "name": 1, "level": 1, "max_hp": 1, 
                "abilities": 1, "difficulty": 1, "xp_reward": 1,
                "created_at": 1, "drop_table": 1, "min_level_requirement": 1, "spawn_areas": 1
            }
        )
        
        if titan_data:
            if "abilities" not in titan_data or titan_data["abilities"] is None:
                titan_data["abilities"] = []
            if "created_at" not in titan_data:
                titan_data["created_at"] = datetime.now(timezone.utc)
            if "drop_table" not in titan_data:
                titan_data["drop_table"] = {}
            if "min_level_requirement" not in titan_data:
                titan_data["min_level_requirement"] = titan_data["level"]
            if "spawn_areas" not in titan_data:
                titan_data["spawn_areas"] = ["Trost District"]
                
            titan = Titan(**titan_data)
            self._update_titan_cache(user_id, titan)  # FIX: Use TTL-aware cache update
            return titan
            
        return None

    async def delete_titan(self, user_id: str):
        """Delete titan from DB and cache"""
        await self.titans.delete_one({"user_id": user_id})
        self.invalidate_titan_cache(user_id)

    async def get_random_titan(self, min_level: int, max_level: int, target_level: int, unlocked_areas: Optional[List[str]] = None) -> Titan:
        level = random.randint(min_level, max_level)
        if level >= 15:
            difficulty = "Hard"
        elif level >= 8:
            difficulty = "Normal"
        else:
            difficulty = "Easy"
        if not unlocked_areas:
            unlocked_areas = ["Trost District", "Karanes District", "Shiganshina District"]
        new_titan = await self.create_new_titan(
            level=level,
            difficulty=difficulty,
            spawn_areas=unlocked_areas
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        new_titan.internal_name = f"encounter_{level}_{difficulty.lower()}_{timestamp}"
        await self.titans.update_one(
            {"internal_name": new_titan.internal_name},
            {"$set": new_titan.dict()}
        )
        return new_titan

    async def get_available_titans(self, character_level: int) -> List[Titan]:
        try:
            cursor = self.titans.find({"min_level_requirement": {"$lte": character_level}, "is_template": {"$ne": True}})
            titans = await cursor.to_list(None)
            return [Titan(**titan) for titan in titans]
        except Exception as e:
            logger.error(f"Failed to get available titans: {e}")
            raise

    # Equipment operations
    async def create_equipment(self, equipment: Equipment) -> Equipment:
        try:
            await self.equipment.insert_one(equipment.dict())
            return equipment
        except Exception as e:
            logger.error(f"Failed to create equipment: {e}")
            raise

    async def get_equipment(self, name: str) -> Optional[Equipment]:
        try:
            equipment_data = await self.equipment.find_one({"name": name})
            return Equipment(**equipment_data) if equipment_data else None
        except Exception as e:
            logger.error(f"Failed to get equipment: {e}")
            raise

    async def get_equipment_by_type(self, type: str) -> List[Equipment]:
        try:
            cursor = self.equipment.find({"type": type})
            equipment_list = await cursor.to_list(None)
            return [Equipment(**equip) for equip in equipment_list]
        except Exception as e:
            logger.error(f"Failed to get equipment by type: {e}")
            raise

    async def get_daily_purchases(self, user_id: str, item_key: str) -> int:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.shop_purchases_collection.count_documents({
            "user_id": user_id,
            "item_key": item_key,
            "purchase_date": {"$gte": today}
        })

    async def store_shop_refresh(self, user_id: str, count: int):
        await self.players.update_one(
            {"user_id": user_id},
            {"$set": {"shop_refresh_count": count, "shop_refresh_date": datetime.now(timezone.utc)}}
        )

    async def get_shop_refresh_count(self, user_id: str) -> int:
        player = await self.get_player(user_id)
        if not player or not player.shop_refresh_date:
            return 0
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if player.shop_refresh_date.date() == today.date():
            return player.shop_refresh_count or 0
        return 0

    async def add_new_character_to_player(self, user_id: str, character_name: str) -> bool:
        """Create a new character for a player with proper initialization"""
        try:
            char_data = get_character_data(character_name)
            if not char_data:
                logger.error(f"Character data not found for: {character_name}")
                return False
            
            existing_char = await self.get_character(str(user_id), character_name)
            if existing_char:
                logger.warning(f"Character {character_name} already exists for user {user_id}")
                return False
            
            max_hp = char_data.get_max_hp(1)
            
            new_character = Character(
                user_id=str(user_id),
                name=character_name,
                character_type=character_name,
                current_hp=max_hp,
                level=1,
                xp=0,
                total_xp=0,
                stats=char_data.base_stats.dict(),
                gas=5000,
                max_gas=5000,
                equipped_weapon=None,
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            
            for ability in char_data.active_abilities:
                if ability.level_required <= 1:
                    ability_dict = ability.dict()
                    ability_dict['is_unlocked'] = True
                    ability_dict['unlocked'] = True
                    new_character.active_abilities.append(Ability(**ability_dict))
                    new_character.unlocked_abilities[ability.name] = True
                    
            for ability in char_data.passive_abilities:
                if ability.level_required <= 1:
                    ability_dict = ability.dict()
                    ability_dict['is_unlocked'] = True
                    ability_dict['unlocked'] = True
                    new_character.passive_abilities.append(Ability(**ability_dict))
                    new_character.unlocked_abilities[ability.name] = True
                    
            for ability in char_data.ultimate_abilities:
                if ability.level_required <= 1:
                    ability_dict = ability.dict()
                    ability_dict['is_unlocked'] = True
                    ability_dict['unlocked'] = True
                    new_character.ultimate_abilities.append(Ability(**ability_dict))
                    new_character.unlocked_abilities[ability.name] = True
            
            await self.characters.insert_one(new_character.dict())
            logger.info(f"Successfully created character {character_name} for user {user_id} with {len(new_character.unlocked_abilities)} abilities unlocked")
            
            # CRITICAL: Invalidate cache so subsequent calls fetch fresh data
            self.invalidate_character_cache(user_id, character_name)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to add new character {character_name} to player {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    async def get_connection_stats(self) -> Optional[Dict]:
        try:
            server_info = await self.db.command("serverStatus")
            return {
                "connections": server_info.get("connections", {}),
                "uptime": server_info.get("uptime", 0),
                "opcounters": server_info.get("opcounters", {}),
                "mem": server_info.get("mem", {})
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return None

    async def get_group(self, group_id: int) -> Optional[Dict]:
        """Get group information by ID"""
        try:
            if self.groups is None:
                logger.warning("Groups collection is None in get_group")
                return None
            group = await self.groups.find_one({"group_id": group_id})
            return group
        except Exception as e:
            logger.error(f"Failed to get group {group_id}: {e}")
            return None

    async def update_group(self, group_id: int, update_data: Dict) -> bool:
        """Update group information"""
        try:
            if self.groups is None:
                logger.warning("Groups collection is None in update_group")
                return False
            update_data["updated_at"] = datetime.now(timezone.utc)
            result = await self.groups.update_one(
                {"group_id": group_id},
                {"$set": update_data},
                upsert=True
            )
            return result.modified_count > 0 or result.upserted_id is not None
        except Exception as e:
            logger.error(f"Failed to update group {group_id}: {e}")
            return False

    async def add_ban(self, user_id: str, reason: str, ban_until: Optional[datetime] = None) -> bool:
        """Add or update a ban for a user"""
        try:
            if self.bans is None:
                logger.warning("Bans collection is None in add_ban")
                return False
            
            ban_data = {
                "user_id": str(user_id),
                "reason": reason,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            
            if ban_until:
                ban_data["ban_until"] = ban_until
            
            result = await self.bans.update_one(
                {"user_id": str(user_id)},
                {"$set": ban_data},
                upsert=True
            )
            logger.info(f"Banned user {user_id}: {reason}")
            return result.modified_count > 0 or result.upserted_id is not None
        except Exception as e:
            logger.error(f"Failed to add ban for user {user_id}: {e}")
            return False

    async def get_all_groups(self, filter_data: Optional[Dict] = None) -> List[Dict]:
        """Get all groups, optionally filtered"""
        try:
            if self.groups is None:
                logger.warning("Groups collection is None in get_all_groups")
                return []
            cursor = self.groups.find(filter_data or {})
            groups = []
            async for doc in cursor:
                groups.append(doc)
            return groups
        except Exception as e:
            logger.error(f"Failed to get groups: {e}")
            return []

    async def delete_group(self, group_id: int) -> bool:
        """Delete a group by ID"""
        try:
            if self.groups is None:
                logger.warning("Groups collection is None in delete_group")
                return False
            result = await self.groups.delete_one({"group_id": group_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Failed to delete group {group_id}: {e}")
            return False

    async def record_purchase(self, user_id: str, item_key: str):
        """Record a shop purchase for tracking stock/cooldown"""
        await self.shop_purchases_collection.insert_one({
            "user_id": user_id,
            "item_key": item_key,
            "purchase_date": datetime.now(timezone.utc)
        })

    async def update_character_stats(self, user_id: str, character_name: str, stats: Dict) -> bool:
        """Update a character's stats"""
        try:
            character = await self.get_character(str(user_id), character_name)
            if character:
                character.stats = CharacterStats(**stats)
                await self.update_character(character)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update character stats: {e}")
            return False