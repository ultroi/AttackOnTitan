import os
import logging
import asyncio
import signal
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ChatMemberHandler, MessageHandler, filters
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate
from database.db import Database
from database.db_instance import get_persistent_database
from game.map_system import show_map, MAP_IMAGE_URL
from game.scheduler import start_scheduler
from utils.sudo_reset import reset_handler
from utils.ban_utils import ban_protected, ban_user, unban_user
from utils.mod_utils import promote_mod, demote_mod
from utils.maintenance import maintenance_protected, maintenance
from utils.disable_mode import disable_command, enable_command, disable_protected
from utils.diagnostics import diagnostic_db_command, check_group_record
from utils.group import group_update_handler
from utils.monitor import monitor_command
from utils.broadcast import broadcast_command, broadcast_location_callback, end_voting_callback, confirm_broadcast_callback, broadcast_type_callback, vote_options_callback, vote_callback, custom_options_count_callback, collect_custom_option
from utils.extra import buy_command, give_command
from game.explore import explore, close_keyboard, open_keyboard
from game.callback_handlers import button_callback, handle_travel_decision
from game.shop_system import ShopSystem
from game.battle_system import handle_battle_action, active_battles
from utils.scheduled_tasks import start_scheduled_tasks
from game.travel_system import travel_command, handle_travel_direction, handle_cancel_travel
from game.captcha import button
from game.pvp_system import pvp_command, pvp_callback_handler
from game.tax_command import tax_status_command, force_tax_check_command
from game.stats_command import stats_command, start_stats_scheduler
from game.missions_command import missions_command, missions_callback_handler, reset_mission_command, remission_command, reset_mission_callback_handler
from game.start import (
    show_character_selection,
    show_character_details, confirm_character_selection,
    create_character, back_to_selection,
    start_character_selection
)
from game.add_resource_command import add_resource_command
from game.profile_system import (
    profile, char_detail,
    show_team, manage_team, add_to_team, remove_from_team, save_team, clear_team, back_from_manage_team,
    show_inventory, view_weapons, view_gear, view_military, view_utilities, view_echo_shards, view_miscellaneous, referral_info,
    fill_gas, exit_profile, view_weapons_char, equip_weapon, char_detail_callback, view_abilities,
    show_characters
)
from game.item_usage import use_command
from game.spin_system import spin_command, spin_callback_handler
from game.bank_command import handle_bank_command, handle_deposit_command, handle_withdrawal_command, handle_open_bank_callback
from database.models import Character, Player
from pymongo import UpdateOne
from typing import List, Dict
import motor.motor_asyncio

# Load environment variables
load_dotenv()

# Get environment variables
ENV = os.getenv("ENV", "development")
USE_POLLING = True  
TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or TEST_BOT_TOKEN
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attackontitan")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"  
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TELEGRAM_TOKEN.split(":")[1] if TELEGRAM_TOKEN else "")

# Test mode configuration - ensures data safety
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
if TEST_MODE:
    DB_NAME = f"{DB_NAME}_test"

