import os
import random
import datetime
from typing import Optional, List, Dict
from dotenv import load_dotenv
from database.models import Character, Player, Titan, Equipment, CharacterStats, SPECIAL_ABILITIES, generate_titan_name, generate_titan_hp, generate_titan_xp
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
            # Test the connection
            await self.db.command('ping')
            logger.info("Database connection verified (Motor)")
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            logger.info("Continuing with limited functionality - database operations may be slower")
            raise

    # Player operations
    async def create_player(self, user_id: int, username: str, name: str, referral_code: str = None, referred_by: str = None) -> Player:
        try:
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
                referral_milestones={},
            )
            logger.info(f"Creating player: {player}")
            await self.players.insert_one(player.dict())
            return player
        except Exception as e:
            logger.error(f"Failed to create player: {e}")
            raise

    async def get_player(self, user_id: str) -> Optional[Player]:
        try:
            if self.players is None:
                await self.init_db()  # Initialize if not already done
            if self.players is None:
                raise ConnectionError("Database connection failed")
                
            player_data = await self.players.find_one({"user_id": user_id})
            return Player(**player_data) if player_data else None
        except (PyMongoError, ConnectionError) as e:
            logger.error(f"Failed to get player: {e}")
            raise

    async def update_player(self, user_id: int, update_data: Dict) -> Optional[Player]:
        try:
            # Ensure XP and total_xp are never negative
            if 'xp' in update_data:
                update_data['xp'] = max(0, update_data['xp'])
            if 'total_xp' in update_data:
                update_data['total_xp'] = max(0, update_data['total_xp'])
            if 'team' in update_data:
                update_data['team'] = [
                    member.dict() if hasattr(member, 'dict') else member
                    for member in update_data['team']
                ]
            update_data["updated_at"] = datetime.now(timezone.utc)
            result = await self.players.find_one_and_update(
                {"user_id": str(user_id)},
                {"$set": update_data},
                return_document=True
            )
            return Player(**result) if result else None
        except Exception as e:
            logger.error(f"Failed to update player: {e}")
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
                stats=CharacterStats(**stats_dict),  # Properly initialize
                gas=5000,
                max_gas=10000,
                rank="Cadet",
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={}
            )
            character.unlock_abilities()
            
            # Ensure the entire character is dumped before saving
            character_dict = character.dict()
            await self.characters.insert_one(character_dict)
            await self.add_character_to_player(user_id, name)
            return character
        except (PyMongoError, ValueError) as e:
            logger.error(f"Failed to create character: {e}")
            raise

    async def get_character(self, user_id: int, character_name: str) -> Optional[Character]:
        try:
            character_data = await self.characters.find_one({
                "user_id": str(user_id),
                "name": character_name
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
            character_dict = character.dict()  # Convert to dict
            # Ensure all passive abilities have 'unlocked' field for DB validation
            if 'passive_abilities' in character_dict:
                for ability in character_dict['passive_abilities']:
                    # Use 'is_unlocked' if present, else fallback to False
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

    # Titan operations
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
        abilities = random.sample(SPECIAL_ABILITIES[difficulty], k=min(2, len(SPECIAL_ABILITIES[difficulty])))
        special_abilities = abilities.copy()
        now = datetime.now(timezone.utc)
        titan = Titan(
            name=name,
            level=level,
            max_hp=max_hp,
            abilities=abilities,
            created_at=now,
            difficulty=difficulty,
            special_abilities=special_abilities,
            spawn_areas=unlocked_areas or [],
            drop_table={},
            xp_reward=generate_titan_xp(level, difficulty),
            min_level_requirement=level
        )
        return titan

    async def store_titan(self, user_id: str, titan: Titan):
        logger = logging.getLogger(__name__)
        logger.info(f"[DB] store_titan called with user_id: {user_id} (type: {type(user_id)})")
        titan_doc = titan.dict()
        titan_doc["user_id"] = user_id
        titan_doc["updated_at"] = datetime.now(timezone.utc)
        await self.titans.update_one(
            {"user_id": user_id},
            {"$set": titan_doc},
            upsert=True
        )

    async def get_titan(self, user_id: str) -> Optional[Titan]:
        logger = logging.getLogger(__name__)
        logger.info(f"[DB] get_titan called with user_id: {user_id} (type: {type(user_id)})")
        titan_data = await self.titans.find_one({"user_id": user_id})
        logger.info(f"[DB] get_titan found: {titan_data is not None}")
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
        logger.info(f"Generated new titan: {new_titan.name} (Level {new_titan.level}, HP: {new_titan.max_hp})")
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