import os
import random
import datetime
from typing import Optional, List, Dict
from dotenv import load_dotenv
from database.models import Character, Player, Titan, Equipment, CharacterStats, generate_titan_name, generate_titan_hp, generate_titan_xp
from database.schemas import Ability  # Use Ability instead of AbilityInfo
from database.characters import get_character_data
from database.db_instance import get_database
import logging
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class Database:
    def __init__(self):
        self.db: AsyncIOMotorDatabase = None
        self.characters = None
        self.players = None
        self.titans = None
        self.equipment = None
        self.shop_purchases = None
        self.shop_purchases_collection = None  # For shop_system compatibility
        self.bank_accounts = None
        self.bans = None

    async def init_db(self):
        try:
            self.db = await get_database()
            if self.db is None:
                raise ConnectionError("Failed to get database instance")
            self.characters = self.db.characters
            self.players = self.db.players
            self.players_collection = self.players 
            self.titans = self.db.titans
            self.equipment = self.db.equipment
            self.shop_purchases = self.db.shop_purchases
            self.shop_purchases_collection = self.db.shop_purchases  # Alias for shop_system
            self.bank_accounts = self.db.bank_accounts
            self.bans = self.db.bans
            # Test the connection
            await self.db.command('ping')
            # Create indexes for faster queries
            index_name = "user_id_1"
            indexes = await self.players.index_information()
            if index_name in indexes:
                await self.players.drop_index(index_name)
            await self.players.create_index("user_id", name=index_name, unique=True, background=True)
            char_index_name = "user_id_1_name_1"
            char_indexes = await self.characters.index_information()
            if char_index_name in char_indexes:
                await self.characters.drop_index(char_index_name)
            await self.characters.create_index([("user_id", 1), ("name", 1)], name=char_index_name, unique=True, background=True)
            await self.titans.create_index("user_id")
            await self.bank_accounts.create_index("user_id", name="user_id_1", unique=True, background=True)
            logger.info("Database connection verified (Motor) and indexes created")
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            logger.info("Continuing with limited functionality - database operations may be slower")
            raise

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
    async def create_player(self, user_id: int, username: str, name: str, referral_code: str = None, referred_by: str = None) -> Player:
        try:
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
            if self.players is None:
                await self.init_db() 
            if self.players is None:
                raise ConnectionError("Database connection failed")
            player_data = await self.players.find_one({"user_id": user_id}, {
                "user_id": 1, "username": 1, "name": 1, "level": 1, "xp": 1, "total_xp": 1,
                "gas": 1, "crystal": 1, "valor": 1, "marks": 1, "explore_count": 1,
                "owned_characters": 1, "location": 1, "travel": 1, "daily_explores": 1,
                "unlocked_areas": 1, "team": 1, "shop_refresh_date": 1, "shop_refresh_count": 1,
                "hcaptcha_verified": 1, "hcaptcha_start_time": 1, "explore_start_time": 1, "last_explore_time": 1,
                "inventory": 1
            })
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"get_player query time: {elapsed:.2f} ms")
            
            # Create a Player object
            if player_data:
                # Convert team members to proper TeamMember objects if present
                if "team" in player_data and player_data["team"]:
                    from database.models import TeamMember
                    player_data["team"] = [
                        TeamMember(**member) if isinstance(member, dict) else member
                        for member in player_data["team"]
                    ]
                return Player(**player_data)
            return None
        except (PyMongoError, ConnectionError) as e:
            logger.error(f"Failed to get player: {e}")
            raise

    async def update_player(self, user_id: int, update_data: Dict) -> Optional[Player]:
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
            update_data["updated_at"] = datetime.now(timezone.utc)
            result = await self.players.find_one_and_update(
                {"user_id": str(user_id)},
                {"$set": update_data},
                return_document=True
            )
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"update_player query time: {elapsed:.2f} ms")
            return Player(**result) if result else None
        except Exception as e:
            logger.error(f"Failed to update player: {e}")
            raise

    async def save_player(self, player: Player) -> Optional[Player]:
        """Save (update) the player document in the database."""
        try:
            player.updated_at = datetime.now(timezone.utc)
            player_dict = player.dict() if hasattr(player, 'dict') else player.__dict__
            
            # Properly serialize TeamMember objects in the team list
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

        
    async def get_player_characters(self, user_id: int) -> List[Character]:
        try:
            cursor = self.characters.find({"user_id": str(user_id)})
            characters = await cursor.to_list(None)
            return [Character(**char) for char in characters]
        except Exception as e:
            logger.error(f"Failed to get player characters: {e}")
            raise

    async def add_character_to_player(self, user_id: int, character_name: str) -> bool:
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
        """Delete a player by user_id."""
        try:
            await self.players.delete_one({"user_id": str(user_id)})
            logger.info(f"Deleted player with user_id: {user_id}")
        except Exception as e:
            logger.error(f"Failed to delete player: {e}")
            raise
            
    async def get_all_players(self) -> List[Player]:
        """Get all players from the database."""
        try:
            import time
            start = time.perf_counter()
            cursor = self.players.find({})
            players_data = await cursor.to_list(None)
            
            # Convert team members to proper TeamMember objects if present
            from database.models import TeamMember
            players = []
            for player_data in players_data:
                if "team" in player_data and player_data["team"]:
                    player_data["team"] = [
                        TeamMember(**member) if isinstance(member, dict) else member
                        for member in player_data["team"]
                    ]
                players.append(Player(**player_data))
            
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"get_all_players query time: {elapsed:.2f} ms")
            return players
        except Exception as e:
            logger.error(f"Failed to get all players: {e}")
            return []

    # Character operations
    async def create_character(self, user_id: str, name: str, character_type: str, current_hp: int) -> Character:
        """Create a new character."""
        try:
            char_data = get_character_data(character_type)
            if not char_data:
                raise ValueError(f"Character type {character_type} not found")
            
            # Ensure stats are properly dumped
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
                stats=CharacterStats(**stats_dict), 
                gas=5000,
                max_gas=5000,
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={}
            )
            character.unlock_abilities()
            character_dict = character.dict()
            if 'passive_abilities' in character_dict:
                for ability in character_dict['passive_abilities']:
                    ability['unlocked'] = ability.get('is_unlocked', False)
            await self.characters.insert_one(character_dict)
            await self.add_character_to_player(user_id, name)
            return character
        except (PyMongoError, ValueError) as e:
            logger.error(f"Failed to create character: {e}")
            raise

    async def get_character(self, user_id: int, character_name: str) -> Optional[Character]:
        try:
            # Use projection for faster reads
            character_data = await self.characters.find_one({
                "user_id": str(user_id),
                "name": character_name
            }, {
                "user_id": 1, "name": 1, "character_type": 1, "current_hp": 1, "level": 1,
                "xp": 1, "total_xp": 1, "stats": 1, "gas": 1, "max_gas": 1,
                "equipped_weapon": 1,
                "active_abilities": 1, "passive_abilities": 1, "ultimate_abilities": 1,
                "unlocked_abilities": 1
            })
            if character_data:
                return Character(**character_data)
            return None
        except Exception as e:
            logger.error(f"Failed to get character: {e}")
            raise

    async def update_character(self, character: Character) -> Character:
        try:
            character.updated_at = datetime.now(timezone.utc)
            character_dict = character.dict() 
            if 'passive_abilities' in character_dict:
                for ability in character_dict['passive_abilities']:
                    ability['unlocked'] = ability.get('is_unlocked', False)
            await self.characters.find_one_and_update(
                {
                    "user_id": character.user_id,
                    "name": character.name
                },
                {"$set": character_dict},  # Use the dumped dict
                return_document=True
            )
            return character
        except Exception as e:
            logger.error(f"Failed to update character: {e}")
            raise

    async def get_character_abilities(self, user_id: int, character_name: str) -> Dict[str, List[Ability]]:
        try:
            character = await self.get_character(user_id, character_name)
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

    async def get_character_level(self, user_id: int, character_name: str) -> int:
        try:
            character = await self.characters.find_one(
                {"user_id": str(user_id), "name": character_name},
                {"level": 1}
            )
            return character.get('level', 1) if character else 1
        except Exception as e:
            logger.error(f"Failed to get character level: {e}")
            raise


    async def generate_titan(self, player_level: int, unlocked_areas: List[str]) -> Optional[Titan]:
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
        return titan
        
    async def generate_multiple_titans(self, player_level: int, unlocked_areas: List[str], count: int = 3) -> List[Titan]:
        """Generate multiple titans at once for better performance."""
        titans = []
        try:
            # Determine difficulty based on player level once
            if player_level < 8:
                difficulty = "Easy"
            elif player_level < 15:
                difficulty = "Normal"
            else:
                difficulty = "Hard"
                
            now = datetime.now(timezone.utc)
            
            for _ in range(count):
                # Titan level: within -2 to +2 of player, but at least 1
                level = max(1, player_level + random.randint(-2, 2))
                
                # Generate titan data
                name = generate_titan_name(difficulty)
                max_hp = generate_titan_hp(level, difficulty)
                
                # Create titan
                titan = Titan(
                    name=name,
                    level=level,
                    max_hp=max_hp,
                    abilities=[],  # No abilities for now
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

    async def store_titan(self, user_id: str, titan: Titan):
        titan_doc = titan.dict()
        titan_doc["user_id"] = user_id
        titan_doc["updated_at"] = datetime.now(timezone.utc)
        await self.titans.update_one(
            {"user_id": user_id},
            {"$set": titan_doc},
            upsert=True
        )

    async def get_titan(self, user_id: str) -> Optional[Titan]:
        titan_data = await self.titans.find_one({"user_id": user_id})
        return Titan(**titan_data) if titan_data else None

    async def delete_titan(self, user_id: str):
        await self.titans.delete_one({"user_id": user_id})

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
        await self.players_collection.update_one(
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

    async def add_new_character_to_player(self, user_id: int, character_name: str) -> bool:
        try:
            char_data = get_character_data(character_name)
            if not char_data:
                return False
            existing_char = await self.get_character(user_id, character_name)
            if existing_char:
                return False
            await self.create_character(user_id, character_name, character_name, current_hp=char_data.base_stats.get("current_hp", 100))
            return True
        except Exception as e:
            logger.error(f"Failed to add new character to player: {e}")
            raise

    async def get_connection_stats(self) -> Optional[Dict]:
        try:
            server_info = await self.db.command("serverStatus")
            return {
                "connections_current": server_info.get("connections", {}).get("current", 0),
                "connections_available": server_info.get("connections", {}).get("available", 0),
                "uptime": server_info.get("uptime", 0)
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return None

    async def record_purchase(self, user_id: str, item_key: str):
        """Record a shop purchase for tracking stock/cooldown."""
        await self.shop_purchases_collection.insert_one({
            "user_id": user_id,
            "item_key": item_key,
            "purchase_date": datetime.now(timezone.utc)
        })