# Local memory storage for test mode
class LocalMemoryDatabase:
    """In-memory database for test mode that doesn't persist to MongoDB"""

    def __init__(self):
        self.players = {}
        self.characters = {}
        self.titans = {}
        self.bans = {}
        self.groups = {}
        self.shop_items = {}
        self.active_battles = {}
        self.titan_timeout_tasks = {}
        self.db = None
        self.characters_collection = self.characters  # Use the dict as mock collection
        self.characters = self.characters  # For compatibility
        self.players_collection = self.players  # Use the dict as mock collection
        self.titans_collection = self.titans  # Use the dict as mock collection
        self.equipment = {}
        self.shop_purchases = {}
        self.shop_purchases_collection = {}
        self.bank_accounts = {}
        self.groups_collection = self.groups  # Use the dict as mock collection
        self.characters_collection = self.characters  # Use the dict as mock collection
        self.players_collection = self.players  # Use the dict as mock collection
        self.titans_collection = self.titans  # Use the dict as mock collection
        self.stats = {}
        self._titan_cache = {}
        self.bans_collection = self.bans  # Use the dict as mock collection
        
        # Initialize the internal dicts
        self._characters_dict = {}
        self._players_dict = {}
        self._titans_dict = {}
        self._bans_dict = {}
        self._groups_dict = {}
        self._equipment_dict = {}
        self._shop_purchases_dict = {}
        self._bank_accounts_dict = {}
        self._stats_dict = {}

    # Collection wrapper classes for MongoDB compatibility
    class MockCollection(dict):
        """Mock collection that delegates to LocalMemoryDatabase methods and behaves like a dict"""
        
        def __init__(self, db_instance, collection_name):
            super().__init__()
            self.db = db_instance
            self.collection_name = collection_name
        
        async def find_one(self, query, projection=None):
            return await self.db.find_one(self.collection_name, query)
        
        async def update_one(self, query, update_data, upsert=False):
            return await self.db.update_one(self.collection_name, query, update_data, upsert)
        
        async def delete_one(self, query):
            return await self.db.delete_one(self.collection_name, query)
        
        async def count_documents(self, query=None):
            return await self.db.count_documents(self.collection_name, query)
        
        async def find(self, query=None, projection=None):
            # Return a mock cursor for compatibility
            return self.db.MockCursor(self.db, self.collection_name, query or {})
        
        async def insert_one(self, document):
            # For insert operations, we'll just store in the appropriate dict
            if self.collection_name == "players":
                user_id = document.get("user_id")
                if user_id:
                    self.db._players_dict[str(user_id)] = document
            elif self.collection_name == "characters":
                # Use user_id + name as key
                user_id = document.get("user_id")
                name = document.get("name")
                if user_id and name:
                    key = f"{user_id}_{name}"
                    self.db._characters_dict[key] = document
            elif self.collection_name == "titans":
                user_id = document.get("user_id")
                if user_id:
                    key = f"{user_id}_titan"
                    self.db._titans_dict[key] = document
            elif self.collection_name == "bans":
                user_id = document.get("user_id")
                if user_id:
                    self.db._bans_dict[str(user_id)] = document
            elif self.collection_name == "groups":
                group_id = document.get("id") or document.get("_id")
                if group_id:
                    self.db._groups_dict[str(group_id)] = document
            elif self.collection_name == "equipment":
                name = document.get("name")
                if name:
                    self.db._equipment_dict[name] = document
            elif self.collection_name == "shop_purchases":
                user_id = document.get("user_id")
                if user_id:
                    self.db._shop_purchases_dict[str(user_id)] = document
            elif self.collection_name == "bank_accounts":
                user_id = document.get("user_id")
                if user_id:
                    self.db._bank_accounts_dict[str(user_id)] = document
            elif self.collection_name == "stats":
                user_id = document.get("user_id")
                if user_id:
                    self.db._stats_dict[str(user_id)] = document
            return {"inserted_id": "mock_id"}
        
        def find_one_and_update(self, query, update_data, return_document=False):
            # Mock implementation
            return None

        def __setitem__(self, key, value):
            """Override dict __setitem__ to store in the appropriate internal dict"""
            if self.collection_name == "players":
                self.db._players_dict[str(key)] = value
            elif self.collection_name == "characters":
                self.db._characters_dict[str(key)] = value
            elif self.collection_name == "titans":
                self.db._titans_dict[str(key)] = value
            elif self.collection_name == "bans":
                self.db._bans_dict[str(key)] = value
            elif self.collection_name == "groups":
                self.db._groups_dict[str(key)] = value
            elif self.collection_name == "equipment":
                self.db._equipment_dict[str(key)] = value
            elif self.collection_name == "shop_purchases":
                self.db._shop_purchases_dict[str(key)] = value
            elif self.collection_name == "bank_accounts":
                self.db._bank_accounts_dict[str(key)] = value
            elif self.collection_name == "stats":
                self.db._stats_dict[str(key)] = value
            else:
                # Fallback to regular dict behavior
                super().__setitem__(key, value)

        def __getitem__(self, key):
            """Override dict __getitem__ to retrieve from the appropriate internal dict"""
            if self.collection_name == "players":
                return self.db._players_dict.get(str(key))
            elif self.collection_name == "characters":
                return self.db._characters_dict.get(str(key))
            elif self.collection_name == "titans":
                return self.db._titans_dict.get(str(key))
            elif self.collection_name == "bans":
                return self.db._bans_dict.get(str(key))
            elif self.collection_name == "groups":
                return self.db._groups_dict.get(str(key))
            elif self.collection_name == "equipment":
                return self.db._equipment_dict.get(str(key))
            elif self.collection_name == "shop_purchases":
                return self.db._shop_purchases_dict.get(str(key))
            elif self.collection_name == "bank_accounts":
                return self.db._bank_accounts_dict.get(str(key))
            elif self.collection_name == "stats":
                return self.db._stats_dict.get(str(key))
            else:
                # Fallback to regular dict behavior
                return super().__getitem__(key)

        def __contains__(self, key):
            """Override dict __contains__ to check the appropriate internal dict"""
            if self.collection_name == "players":
                return str(key) in self.db._players_dict
            elif self.collection_name == "characters":
                return str(key) in self.db._characters_dict
            elif self.collection_name == "titans":
                return str(key) in self.db._titans_dict
            elif self.collection_name == "bans":
                return str(key) in self.db._bans_dict
            elif self.collection_name == "groups":
                return str(key) in self.db._groups_dict
            elif self.collection_name == "equipment":
                return str(key) in self.db._equipment_dict
            elif self.collection_name == "shop_purchases":
                return str(key) in self.db._shop_purchases_dict
            elif self.collection_name == "bank_accounts":
                return str(key) in self.db._bank_accounts_dict
            elif self.collection_name == "stats":
                return str(key) in self.db._stats_dict
            else:
                # Fallback to regular dict behavior
                return super().__contains__(key)

    class MockCursor:
        """Mock cursor for find operations"""
        
        def __init__(self, db_instance, collection_name, query):
            self.db = db_instance
            self.collection_name = collection_name
            self.query = query
        
        async def to_list(self, length=None):
            # Return matching documents from the appropriate collection
            if self.collection_name == "players":
                return list(self.db._players_dict.values())
            elif self.collection_name == "characters":
                return list(self.db._characters_dict.values())
            elif self.collection_name == "titans":
                return list(self.db._titans_dict.values())
            elif self.collection_name == "bans":
                return list(self.db._bans_dict.values())
            elif self.collection_name == "groups":
                return list(self.db._groups_dict.values())
            elif self.collection_name == "equipment":
                return list(self.db._equipment_dict.values())
            elif self.collection_name == "shop_purchases":
                return list(self.db._shop_purchases_dict.values())
            elif self.collection_name == "bank_accounts":
                return list(self.db._bank_accounts_dict.values())
            elif self.collection_name == "stats":
                return list(self.db._stats_dict.values())
            return []
        
        def __aiter__(self):
            return self
        
        async def __anext__(self):
            # Simple iterator implementation
            raise StopAsyncIteration

    async def init_db(self, motor_db=None):
        """Initialize the local memory database"""
        logger.info("🧪 Initializing Local Memory Database for Test Mode")
        
        # Create mock collections for MongoDB compatibility
        self.characters = self.MockCollection(self, "characters")
        self.players = self.MockCollection(self, "players") 
        self.titans = self.MockCollection(self, "titans")
        self.bans = self.MockCollection(self, "bans")
        self.groups = self.MockCollection(self, "groups")
        self.equipment = self.MockCollection(self, "equipment")
        self.shop_purchases = self.MockCollection(self, "shop_purchases")
        self.bank_accounts = self.MockCollection(self, "bank_accounts")
        self.stats = self.MockCollection(self, "stats")
        
        # Keep dict versions for internal use
        self._characters_dict = {}
        self._players_dict = {}
        self._titans_dict = {}
        self._bans_dict = {}
        self._groups_dict = {}
        self._equipment_dict = {}
        self._shop_purchases_dict = {}
        self._bank_accounts_dict = {}
        self._stats_dict = {}
        
        # Collection references for compatibility
        self.players_collection = self.players
        self.characters_collection = self.characters
        self.titans_collection = self.titans
        self.groups_collection = self.groups
        self.bans_collection = self.bans
        self.shop_purchases_collection = self.shop_purchases
        
        return self

    async def get_player(self, user_id):
        """Get player from local memory, lazy load if needed"""
        user_id_str = str(user_id)
        if user_id_str not in self._players_dict:
            # Try to load from persistent DB if available
            await self._try_load_from_persistent_db(user_id_str)
        player_data = self._players_dict.get(user_id_str)
        if player_data and isinstance(player_data, dict):
            # Sanitize player data before creating Player object
            from database.db import sanitize_player_data
            from database.models import Player
            player_data = sanitize_player_data(player_data)
            # Convert dict back to Player object
            return Player(**player_data)
        return player_data

    async def _try_load_from_persistent_db(self, user_id_str):
        """Try to load user data from persistent DB if possible"""
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                
                # Load player data
                player = await db.get_player(user_id_str)
                if player:
                    self.players[user_id_str] = player
                    logger.info(f"[LocalMemoryDB] Loaded player {user_id_str} from persistent DB")
                    
                    # Load all characters for this player
                    characters = await db.get_player_characters(user_id_str)
                    for character in characters:
                        key = f"{user_id_str}_{character.name}"
                        self._characters_dict[key] = character
                        logger.info(f"[LocalMemoryDB] Loaded character {character.name} for player {user_id_str}")
                    
                    # Load titan if exists
                    titan = await db.get_titan(user_id_str)
                    if titan:
                        key = f"{user_id_str}_titan"
                        self.titans[key] = titan
                        logger.info(f"[LocalMemoryDB] Loaded titan for player {user_id_str}")
                    
                    # Load bank account if exists
                    bank_account = await db.get_bank_account(user_id_str)
                    if bank_account:
                        self.bank_accounts[user_id_str] = bank_account
                        logger.info(f"[LocalMemoryDB] Loaded bank account for player {user_id_str}")
                    
                    logger.info(f"[LocalMemoryDB] Successfully loaded complete data for player {user_id_str}")
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not load from persistent DB: {e}")

    async def update_player(self, user_id, update_data):
        """Update player in local memory"""
        user_id_str = str(user_id)
        if user_id_str not in self._players_dict:
            self._players_dict[user_id_str] = {}

        player_data = self._players_dict[user_id_str]
        
        # If player_data is a Player object, update its attributes
        if hasattr(player_data, '__dict__') and not isinstance(player_data, dict):
            for key, value in update_data.items():
                if hasattr(player_data, key):
                    setattr(player_data, key, value)
        else:
            # Handle as dict
            for key, value in update_data.items():
                if isinstance(player_data, dict):
                    if isinstance(player_data.get(key), dict) and isinstance(value, dict):
                        # Merge nested dictionaries
                        if key not in player_data:
                            player_data[key] = {}
                        player_data[key].update(value)
                    else:
                        player_data[key] = value
                else:
                    # Fallback: try to set as attribute
                    if hasattr(player_data, key):
                        setattr(player_data, key, value)

        return player_data

    async def get_character(self, user_id, character_name):
        """Get character from local memory, lazy load if needed"""
        key = f"{user_id}_{character_name}"
        if key not in self._characters_dict:
            # Try to load from persistent DB if available
            await self._try_load_character_from_persistent_db(str(user_id), character_name)
        character_data = self._characters_dict.get(key)
        if character_data and isinstance(character_data, dict):
            # Convert dict back to Character object
            from database.models import Character
            return Character(**character_data)
        return character_data

    async def _try_load_character_from_persistent_db(self, user_id_str, character_name):
        """Try to load character data from persistent DB if possible"""
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                character = await db.get_character(user_id_str, character_name)
                if character:
                    key = f"{user_id_str}_{character_name}"
                    self._characters_dict[key] = character
                    logger.info(f"[LocalMemoryDB] Loaded character {character_name} for player {user_id_str} from persistent DB")
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not load character {character_name} from persistent DB: {e}")

    async def update_character(self, character):
        """Update character in local memory"""
        key = f"{character.user_id}_{character.name}"
        self._characters_dict[key] = character
        return character

    async def store_titan(self, user_id, titan):
        """Store titan in local memory"""
        key = f"{user_id}_titan"
        self._titans_dict[key] = titan

    async def get_titan(self, user_id):
        """Get titan from local memory, lazy load if needed"""
        key = f"{user_id}_titan"
        if key not in self._titans_dict:
            # Try to load from persistent DB if available
            await self._try_load_titan_from_persistent_db(str(user_id))
        return self._titans_dict.get(key)

    async def _try_load_titan_from_persistent_db(self, user_id_str):
        """Try to load titan data from persistent DB if possible"""
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                titan = await db.get_titan(user_id_str)
                if titan:
                    key = f"{user_id_str}_titan"
                    self.titans[key] = titan
                    logger.info(f"[LocalMemoryDB] Loaded titan for player {user_id_str} from persistent DB")
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not load titan from persistent DB: {e}")

    async def delete_titan(self, user_id):
        """Delete titan from local memory"""
        key = f"{user_id}_titan"
        if key in self._titans_dict:
            del self._titans_dict[key]

    async def get_bank_account(self, user_id):
        """Get bank account from local memory, lazy load if needed"""
        user_id_str = str(user_id)
        if user_id_str not in self._bank_accounts_dict:
            # Try to load from persistent DB if available
            await self._try_load_bank_from_persistent_db(user_id_str)
        return self._bank_accounts_dict.get(user_id_str)

    async def _try_load_bank_from_persistent_db(self, user_id_str):
        """Try to load bank account data from persistent DB if possible"""
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                bank_account = await db.get_bank_account(user_id_str)
                if bank_account:
                    self.bank_accounts[user_id_str] = bank_account
                    logger.info(f"[LocalMemoryDB] Loaded bank account for player {user_id_str} from persistent DB")
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not load bank account from persistent DB: {e}")

    async def preload_user_data(self, user_id_str):
        """Preload all user data from persistent database"""
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                
                # Load player data
                player = await db.get_player(user_id_str)
                if player:
                    self._players_dict[user_id_str] = player
                    logger.info(f"[LocalMemoryDB] Preloaded player {user_id_str} from persistent DB")
                    
                    # Load all characters for this player
                    characters = await db.get_player_characters(user_id_str)
                    for character in characters:
                        key = f"{user_id_str}_{character.name}"
                        self._characters_dict[key] = character
                    
                    # Load titan if exists
                    titan = await db.get_titan(user_id_str)
                    if titan:
                        key = f"{user_id_str}_titan"
                        self._titans_dict[key] = titan
                    
                    # Load bank account if exists
                    bank_account = await db.get_bank_account(user_id_str)
                    if bank_account:
                        self._bank_accounts_dict[user_id_str] = bank_account
                    
                    logger.info(f"[LocalMemoryDB] Successfully preloaded complete data for player {user_id_str}")
                    return True
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not preload data for {user_id_str}: {e}")
        return False

    # Essential database methods for compatibility
    async def create_player(self, user_id: str, username: str, name: str, referral_code: str = None, referred_by: str = None):
        """Create a new player in local memory"""
        try:
            from database.models import Player
            import time
            
            # Generate a unique referral code if not provided
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
            
            self.players[str(user_id)] = player
            logger.info(f"[LocalMemoryDB] Created player: {player}")
            return player
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to create player: {e}")
            raise

    async def create_character(self, user_id: str, name: str, character_type: str, current_hp: int):
        """Create a new character in local memory"""
        try:
            from database.characters import get_character_data
            from database.models import Character, CharacterStats
            
            char_data = get_character_data(character_type)
            if not char_data:
                raise ValueError(f"Character type {character_type} not found")
            
            # Ensure stats are properly handled
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
            key = f"{user_id}_{name}"
            self.characters[key] = character
            
            # Add to player's owned characters
            if user_id in self.players:
                if name not in self.players[user_id].owned_characters:
                    self.players[user_id].owned_characters.append(name)
            
            logger.info(f"[LocalMemoryDB] Created character: {name} for user {user_id}")
            return character
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to create character: {e}")
            raise

    async def add_character_to_player(self, user_id: str, character_name: str):
        """Add character to player's owned list"""
        try:
            user_id_str = str(user_id)
            if user_id_str in self.players:
                if character_name not in self.players[user_id_str].owned_characters:
                    self.players[user_id_str].owned_characters.append(character_name)
                    logger.info(f"[LocalMemoryDB] Added character {character_name} to player {user_id_str}")
                    return True
            return False
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to add character to player: {e}")
            return False

    async def get_player_characters(self, user_id: str):
        """Get all characters for a player"""
        try:
            user_id_str = str(user_id)
            characters = []
            for key, character in self._characters_dict.items():
                if key.startswith(f"{user_id_str}_"):
                    # Ensure stats is a CharacterStats object, not a dict
                    if isinstance(character.stats, dict):
                        from database.models import CharacterStats
                        character.stats = CharacterStats(**character.stats)
                    characters.append(character)
            return characters
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to get player characters: {e}")
            return []

    async def save_player(self, player):
        """Save player to local memory"""
        try:
            user_id_str = str(player.user_id)
            self._players_dict[user_id_str] = player
            logger.info(f"[LocalMemoryDB] Saved player {user_id_str}")
            return player
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to save player: {e}")
            raise

    async def add_new_character_to_player(self, user_id: str, character_name: str):
        """Add new character to player (compatibility method)"""
        return await self.add_character_to_player(user_id, character_name)

    async def get_all_players(self):
        """Get all players from local memory"""
        return list(self.players.values())

    async def delete_player(self, user_id: str):
        """Delete player from local memory"""
        try:
            user_id_str = str(user_id)
            if user_id_str in self._players_dict:
                del self._players_dict[user_id_str]
                logger.info(f"[LocalMemoryDB] Deleted player {user_id_str}")
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Failed to delete player: {e}")

    # Auto-load user data when any command is executed
    async def ensure_user_data_loaded(self, user_id: str):
        """Ensure user data is loaded from production database"""
        user_id_str = str(user_id)
        if user_id_str not in self._players_dict:
            logger.info(f"[LocalMemoryDB] Auto-loading data for user {user_id_str}")
            await self.preload_user_data(user_id_str)

    def get_status_message(self):
        """Get a status message showing current local database state"""
        stats = self.get_stats()
        return (
            f"🧪 *Local Memory Database Status*\n\n"
            f"📊 Current Data:\n"
            f"• Players: {stats['players']}\n"
            f"• Characters: {stats['characters']}\n"
            f"• Active Titans: {stats['titans']}\n"
            f"• Bank Accounts: {stats['bank_accounts']}\n"
            f"• Bans: {stats['bans']}\n\n"
            f"💾 Data loaded from production database\n"
            f"🔄 Data will be lost on restart\n"
            f"🗑️ Use /cleardb to reset all test data"
        )

    # Compatibility methods
    async def find_one(self, collection, query):
        """Mock find_one for compatibility"""
        logger.debug(f"📖 Local DB: find_one called on {collection} with query: {query}")
        # Handle different collection types
        if collection == "bans":
            return self._find_one_in_dict(self._bans_dict, query)
        elif collection == "players":
            return self._find_one_in_dict(self._players_dict, query)
        elif collection == "characters":
            return self._find_one_in_dict(self._characters_dict, query)
        elif collection == "titans":
            return self._find_one_in_dict(self._titans_dict, query)
        elif collection == "groups":
            return self._find_one_in_dict(self._groups_dict, query)
        elif collection == "equipment":
            return self._find_one_in_dict(self._equipment_dict, query)
        elif collection == "shop_purchases":
            return self._find_one_in_dict(self._shop_purchases_dict, query)
        elif collection == "bank_accounts":
            return self._find_one_in_dict(self._bank_accounts_dict, query)
        elif collection == "stats":
            return self._find_one_in_dict(self._stats_dict, query)
        return None

    async def update_one(self, collection, query, update_data, upsert=False):
        """Mock update_one for compatibility"""
        logger.debug(f"📝 Local DB: update_one called on {collection}")
        # Handle different collection types
        if collection == "bans":
            return self._update_one_in_dict(self._bans_dict, query, update_data, upsert)
        elif collection == "players":
            return self._update_one_in_dict(self._players_dict, query, update_data, upsert)
        elif collection == "characters":
            return self._update_one_in_dict(self._characters_dict, query, update_data, upsert)
        elif collection == "titans":
            return self._update_one_in_dict(self._titans_dict, query, update_data, upsert)
        elif collection == "groups":
            return self._update_one_in_dict(self._groups_dict, query, update_data, upsert)
        elif collection == "equipment":
            return self._update_one_in_dict(self._equipment_dict, query, update_data, upsert)
        elif collection == "shop_purchases":
            return self._update_one_in_dict(self._shop_purchases_dict, query, update_data, upsert)
        elif collection == "bank_accounts":
            return self._update_one_in_dict(self._bank_accounts_dict, query, update_data, upsert)
        elif collection == "stats":
            return self._update_one_in_dict(self._stats_dict, query, update_data, upsert)
        return None

    def _find_one_in_dict(self, data_dict, query):
        """Find one document in a dict that matches the query"""
        try:
            for key, value in data_dict.items():
                if isinstance(value, dict):
                    # Check if this document matches the query
                    matches = True
                    for q_key, q_value in query.items():
                        if q_key == "$exists":
                            # Handle $exists operator
                            if isinstance(q_value, dict):
                                for exists_key, should_exist in q_value.items():
                                    if should_exist and exists_key not in value:
                                        matches = False
                                        break
                                    elif not should_exist and exists_key in value:
                                        matches = False
                                        break
                        elif q_key not in value or value[q_key] != q_value:
                            matches = False
                            break
                    if matches:
                        return value
                elif hasattr(value, 'user_id') and 'user_id' in query:
                    # Handle object attributes
                    if str(getattr(value, 'user_id', None)) == str(query['user_id']):
                        return value
        except Exception as e:
            logger.error(f"Error in _find_one_in_dict: {e}")
        return None

    def _update_one_in_dict(self, data_dict, query, update_data, upsert=False):
        """Update one document in a dict"""
        try:
            # Find the document to update
            target_key = None
            for key, value in data_dict.items():
                if isinstance(value, dict):
                    matches = True
                    for q_key, q_value in query.items():
                        if q_key not in value or value[q_key] != q_value:
                            matches = False
                            break
                    if matches:
                        target_key = key
                        break
                elif hasattr(value, 'user_id') and 'user_id' in query:
                    if str(getattr(value, 'user_id', None)) == str(query['user_id']):
                        target_key = key
                        break

            if target_key is not None:
                # Update existing document
                if "$set" in update_data:
                    for set_key, set_value in update_data["$set"].items():
                        if isinstance(data_dict[target_key], dict):
                            data_dict[target_key][set_key] = set_value
                        else:
                            # Handle object attributes
                            if set_key == "stats" and isinstance(set_value, dict):
                                # Ensure stats remains a CharacterStats object
                                from database.models import CharacterStats
                                setattr(data_dict[target_key], set_key, CharacterStats(**set_value))
                            else:
                                setattr(data_dict[target_key], set_key, set_value)
                return {"matched_count": 1, "modified_count": 1}
            elif upsert:
                # Create new document
                new_key = str(query.get('user_id', len(data_dict)))
                data_dict[new_key] = update_data.get("$set", update_data)
                return {"matched_count": 0, "modified_count": 1, "upserted_id": new_key}
            else:
                return {"matched_count": 0, "modified_count": 0}
        except Exception as e:
            logger.error(f"Error in _update_one_in_dict: {e}")
            return {"matched_count": 0, "modified_count": 0}

    async def delete_one(self, collection, query):
        """Mock delete_one for compatibility"""
        logger.debug(f"🗑️ Local DB: delete_one called on {collection}")
        # Handle different collection types
        if collection == "bans":
            return self._delete_one_from_dict(self._bans_dict, query)
        elif collection == "players":
            return self._delete_one_from_dict(self._players_dict, query)
        elif collection == "characters":
            return self._delete_one_from_dict(self._characters_dict, query)
        elif collection == "titans":
            return self._delete_one_from_dict(self._titans_dict, query)
        elif collection == "groups":
            return self._delete_one_from_dict(self._groups_dict, query)
        elif collection == "equipment":
            return self._delete_one_from_dict(self._equipment_dict, query)
        elif collection == "shop_purchases":
            return self._delete_one_from_dict(self._shop_purchases_dict, query)
        elif collection == "bank_accounts":
            return self._delete_one_from_dict(self._bank_accounts_dict, query)
        elif collection == "stats":
            return self._delete_one_from_dict(self._stats_dict, query)
        return {"deleted_count": 0}

    def _delete_one_from_dict(self, data_dict, query):
        """Delete one document from a dict"""
        try:
            for key, value in list(data_dict.items()):
                if isinstance(value, dict):
                    matches = True
                    for q_key, q_value in query.items():
                        if q_key not in value or value[q_key] != q_value:
                            matches = False
                            break
                    if matches:
                        del data_dict[key]
                        return {"deleted_count": 1}
                elif hasattr(value, 'user_id') and 'user_id' in query:
                    if str(getattr(value, 'user_id', None)) == str(query['user_id']):
                        del data_dict[key]
                        return {"deleted_count": 1}
        except Exception as e:
            logger.error(f"Error in _delete_one_from_dict: {e}")
        return {"deleted_count": 0}

    async def count_documents(self, collection, query=None):
        """Mock count_documents for compatibility"""
        logger.debug(f"🔢 Local DB: count_documents called on {collection}")
        # Handle different collection types
        if collection == "bans":
            return self._count_documents_in_dict(self._bans_dict, query)
        elif collection == "players":
            return self._count_documents_in_dict(self._players_dict, query)
        elif collection == "characters":
            return self._count_documents_in_dict(self._characters_dict, query)
        elif collection == "titans":
            return self._count_documents_in_dict(self._titans_dict, query)
        elif collection == "groups":
            return self._count_documents_in_dict(self._groups_dict, query)
        elif collection == "equipment":
            return self._count_documents_in_dict(self._equipment_dict, query)
        elif collection == "shop_purchases":
            return self._count_documents_in_dict(self._shop_purchases_dict, query)
        elif collection == "bank_accounts":
            return self._count_documents_in_dict(self._bank_accounts_dict, query)
        elif collection == "stats":
            return self._count_documents_in_dict(self._stats_dict, query)
        return 0

    def _count_documents_in_dict(self, data_dict, query=None):
        """Count documents in a dict that match the query"""
        try:
            if query is None:
                return len(data_dict)

            count = 0
            for value in data_dict.values():
                if isinstance(value, dict):
                    matches = True
                    if query:
                        for q_key, q_value in query.items():
                            if q_key not in value or value[q_key] != q_value:
                                matches = False
                                break
                    if matches:
                        count += 1
                elif hasattr(value, 'user_id') and 'user_id' in query:
                    if str(getattr(value, 'user_id', None)) == str(query['user_id']):
                        count += 1
            return count
        except Exception as e:
            logger.error(f"Error in _count_documents_in_dict: {e}")
            return 0

    def invalidate_titan_cache(self, user_id: str):
        """Mock invalidate_titan_cache for compatibility"""
        if user_id in self._titan_cache:
            del self._titan_cache[user_id]
        return True

    def invalidate_player_cache(self, user_id: str):
        """Mock invalidate_player_cache for compatibility"""
        return True

    def invalidate_character_cache(self, user_id: str, character_name: str):
        """Mock invalidate_character_cache for compatibility"""
        return True

    def invalidate_all_character_caches(self, user_id: str):
        return True

    def invalidate_battle_caches(self, user_id: str):
        cleared_count = 0

        # Clear all character caches for this user
        if self.invalidate_all_character_caches(user_id):
            cleared_count += 1

        # Clear player cache for this user
        if self.invalidate_player_cache(user_id):
            cleared_count += 1

        # Clear titan cache for this user
        if self.invalidate_titan_cache(user_id):
            cleared_count += 1

        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} battle-related cache entries for user {user_id}")

        return cleared_count > 0

    def get_stats(self):
        """Get database statistics"""
        return {
            "players": len(self._players_dict),
            "characters": len(self._characters_dict),
            "titans": len(self._titans_dict),
            "bank_accounts": len(self._bank_accounts_dict),
            "bans": len(self._bans_dict)
        }

    def clear_all(self):
        """Clear all data (for testing)"""
        self._players_dict.clear()
        self._characters_dict.clear()
        self._titans_dict.clear()
        self._bank_accounts_dict.clear()
        self._bans_dict.clear()
        self._groups_dict.clear()
        self._equipment_dict.clear()
        self._shop_purchases_dict.clear()
        self._stats_dict.clear()
        logger.info("🧪 Local DB: All data cleared")

    async def create_player(self, user_id: str, username: str, name: str, referral_code: str = None, referred_by: str = None) -> Player:
        """Create a new player in local memory"""
        try:
            from database.models import Player
            import time
            start = time.perf_counter()
            
            # Generate a unique referral code if not provided
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
            
            # Store in local memory
            self._players_dict[str(user_id)] = player
            
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"create_player (local) query time: {elapsed:.2f} ms")
            return player
        except Exception as e:
            logger.error(f"Failed to create player in local memory: {e}")
            raise

    async def save_player(self, player: Player) -> Player:
        """Save player in local memory"""
        try:
            self._players_dict[str(player.user_id)] = player
            return player
        except Exception as e:
            logger.error(f"Failed to save player in local memory: {e}")
            raise

    async def get_player_characters(self, user_id: str):
        """Get player characters from local memory"""
        try:
            user_id_str = str(user_id)
            characters = []
            for key, character in self._characters_dict.items():
                if key.startswith(f"{user_id_str}_"):
                    characters.append(character)
            return characters
        except Exception as e:
            logger.error(f"Failed to get player characters from local memory: {e}")
            return []

    async def add_character_to_player(self, user_id: str, character_name: str) -> bool:
        """Add character to player in local memory"""
        try:
            if str(user_id) in self._players_dict:
                if character_name not in self._players_dict[str(user_id)].owned_characters:
                    self._players_dict[str(user_id)].owned_characters.append(character_name)
                    
                    # Auto-add to team if there's space
                    player = self._players_dict[str(user_id)]
                    current_team = player.team
                    
                    team_members = [m.character_name for m in current_team] if current_team else []
                    
                    if len(team_members) < 3 and character_name not in team_members:
                        used_positions = {m.position for m in current_team} if current_team else set()
                        next_pos = min(set([1, 2, 3]) - used_positions)
                        new_member = TeamMember(character_name=character_name, position=next_pos)
                        
                        if current_team:
                            current_team.append(new_member)
                        else:
                            current_team = [new_member]
                        
                        # Update the player's team in local memory
                        player.team = current_team
                        
                        # Also update in persistent database if available
                        try:
                            motor_db = await get_persistent_database()
                            if motor_db is not None:
                                db = Database()
                                await db.init_db(motor_db)
                                await db.update_player(str(user_id), {"team": [m.dict() for m in current_team]})
                        except Exception as db_e:
                            logger.warning(f"Failed to update team in persistent database: {db_e}")
                
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to add character to player in local memory: {e}")
            return False

    async def create_character(self, user_id: str, name: str, character_type: str, current_hp: int):
        """Create character in local memory"""
        try:
            from database.models import Character, CharacterStats
            from database.characters import get_character_data
            
            char_data = get_character_data(character_type)
            if not char_data:
                raise ValueError(f"Character type {character_type} not found")
            
            # Ensure stats is a CharacterStats object, not a dict
            if isinstance(char_data.base_stats, dict):
                stats = CharacterStats(**char_data.base_stats)
            else:
                stats = char_data.base_stats
            
            character = Character(
                user_id=str(user_id),
                name=name,
                character_type=character_type,
                current_hp=current_hp,
                level=1,
                xp=0,
                total_xp=0,
                stats=stats,
                gas=5000,
                max_gas=5000,
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={}
            )
            
            # Store in local memory
            key = f"{user_id}_{name}"
            self._characters_dict[key] = character
            
            # Add to player's owned characters
            await self.add_character_to_player(user_id, name)
            
            return character
        except Exception as e:
            logger.error(f"Failed to create character in local memory: {e}")
            raise

    async def get_character(self, user_id: str, character_name: str):
        """Get character from local memory"""
        try:
            key = f"{user_id}_{character_name}"
            character = self._characters_dict.get(key)
            if character:
                # Ensure stats is a CharacterStats object, not a dict
                if isinstance(character.stats, dict):
                    from database.models import CharacterStats
                    character.stats = CharacterStats(**character.stats)
            return character
        except Exception as e:
            logger.error(f"Failed to get character from local memory: {e}")
            return None

    async def update_character(self, character):
        """Update character in local memory"""
        try:
            from database.models import CharacterStats
            
            # Ensure stats is a CharacterStats object if it's a dict
            if isinstance(character.stats, dict):
                character.stats = CharacterStats(**character.stats)
            
            key = f"{character.user_id}_{character.name}"
            self._characters_dict[key] = character
            return character
        except Exception as e:
            logger.error(f"Failed to update character in local memory: {e}")
            raise

    async def get_all_players(self):
        """Get all players from local memory"""
        try:
            return list(self._players_dict.values())
        except Exception as e:
            logger.error(f"Failed to get all players from local memory: {e}")
            return []

    async def delete_player(self, user_id: str):
        """Delete player from local memory"""
        try:
            if str(user_id) in self._players_dict:
                del self._players_dict[str(user_id)]
            logger.info(f"Deleted player with user_id: {user_id} from local memory")
        except Exception as e:
            logger.error(f"Failed to delete player from local memory: {e}")
            raise

    async def get_connection_stats(self):
        """Mock connection stats for local memory"""
        return {
            "connections": {"current": 1, "available": 1, "totalCreated": 1},
            "uptime": 0,
            "opcounters": {"insert": 0, "query": 0, "update": 0, "delete": 0, "getmore": 0, "command": 0},
            "mem": {"bits": 64, "resident": 0, "virtual": 0, "supported": True, "mapped": 0, "mappedWithJournal": 0}
        }

    async def get_group(self, group_id: int):
        """Get group from local memory"""
        try:
            return self._groups_dict.get(str(group_id))
        except Exception as e:
            logger.error(f"Failed to get group from local memory: {e}")
            return None

    async def update_group(self, group_id: int, update_data: Dict) -> bool:
        """Update group in local memory"""
        try:
            self._groups_dict[str(group_id)] = update_data
            return True
        except Exception as e:
            logger.error(f"Failed to update group in local memory: {e}")
            return False

    async def get_all_groups(self, filter_data: Dict = None):
        """Get all groups from local memory"""
        try:
            return list(self._groups_dict.values())
        except Exception as e:
            logger.error(f"Failed to get all groups from local memory: {e}")
            return []

    async def delete_group(self, group_id: int) -> bool:
        """Delete group from local memory"""
        try:
            if str(group_id) in self._groups_dict:
                del self._groups_dict[str(group_id)]
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete group from local memory: {e}")
            return False

    async def record_purchase(self, user_id: str, item_key: str):
        """Record purchase in local memory"""
        try:
            if str(user_id) not in self._shop_purchases_dict:
                self._shop_purchases_dict[str(user_id)] = []
            self._shop_purchases_dict[str(user_id)].append({
                "item_key": item_key,
                "purchase_date": datetime.now(timezone.utc)
            })
        except Exception as e:
            logger.error(f"Failed to record purchase in local memory: {e}")

    async def update_character_stats(self, user_id: str, character_name: str, stats: Dict) -> bool:
        """Update character stats in local memory"""
        try:
            character = await self.get_character(str(user_id), character_name)
            if character:
                character.stats = stats
                await self.update_character(character)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update character stats in local memory: {e}")
            return False

    async def get_available_titans(self, character_level: int):
        """Get available titans from local memory"""
        try:
            available_titans = []
            for titan in self._titans_dict.values():
                # Check if titan is an object with min_level_requirement attribute
                if hasattr(titan, 'min_level_requirement'):
                    if titan.min_level_requirement <= character_level:
                        available_titans.append(titan)
                # Or if it's a dict
                elif isinstance(titan, dict) and titan.get('min_level_requirement', 1) <= character_level:
                    available_titans.append(titan)
            return available_titans
        except Exception as e:
            logger.error(f"Failed to get available titans from local memory: {e}")
            return []

    async def get_daily_purchases(self, user_id: str, item_key: str) -> int:
        """Get daily purchases from local memory"""
        try:
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            purchases = self._shop_purchases_dict.get(str(user_id), [])
            count = 0
            for p in purchases:
                # Check if p is a dict
                if isinstance(p, dict):
                    if p.get('item_key') == item_key and p.get('purchase_date') and p['purchase_date'] >= today:
                        count += 1
                # Or if it's an object
                elif hasattr(p, 'item_key') and hasattr(p, 'purchase_date'):
                    if getattr(p, 'item_key', None) == item_key and getattr(p, 'purchase_date', None) and getattr(p, 'purchase_date') >= today:
                        count += 1
            return count
        except Exception as e:
            logger.error(f"Failed to get daily purchases from local memory: {e}")
            return 0

    async def store_shop_refresh(self, user_id: str, count: int):
        """Store shop refresh in local memory"""
        try:
            if str(user_id) in self._players_dict:
                self._players_dict[str(user_id)].shop_refresh_count = count
                self._players_dict[str(user_id)].shop_refresh_date = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Failed to store shop refresh in local memory: {e}")

    async def get_shop_refresh_count(self, user_id: str) -> int:
        """Get shop refresh count from local memory"""
        try:
            if str(user_id) in self._players_dict:
                player = self._players_dict[str(user_id)]
                if not player.shop_refresh_date:
                    return 0
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                if player.shop_refresh_date.date() == today.date():
                    return player.shop_refresh_count or 0
            return 0
        except Exception as e:
            logger.error(f"Failed to get shop refresh count from local memory: {e}")
            return 0

    async def add_new_character_to_player(self, user_id: str, character_name: str) -> bool:
        """Add new character to player in local memory"""
        try:
            from database.characters import get_character_data
            
            char_data = get_character_data(character_name)
            if not char_data:
                return False
            
            existing_char = await self.get_character(str(user_id), character_name)
            if existing_char:
                return False
            
            await self.create_character(str(user_id), character_name, character_name, current_hp=getattr(char_data.base_stats, "current_hp", 100))
            return True
        except Exception as e:
            logger.error(f"Failed to add new character to player in local memory: {e}")
            return False

    async def get_character_abilities(self, user_id: str, character_name: str):
        """Get character abilities from local memory"""
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
            logger.error(f"Failed to get character abilities from local memory: {e}")
            return {"active": [], "passive": [], "ultimate": []}

    async def get_character_fresh(self, user_id: str, character_name: str):
        """Get fresh character from local memory (bypassing any cache)"""
        try:
            key = f"{user_id}_{character_name}"
            character = self._characters_dict.get(key)
            if character:
                # Ensure stats is a CharacterStats object, not a dict
                if isinstance(character.stats, dict):
                    from database.models import CharacterStats
                    character.stats = CharacterStats(**character.stats)
            return character
        except Exception as e:
            logger.error(f"Failed to get fresh character from local memory: {e}")
            return None

    async def batch_update_character(self, user_id: str, character_name: str, update_data: Dict) -> bool:
        """Batch update character in local memory"""
        try:
            character = await self.get_character(str(user_id), character_name)
            if character:
                for key, value in update_data.items():
                    if hasattr(character, key):
                        setattr(character, key, value)
                await self.update_character(character)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to batch update character in local memory: {e}")
            return False

    async def batch_update_player(self, user_id: str, update_data: Dict) -> bool:
        """Batch update player in local memory"""
        try:
            player = await self.update_player(str(user_id), update_data)
            return player is not None  # Return True if player was updated, False if not
        except Exception as e:
            logger.error(f"Failed to batch update player in local memory: {e}")
            return False

    async def update_player(self, user_id: str, update_data: Dict):
        """Update player in local memory"""
        try:
            user_id_str = str(user_id)
            if user_id_str not in self._players_dict:
                # Create a new player if it doesn't exist
                from database.models import Player
                self._players_dict[user_id_str] = Player(
                    user_id=user_id_str,
                    username="Unknown",
                    name="Unknown Player",
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
                    referral_code=user_id_str,
                    referred_by=None,
                    referral_count=0,
                    referral_milestones={}
                )

            player = self._players_dict[user_id_str]

            # Apply updates to the player object
            for key, value in update_data.items():
                if hasattr(player, key):
                    if key == "team" and isinstance(value, list):
                        from database.models import TeamMember
                        player.team = [TeamMember(**m) if isinstance(m, dict) else m for m in value]
                    else:
                        setattr(player, key, value)

            return player
        except Exception as e:
            logger.error(f"Failed to update player in local memory: {e}")
            return None

    async def get_equipment(self, name: str):
        """Get equipment from local memory"""
        try:
            return self._equipment_dict.get(name)
        except Exception as e:
            logger.error(f"Failed to get equipment from local memory: {e}")
            return None

    async def get_equipment_by_type(self, type: str):
        """Get equipment by type from local memory"""
        try:
            matching_equipment = []
            for equip in self._equipment_dict.values():
                # Check if equip is an object with type attribute
                if hasattr(equip, 'type'):
                    if getattr(equip, 'type', None) == type:
                        matching_equipment.append(equip)
                # Or if it's a dict
                elif isinstance(equip, dict) and equip.get('type') == type:
                    matching_equipment.append(equip)
            return matching_equipment
        except Exception as e:
            logger.error(f"Failed to get equipment by type from local memory: {e}")
            return []

    async def create_equipment(self, equipment):
        """Create equipment in local memory"""
        try:
            self._equipment_dict[equipment.name] = equipment
            return equipment
        except Exception as e:
            logger.error(f"Failed to create equipment in local memory: {e}")
            raise

    async def get_bank_account(self, user_id: str):
        """Get bank account from local memory"""
        try:
            return self._bank_accounts_dict.get(str(user_id))
        except Exception as e:
            logger.error(f"Failed to get bank account from local memory: {e}")
            return None

    async def save_bank_account(self, account):
        """Save bank account in local memory"""
        try:
            self._bank_accounts_dict[str(account.user_id)] = account
        except Exception as e:
            logger.error(f"Failed to save bank account in local memory: {e}")

    async def get_all_bank_accounts(self):
        """Get all bank accounts from local memory"""
        try:
            return list(self._bank_accounts_dict.values())
        except Exception as e:
            logger.error(f"Failed to get all bank accounts from local memory: {e}")
            return []

    async def generate_titan(self, player_level: int, unlocked_areas: List[str], user_id: str = None):
        """Generate titan in local memory"""
        try:
            from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
            import random
            from datetime import datetime, timezone
            
            # Determine difficulty based on player level
            if player_level < 8:
                difficulty = "Easy"
            elif player_level < 15:
                difficulty = "Normal"
            else:
                difficulty = "Hard"

            # Titan level: within -2 to +2 of player, but at least 1
            level = max(1, player_level + random.randint(-2, 2))

            # Name and HP using models.py logic
            name = generate_titan_name(difficulty)
            max_hp = generate_titan_hp(level, difficulty)
            now = datetime.now(timezone.utc)
            
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
            
            return titan
        except Exception as e:
            logger.error(f"Failed to generate titan in local memory: {e}")
            return None

    async def store_titan(self, user_id: str, titan):
        """Store titan in local memory"""
        try:
            key = f"{user_id}_titan"
            self._titans_dict[key] = titan
        except Exception as e:
            logger.error(f"Failed to store titan in local memory: {e}")

    async def get_titan(self, user_id: str):
        """Get titan from local memory"""
        try:
            key = f"{user_id}_titan"
            return self._titans_dict.get(key)
        except Exception as e:
            logger.error(f"Failed to get titan from local memory: {e}")
            return None

    async def delete_titan(self, user_id: str):
        """Delete titan from local memory"""
        try:
            key = f"{user_id}_titan"
            if key in self._titans_dict:
                del self._titans_dict[key]
        except Exception as e:
            logger.error(f"Failed to delete titan from local memory: {e}")

    async def generate_multiple_titans(self, player_level: int, unlocked_areas: List[str], count: int = 3):
        """Generate multiple titans in local memory"""
        try:
            titans = []
            for _ in range(count):
                titan = await self.generate_titan(player_level, unlocked_areas)
                if titan:
                    titans.append(titan)
            return titans
        except Exception as e:
            logger.error(f"Failed to generate multiple titans in local memory: {e}")
            return []

    async def create_new_titan(self, level: int, difficulty: str, spawn_areas: List[str]):
        """Create new titan in local memory"""
        try:
            from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
            from datetime import datetime, timezone
            
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
        except Exception as e:
            logger.error(f"Failed to create new titan in local memory: {e}")
            return None

    async def get_random_titan(self, min_level: int, max_level: int, target_level: int, unlocked_areas: List[str] = None):
        """Get random titan from local memory"""
        try:
            level = random.randint(min_level, max_level)
            if level >= 15:
                difficulty = "Hard"
            elif level >= 8:
                difficulty = "Normal"
            else:
                difficulty = "Easy"
            
            if not unlocked_areas:
                unlocked_areas = ["Trost District", "Karanes District", "Shiganshina District"]
            
            return await self.create_new_titan(level=level, difficulty=difficulty, spawn_areas=unlocked_areas)
        except Exception as e:
            logger.error(f"Failed to get random titan from local memory: {e}")
            return None

    async def preload_user_data(self, user_id_str: str) -> bool:
        """Load user data from production database into local memory"""
        try:
            # Try to load from persistent DB if available
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
                
                # Load player data
                player = await db.get_player(user_id_str)
                if player:
                    self._players_dict[user_id_str] = player
                    logger.info(f"Loaded player {user_id_str} from production DB")
                
                # Load characters
                characters = await db.get_player_characters(user_id_str)
                for char in characters:
                    key = f"{user_id_str}_{char.name}"
                    self._characters_dict[key] = char
                
                # Load bank account
                bank_account = await db.get_bank_account(user_id_str)
                if bank_account:
                    self._bank_accounts_dict[user_id_str] = bank_account
                
                # Load active titan
                titan = await db.get_titan(user_id_str)
                if titan:
                    key = f"{user_id_str}_titan"
                    self._titans_dict[key] = titan
                
                logger.info(f"Successfully preloaded data for user {user_id_str}")
                return True
            else:
                logger.warning("No persistent database available for preloading")
                return False
        except Exception as e:
            logger.error(f"Failed to preload user data: {e}")
            return False

async def check_database_health(db_instance):
    """Check if database is healthy and responsive"""
    try:
        if TEST_MODE:
            # For test mode, just check if the instance exists
            return db_instance is not None
        else:
            # For production, try a simple database operation
            if hasattr(db_instance, 'players') and db_instance.players is not None:
                # Try to count documents (lightweight operation)
                count = await db_instance.players.count_documents({})
                logger.info(f"✅ Database health check passed - found {count} player documents")
                return True
            else:
                logger.error("❌ Database health check failed - players collection not available")
                return False
    except Exception as e:
        logger.error(f"❌ Database health check failed: {e}")
        return False

async def handle_database_error(context, error):
    """Handle database-related errors and attempt recovery"""
    logger.error(f"Database error detected: {error}")

    # Check if it's a connection error
    if "connection" in str(error).lower() or "timeout" in str(error).lower():
        logger.info("🔄 Attempting database reconnection...")

        # Try to reinitialize database
        db_instance = await initialize_database()
        if db_instance is not None:
            global global_db
            global_db = db_instance

            # Update application bot_data if application exists
            if application is not None:
                application.bot_data["db"] = global_db
                logger.info("✅ Database reconnected successfully")
                return True
            else:
                logger.error("❌ Application not available for database update")
        else:
            logger.error("❌ Database reconnection failed")

    return False


# Validate required environment variables
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN or TEST_BOT_TOKEN environment variable is not set")

if not MONGODB_URI:
    raise ValueError("MONGODB_URI environment variable is not set")

# Configure logging based on DEBUG setting
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
logger = logging.getLogger(__name__)

# Log environment info
logger.info(f"Environment: {ENV}")
logger.info(f"Using polling: {USE_POLLING}")
logger.info(f"Database: {DB_NAME}")
logger.info(f"Debug mode: {DEBUG}")
logger.info(f"Test mode: {TEST_MODE}")
logger.info("✅ Environment variables loaded successfully from .env file")

# Global variables
application = None
app_initialized = False
global_db = None

# Initialize database and services
async def initialize_database():
    """Initialize database and return the database instance"""
    global global_db

    logger.info("🔄 Initializing database connection...")

    try:
        if TEST_MODE:
            # Use local memory database for test mode
            logger.info("🧪 Using Local Memory Database (Test Mode)")
            if global_db is None or not isinstance(global_db, LocalMemoryDatabase):
                global_db = local_db
                await global_db.init_db()
        else:
            # Use persistent MongoDB connection for production
            if global_db is None:
                logger.info("💾 Connecting to MongoDB database")
                motor_db = await get_persistent_database()
                if motor_db is not None:
                    global_db = Database()
                    await global_db.init_db(motor_db)

                    # Apply battle system fixes if needed
                    from game.battle_fix import apply_battle_fixes
                    fixes_applied = await apply_battle_fixes(global_db)
                    if fixes_applied:
                        logger.info("Applied battle system fixes")
                else:
                    logger.error("❌ Failed to get database instance")
                    return None

        # Verify database is working
        if global_db is not None:
            logger.info("✅ Database connection established successfully")
            if TEST_MODE:
                logger.info("🧪 Local Memory Database loaded - NO PERSISTENT STORAGE")
                if isinstance(global_db, LocalMemoryDatabase) and hasattr(global_db, "get_stats"):
                    try:
                        stats = global_db.get_stats()
                        logger.info(f"📊 Local DB Stats: Players: {stats['players']}, Characters: {stats['characters']}, Titans: {stats['titans']}")
                    except Exception as e:
                        logger.error(f"Error getting stats: {e}")
            else:
                logger.info(f"� Connected to database: {DB_NAME}")
        else:
            logger.error("❌ Database initialization failed")
            return None

        return global_db

    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}", exc_info=True)
        return None

# Initialize shop system
async def initialize_shop_system():
    """Initialize shop system and return the instance"""
    try:
        logger.info("� Initializing shop system...")
        shop_system = ShopSystem()
        logger.info(f"✅ Shop system loaded with {len(shop_system.shop_items)} items")
        return shop_system
    except Exception as e:
        logger.error(f"❌ Failed to initialize shop system: {e}")
        return None

async def initialize_bot():
    """Initialize all bot components in the correct order"""
    global application, global_db

    logger.info("� Starting bot initialization...")

    # Step 1: Initialize database first
    db_instance = await initialize_database()
    if db_instance is None:
        logger.error("❌ Database initialization failed - cannot continue")
        return False
    global_db = db_instance

    # Step 2: Initialize shop system
    shop_system = await initialize_shop_system()
    if shop_system is None:
        logger.error("❌ Shop system initialization failed - cannot continue")
        return False

    # Step 3: Create application
    logger.info("🤖 Creating Telegram application...")
    try:
        from telegram.request import HTTPXRequest
        # Set a longer connection timeout to handle potential network issues
        request = HTTPXRequest(connect_timeout=30.0, read_timeout=20.0)
        if not TELEGRAM_TOKEN:
            logger.error("❌ TELEGRAM_TOKEN is not set")
            return False
        application = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
        logger.info("✅ Application created successfully")
    except Exception as e:
        logger.error(f"❌ Failed to create application: {e}")
        return False

    # Step 4: Store global data in application
    logger.info("💾 Storing global data in application...")
    application.bot_data["db"] = global_db
    application.bot_data["shop_system"] = shop_system
    application.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}

    # Step 5: Set up error handler
    await setup_error_handler(application)

    # Step 6: Register all handlers
    logger.info("📋 Registering command handlers...")
    setup_handlers(application)

    # Step 7: Start schedulers
    logger.info("⏰ Starting schedulers...")
    try:
        # Start the midnight tax scheduler with bot instance
        start_scheduler(application.bot)
        logger.info("✅ Tax scheduler started")

        # Start scheduled tasks
        start_scheduled_tasks(application.bot)
        logger.info("✅ Scheduled tasks started")

        # Start stats scheduler
        if global_db is not None:
            await start_stats_scheduler(global_db)
            logger.info("✅ Stats scheduler started")
        else:
            logger.warning("⚠️ Cannot start stats scheduler: Database not initialized")
    except Exception as e:
        logger.error(f"❌ Failed to start schedulers: {e}")
        # Don't fail completely if schedulers fail
        pass

    logger.info("🎉 Bot initialization completed successfully!")
    return True

# Register all command and callback handlers
def setup_handlers(application):
    """Register all command and callback handlers"""

    # User commands (protected only by disable) - with auto user data loading
    application.add_handler(CommandHandler("start", auto_load_user_data(disable_protected(start_character_selection))))
    application.add_handler(CommandHandler("inv", auto_load_user_data(disable_protected(profile))))
    application.add_handler(CommandHandler("explore", auto_load_user_data(disable_protected(explore))))
    application.add_handler(CommandHandler("open", auto_load_user_data(disable_protected(open_keyboard))))
    application.add_handler(CommandHandler("close", auto_load_user_data(disable_protected(close_keyboard))))
    application.add_handler(CommandHandler("map", auto_load_user_data(disable_protected(show_map))))
    application.add_handler(CommandHandler("travel", auto_load_user_data(disable_protected(travel_command))))
    application.add_handler(CommandHandler("shop", auto_load_user_data(disable_protected(shop_command))))
    application.add_handler(CommandHandler("status", auto_load_user_data(disable_protected(profile))))
    application.add_handler(CommandHandler("buy", auto_load_user_data(disable_protected(buy_command))))
    application.add_handler(CommandHandler("referral", auto_load_user_data(disable_protected(referral_info))))
    application.add_handler(CommandHandler("chars", auto_load_user_data(disable_protected(show_characters))))
    application.add_handler(CommandHandler("char", auto_load_user_data(disable_protected(char_detail))))
    application.add_handler(CommandHandler("give", auto_load_user_data(disable_protected(give_command))))
    application.add_handler(CommandHandler("add", auto_load_user_data(disable_protected(add_resource_command))))
    application.add_handler(CommandHandler("remove", auto_load_user_data(disable_protected(add_resource_command))))
    application.add_handler(CommandHandler("stats", auto_load_user_data(disable_protected(stats_command))))
    application.add_handler(CommandHandler("missions", auto_load_user_data(disable_protected(missions_command))))
    application.add_handler(CommandHandler("resetmission", auto_load_user_data(disable_protected(reset_mission_command))))
    application.add_handler(CommandHandler("remission", auto_load_user_data(disable_protected(remission_command))))

    # Mod/owner commands (not protected by disable)
    application.add_handler(CommandHandler("monitor", monitor_command))
    application.add_handler(CommandHandler("nuke", reset_handler))
    application.add_handler(CommandHandler("bfb", ban_user))
    application.add_handler(CommandHandler("ubfb", unban_user))
    application.add_handler(CommandHandler("mod", promote_mod))
    application.add_handler(CommandHandler("demod", demote_mod))
    application.add_handler(CommandHandler("mm", maintenance))
    application.add_handler(CommandHandler("disablecmd", disable_command))
    application.add_handler(CommandHandler("enablecmd", enable_command))
    application.add_handler(CommandHandler("dbdiag", diagnostic_db_command))
    application.add_handler(CommandHandler("checkgroup", check_group_record))
    application.add_handler(CommandHandler("taxstatus", tax_status_command))
    application.add_handler(CommandHandler("forcetax", force_tax_check_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))

    # Test mode only commands
    if TEST_MODE:
        application.add_handler(CommandHandler("cleardb", clear_local_db_command))
        application.add_handler(CommandHandler("dbstatus", local_db_status_command))
        application.add_handler(CommandHandler("loaddata", load_user_data_command))

    # Bank system handlers
    application.add_handler(CommandHandler("bank", disable_protected(handle_bank_command)))
    application.add_handler(CommandHandler("deposit", disable_protected(handle_deposit_command)))
    application.add_handler(CommandHandler("withdraw", disable_protected(handle_withdrawal_command)))
    application.add_handler(CallbackQueryHandler(handle_open_bank_callback, pattern="^bank_open_account$"))

    # PVP system handlers
    application.add_handler(CommandHandler("pvp", disable_protected(pvp_command)))
    application.add_handler(CallbackQueryHandler(pvp_callback_handler, pattern="^pvp_"))

    application.add_handler(CommandHandler("spin", disable_protected(spin_command)))
    application.add_handler(CallbackQueryHandler(spin_callback_handler, pattern="^spin_"))

    application.add_handler(CommandHandler("use", disable_protected(use_command)))

    # Broadcast handlers (more specific patterns first)
    application.add_handler(CallbackQueryHandler(confirm_broadcast_callback, pattern=r"^confirm_broadcast$"))
    application.add_handler(CallbackQueryHandler(broadcast_type_callback, pattern=r"^broadcast_type_"))
    application.add_handler(CallbackQueryHandler(broadcast_location_callback, pattern=r"^broadcast_location_"))
    application.add_handler(CallbackQueryHandler(vote_options_callback, pattern=r"^vote_options_"))
    application.add_handler(CallbackQueryHandler(custom_options_count_callback, pattern=r"^custom_count_"))
    application.add_handler(CallbackQueryHandler(end_voting_callback, pattern=r"^end_voting$"))
    application.add_handler(CallbackQueryHandler(vote_callback, pattern=r"^vote_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_custom_option))

    # Character selection and team management (general patterns after specific ones)
    application.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
    application.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
    application.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_.*$"))
    application.add_handler(CallbackQueryHandler(create_character, pattern=r"^location_"))
    application.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
    application.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
    application.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
    application.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
    application.add_handler(CallbackQueryHandler(remove_from_team, pattern="^remove_from_team_"))
    application.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
    application.add_handler(CallbackQueryHandler(clear_team, pattern="^clear_team$"))
    application.add_handler(CallbackQueryHandler(back_from_manage_team, pattern="^back_from_manage_team$"))

    # Profile and inventory
    application.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    application.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    application.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    application.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    application.add_handler(CallbackQueryHandler(view_military, pattern="^view_military$"))
    application.add_handler(CallbackQueryHandler(view_miscellaneous, pattern="^view_miscellaneous$"))
    application.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    application.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))

    # Character detail handlers
    application.add_handler(CallbackQueryHandler(fill_gas, pattern=r"^fill_gas_"))
    application.add_handler(CallbackQueryHandler(view_weapons_char, pattern=r"^view_weapons_"))
    application.add_handler(CallbackQueryHandler(equip_weapon, pattern=r"^equip_weapon_"))
    application.add_handler(CallbackQueryHandler(view_abilities, pattern=r"^view_abilities_"))
    application.add_handler(CallbackQueryHandler(char_detail_callback, pattern=r"^char_detail_"))
    application.add_handler(CallbackQueryHandler(exit_profile, pattern=r"^exit_profile$"))

    # Battle and travel
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    application.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    application.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    application.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))

    # Mission handlers
    application.add_handler(CallbackQueryHandler(missions_callback_handler, pattern=r"^mission_"))
    application.add_handler(CallbackQueryHandler(reset_mission_callback_handler, pattern=r"^reset_"))

    # Shop and purchases
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))

    # Group membership handler
    application.add_handler(ChatMemberHandler(group_update_handler, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER | ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, group_update_handler))

    # Generic button handler (should be last before fallback)
    application.add_handler(CallbackQueryHandler(button, pattern=r"^[A-Z0-9]+$"))

    # Fallback handler (must be absolutely last)
    application.add_handler(CallbackQueryHandler(button_callback))

    # Text message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button))

    logger.info("✅ All handlers registered successfully")

# Shop command handler for /shop
@maintenance_protected
@ban_protected
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id) if update.effective_user else None
    try:
        shop_system = context.bot_data.get("shop_system")
        if not shop_system:
            if update.message:
                await update.message.reply_text("Shop system not initialized. Please try again later.")
            return
        # Always set shop_items and hidden_items in context.bot_data for consistency
        context.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}
        text, reply_markup = await shop_system.show_shop(context, user_id)
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in shop_command: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while showing the shop.")

# Test mode command to clear local database
async def clear_local_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all data from local memory database (test mode only)"""
    if not TEST_MODE:
        if update.message:
            await update.message.reply_text("❌ This command is only available in test mode.")
        return

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Check if user is owner (you can modify this check as needed)
    # For now, allow anyone to use it in test mode
    try:
        local_db.clear_all()
        stats = local_db.get_stats()
        message = (
            "🧪 *Local Database Cleared!*\n\n"
            "📊 Current Stats:\n"
            f"• Players: {stats['players']}\n"
            f"• Characters: {stats['characters']}\n"
            f"• Titans: {stats['titans']}\n"
            f"• Bank Accounts: {stats['bank_accounts']}\n"
            f"• Bans: {stats['bans']}\n\n"
            "✅ All test data has been reset."
        )
        if update.message:
            await update.message.reply_text(message, parse_mode="Markdown")
        logger.info(f"🧪 Local database cleared by user {user_id}")
    except Exception as e:
        logger.error(f"Error clearing local database: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to clear local database.")

# Test mode command to show local database status
async def local_db_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show status of local memory database (test mode only)"""
    if not TEST_MODE:
        if update.message:
            await update.message.reply_text("❌ This command is only available in test mode.")
        return

    if not update.effective_user:
        return

    try:
        message = local_db.get_status_message()
        if update.message:
            await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error getting local database status: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to get database status.")

# Auto-load user data when any command is executed
async def ensure_user_data_loaded(update: Update, context):
    """Ensure user data is loaded before executing any command"""
    if TEST_MODE and update.effective_user:
        user_id = str(update.effective_user.id)
        if hasattr(context.bot_data, 'get') and 'db' in context.bot_data:
            db = context.bot_data['db']
            if hasattr(db, 'ensure_user_data_loaded'):
                await db.ensure_user_data_loaded(user_id)

# Wrapper for commands to auto-load user data
def auto_load_user_data(command_handler):
    """Decorator to auto-load user data before executing commands"""
    async def wrapper(update: Update, context):
        await ensure_user_data_loaded(update, context)
        return await command_handler(update, context)
    return wrapper

# Test mode command to load user data from production
async def load_user_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Load user data from production database (test mode only)"""
    if not TEST_MODE:
        if update.message:
            await update.message.reply_text("❌ This command is only available in test mode.")
        return

    if not update.effective_user:
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)

    try:
        # Show loading message
        loading_msg = await update.message.reply_text("📥 Loading your data from production database...")

        # Load user data
        success = await local_db.preload_user_data(user_id_str)
        
        if success:
            stats = local_db.get_stats()
            message = (
                "✅ *Data Loaded Successfully!*\n\n"
                f"📊 Your Data:\n"
                f"• Player: {'✅' if user_id_str in local_db.players else '❌'}\n"
                f"• Characters: {len([c for c in local_db.characters.keys() if c.startswith(f'{user_id_str}_')])}\n"
                f"• Active Titan: {'✅' if f'{user_id_str}_titan' in local_db.titans else '❌'}\n"
                f"• Bank Account: {'✅' if user_id_str in local_db.bank_accounts else '❌'}\n\n"
                f"🎮 You can now continue your game in test mode!"
            )
            await loading_msg.edit_text(message, parse_mode="Markdown")
            logger.info(f"🧪 User {user_id} loaded data from production database")
        else:
            await loading_msg.edit_text("❌ Failed to load data from production database. Please check your connection.")
            
    except Exception as e:
        logger.error(f"Error loading user data: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to load data from production database.")

async def setup_error_handler(application):
    """Set up error handler for the bot"""
    
    async def error_handler(update: object, context):
        if isinstance(context.error, asyncio.CancelledError):
            logger.warning(f"Task cancelled for update {update}")
            return
                
        # Special handling for rate limiting errors
        from telegram.error import RetryAfter
        if isinstance(context.error, RetryAfter):
            retry_seconds = context.error.retry_after
            logger.warning(f"Rate limited. Retry after {retry_seconds} seconds")
            
            # For rate limit errors, only notify the user if possible
            if isinstance(update, Update) and getattr(update, "effective_message", None):
                try:
                    if update.effective_message:
                        await asyncio.sleep(min(retry_seconds, 5))  # Wait a bit before sending the message
                        await update.effective_message.reply_text(
                            f"Bot is being rate limited. Please try again in {int(retry_seconds)} seconds."
                        )
                except Exception as e:
                    logger.error(f"Failed to notify user about rate limit: {e}")
            return

        # Handle database connection errors
        if "connection" in str(context.error).lower() or "timeout" in str(context.error).lower():
            recovery_success = await handle_database_error(context, context.error)
            if recovery_success:
                # If recovery successful, don't log as error
                logger.info("Database error recovered successfully")
                return
                
        logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
            
        # Prepare detailed error message
        command = None
        if isinstance(update, Update):
            if hasattr(update, "message") and update.message is not None and hasattr(update.message, "text") and update.message.text:
                command = update.message.text
            elif hasattr(update, "callback_query") and update.callback_query is not None and hasattr(update.callback_query, "data") and update.callback_query.data:
                command = f"Callback: {update.callback_query.data}"
        user_id = getattr(update, "effective_user", None)
        user_id_str = getattr(user_id, "id", "N/A") if user_id is not None else "N/A"
        
        error_text = (
            f"⚠️ <b>Error Occurred</b>\n"
            f"<b>Command:</b> <code>{command}</code>\n"
            f"<b>User:</b> <code>{user_id_str}</code>\n"
            f"<b>Error:</b>\n<pre>{repr(context.error)}</pre>\n"
        )
        
        # In test mode, log to console
        if TEST_MODE:
            logger.error(f"ERROR: {error_text}")
            if DEBUG:
                import traceback
                traceback.print_exc()

    application.add_error_handler(error_handler)

async def database_health_monitor():
    """Monitor database health periodically"""
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes

            if not TEST_MODE and global_db is not None:
                health_ok = await check_database_health(global_db)
                if not health_ok:
                    logger.warning("⚠️ Database health check failed, attempting recovery...")
                    recovery_success = await handle_database_error(None, "Health check failed")
                    if recovery_success:
                        logger.info("✅ Database health restored")
                    else:
                        logger.error("❌ Database health recovery failed")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in database health monitor: {e}")
            await asyncio.sleep(60)  # Wait a minute before retrying

# Global instances
local_db = LocalMemoryDatabase()
health_monitor_task = None

async def main():
    """Main bot runner with proper initialization sequence"""
    print("🤖 ATTACK ON TITAN BOT - LOCAL TEST MODE")
    print("=" * 50)
    print(f"📋 Environment: {ENV}")
    print(f"📋 Database: {DB_NAME}")
    print(f"📋 Debug Mode: {DEBUG}")
    print(f"📋 Test Mode: {TEST_MODE}")
    if TEST_MODE:
        print("🧪 TEST MODE ACTIVE - Using LOCAL MEMORY DATABASE")
        print("💾 Data will be loaded from PRODUCTION database")
        print("⚠️  CHANGES will NOT be saved to production database")
        print("� All data is stored in memory and lost on restart")
        print("🗑️ Use /cleardb to reset all test data")
        print("📥 Use /loaddata to manually load your data from production")
    else:
        print("💾 PRODUCTION MODE - Using MongoDB database")
    print(f"📋 Bot Token: {'*' * 10 + TELEGRAM_TOKEN[-10:] if TELEGRAM_TOKEN and len(TELEGRAM_TOKEN) > 10 else 'Not Set'}")
    print("=" * 50)

    # Validate environment
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: No bot token found in environment variables!")
        print("   Make sure TEST_BOT_TOKEN or TELEGRAM_TOKEN is set in .env file")
        return

    if not MONGODB_URI:
        print("❌ ERROR: MongoDB URI not found in environment variables!")
        print("   Make sure MONGODB_URI is set in .env file")
        return

    # Initialize bot with all components
    success = await initialize_bot()
    if not success:
        print("❌ ERROR: Failed to initialize bot")
        return

    # Final database readiness check
    # Database is already initialized, so we consider it ready
    
    # Start database health monitor for production mode
    global health_monitor_task
    if not TEST_MODE:
        health_monitor_task = asyncio.create_task(database_health_monitor())
        logger.info("✅ Database health monitor started")

    print("\n🚀 Starting bot...")
    print("🤖 Bot is now running in polling mode! Send commands to your test bot in Telegram")
    print("Press Ctrl+C to stop the bot")

    # Set up signal handlers for graceful shutdown
    stop_event = asyncio.Event()
    def stop_bot(signum, frame):
        print("\n⏱️ Stopping bot...")
        stop_event.set()
    signal.signal(signal.SIGINT, stop_bot)
    signal.signal(signal.SIGTERM, stop_bot)

    # Start the bot with polling
    try:
        # Initialize and start the application
        await application.initialize()
        await application.start()
        app_initialized = True

        # Start polling for updates
        if application.updater:
            await application.updater.start_polling(drop_pending_updates=True)
            print("✅ Bot is now polling for updates")

            # Wait until stop signal
            await stop_event.wait()
        else:
            logger.error("Updater not initialized - cannot start polling")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
    finally:
        # Perform graceful shutdown
        print("\n⏱️ Shutting down...")
        
        # Cancel health monitor task
        if health_monitor_task and not health_monitor_task.done():
            health_monitor_task.cancel()
            try:
                await health_monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("✅ Database health monitor stopped")
        
        if application:
            if application.updater:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
        print("👋 Bot stopped")

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "cannot run the event loop" in str(e).lower():
            # Loop is running, run main in the existing loop
            loop = asyncio.get_running_loop()
            loop.create_task(main())
            # To keep the script running
            def stop_loop(signum, frame):
                loop.stop()
            signal.signal(signal.SIGINT, stop_loop)
            try:
                loop.run_forever()
            except KeyboardInterrupt:
                print("\n👋 Bot stopped by user")
        else:
            raise
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)