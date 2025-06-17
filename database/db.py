import os
import random
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, List, Dict
from datetime import datetime
from dotenv import load_dotenv
from database.models import Character, Player, Titan, Equipment, AbilityInfo, CharacterStats, scale_titan
from database.characters import get_character_data
import datetime
import logging
import certifi
import ssl
import dns.resolver
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class Database:
    def __init__(self):
        # MongoDB Atlas connection string
        mongodb_uri = os.getenv("MONGODB_URI")
        if not mongodb_uri:
            raise ValueError("MONGODB_URI environment variable is not set")
        
        try:
            # Configure SSL context
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            
            # Configure DNS resolver
            dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
            dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']  # Google DNS servers
            
            # Connect to MongoDB with SSL configuration
            self.client = AsyncIOMotorClient(
                mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=30000,
                waitQueueTimeoutMS=10000,
                retryWrites=True,
                retryReads=True,
                tls=True,
                tlsAllowInvalidCertificates=False,
                tlsCAFile=certifi.where()
            )
            
            # Test the connection
            self.client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        self.db = self.client.attackontitan
        self.characters = self.db.characters
        self.players = self.db.players
        self.titans = self.db.titans
        self.equipment = self.db.equipment

    async def init_db(self):
        try:
            # Create indexes with updated options
            await self.players.create_index("user_id", unique=True, background=True)
            await self.equipment.create_index("name", unique=True, background=True)
            logger.info("Database indexes created successfully")
        except Exception as e:
            logger.error(f"Failed to create indexes: {e}")
            raise

    # Player operations
    async def create_player(self, user_id: int, username: str, name: str) -> Player:
        try:
            player = Player(
                user_id=str(user_id),  # Store as integer
                username=username,
                name=name,
                level=1,
                xp=0,
                total_xp=0,
                gas=1000,
                valor=0,  # Ensure this is included
                crystal=0,
                marks=0,
                explore_count=0,
                owned_characters=[],
                team=[]
            )
            logger.info(f"Creating player: {player}")
            await self.players.insert_one(player.model_dump())
            return player
        except Exception as e:
            logger.error(f"Failed to create player: {e}")
            raise

    async def get_player(self, user_id: int) -> Optional[Player]:
        try:
            player_data = await self.players.find_one({"user_id": user_id})  # Use integer
            return Player(**player_data) if player_data else None
        except Exception as e:
            logger.error(f"Failed to get player: {e}")
            raise

    async def update_player(self, user_id: int, update_data: Dict) -> Optional[Player]:
        try:
            # Convert team members to dictionaries if they're model instances
            if 'team' in update_data:
                update_data['team'] = [
                    member.model_dump() if hasattr(member, 'model_dump') else member
                    for member in update_data['team']
                ]
        
            update_data["updated_at"] = datetime.datetime.now(datetime.timezone.utc)
            result = await self.players.find_one_and_update(
                {"user_id": user_id},
                {"$set": update_data},
                return_document=True
            )
            return Player(**result) if result else None
        except Exception as e:
            logger.error(f"Failed to update player: {e}")
            raise

    async def get_player_characters(self, user_id: int) -> List[Character]:
        try:
            cursor = self.characters.find({"user_id": user_id})  # Use integer
            characters = await cursor.to_list(length=None)
            return [Character(**char) for char in characters]
        except Exception as e:
            logger.error(f"Failed to get player characters: {e}")
            raise

    async def add_character_to_player(self, user_id: int, character_name: str) -> bool:
        try:
            result = await self.players.update_one(
                {"user_id": user_id},  # Use integer
                {"$addToSet": {"owned_characters": character_name}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to add character to player: {e}")
            raise

    # Character operations
    async def create_character(self, user_id: int, name: str, character_type: str, birthplace: str, current_hp: int) -> Character:
        try:
            char_data = get_character_data(character_type)
            if not char_data:
                raise ValueError(f"Character type {character_type} not found")

            character = Character(
                user_id=user_id,
                name=name,
                character_type=character_type,
                birthplace=birthplace,
                current_hp=current_hp,
                level=1,
                xp=0,
                total_xp=0,
                stats=CharacterStats(**char_data.base_stats),
                gas=5000,
                rank="Cadet",
                active_abilities=[],
                passive_abilities=[],
                ultimate_abilities=[],
                unlocked_abilities={}
            )

            # After creating the character
            character.unlock_abilities()

            await self.characters.insert_one(character.model_dump())

            # Add character to player's owned characters
            await self.add_character_to_player(user_id, name)

            return character
        except Exception as e:
            logger.error(f"Failed to create character: {e}")
            raise

    async def get_character(self, user_id: int, character_name: str) -> Optional[Character]:
        try:
            character_data = await self.characters.find_one({
                "user_id": user_id,
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
            character.updated_at = datetime.datetime.utcnow()
            await self.characters.find_one_and_update(
                {
                    "user_id": character.user_id,
                    "name": character.name
                },
                {"$set": character.model_dump()},
                return_document=True
            )
            return character
        except Exception as e:
            logger.error(f"Failed to update character: {e}")
            raise

    async def get_character_abilities(self, user_id: int, character_name: str) -> Dict[str, List[AbilityInfo]]:
        try:
            character = await self.get_character(user_id, character_name)
            if not character:
                return {"active": [], "passive": []}
            
            return {
                "active": character.active_abilities,
                "passive": character.passive_abilities
            }
        except Exception as e:
            logger.error(f"Failed to get character abilities: {e}")
            raise

    # Titan operations
    async def create_titan(self, titan: Titan) -> Titan:
        try:
            await self.titans.insert_one(titan.model_dump())
            return titan
        except Exception as e:
            logger.error(f"Failed to create titan: {e}")
            raise

    async def get_titan(self, titan_name: str) -> Optional[Titan]:
        try:
            # Try direct name match
            titan_data = await self.titans.find_one({"name": titan_name})
            if not titan_data:
                # Try normalized internal name
                normalized = titan_name.lower().replace(" ", "_")
                titan_data = await self.titans.find_one({"internal_name": normalized})
            return Titan(**titan_data) if titan_data else None
        except Exception as e:
            logger.error(f"Failed to get titan: {e}")
            raise

    async def get_random_titan(self, min_level: int, max_level: int, target_level: int, unlocked_areas: List[str] = None) -> Titan:
        """Get a random titan (existing or newly scaled)."""
        # Try to find an existing titan first
        query = {
            "level": {"$gte": min_level, "$lte": max_level},
            "min_level_requirement": {"$lte": target_level},
            "spawn_areas": {"$in": unlocked_areas} if unlocked_areas else {"$exists": True},
            "is_template": {"$ne": True},  # Skip templates
            "is_scaled": True  # Only fetch scaled titans
        }
    
        pipeline = [{"$match": query}, {"$sample": {"size": 1}}]
        titans = await self.titans.aggregate(pipeline).to_list(1)
    
        if titans:
            return Titan(**titans[0])
    
        # If no titan exists, create and save a new scaled one
        template = await self.titans.find_one({"is_template": True})
        if not template:
            template = {
                "name": "Generic Titan",
                "level": 1,
                "max_hp": 300,
                "abilities": ["Basic Attack"],
                "difficulty": "Normal",
                "spawn_areas": ["Trost District", "Karanes District", "Shiganshina District"],
                "min_level_requirement": 1,
                "created_at": datetime.utcnow(),
                "is_template": True
            }
            await self.titans.insert_one(template)
    
        scaled_titan = scale_titan(template, target_level)
        await self.titans.insert_one(scaled_titan)  # Save for future use
        return Titan(**scaled_titan)


    async def get_available_titans(self, character_level: int) -> List[Titan]:
        try:
            cursor = self.titans.find({"min_level_requirement": {"$lte": character_level}, "is_template": {"$ne": True}})
            titans = await cursor.to_list(length=None)
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
            equipment_list = await cursor.to_list(length=None)
            return [Equipment(**equip) for equip in equipment_list]
        except Exception as e:
            logger.error(f"Failed to get equipment by type: {e}")
            raise

    async def calculate_rewards(self, titan: Titan, character: Character, player: Player, explore_count: int, valor: int, crystal: int) -> dict:
        """Calculate rewards for defeating the titan with new random system."""
        rewards = {
            "xp": titan.xp_reward,  # Keep original XP
            "marks": random.randint(90, 150),  # New marks range
            "titan_crystals": 0,  # Initialize
            "valor_points": 0,  # Initialize
        }

        # Valor points - rare drop (10-25) with explore count check
        if random.random() < 0.3:  # 30% chance
            required_explore = random.randint(30, 40)
            if hasattr(character, 'player') and character.player.explore_count >= required_explore:
                rewards["valor_points"] = random.randint(10, 25)

        # Crystals - more common (2-4) with explore count check
        if random.random() < 0.6:  # 60% chance
            required_explore = random.randint(10, 15)
            if hasattr(character, 'player') and character.player.explore_count >= required_explore:
                rewards["titan_crystals"] = random.randint(2, 4)

        return rewards

    async def add_new_character_to_player(self, user_id: int, character_name: str) -> bool:
        """Add a new character to an existing player's owned characters."""
        try:
            char_data = get_character_data(character_name)
            if not char_data:
                return False

            # Check if character already exists
            existing_char = await self.get_character(user_id, character_name)
            if existing_char:
                return False

            # Create new character
            await self.create_character(user_id, character_name, character_name, birthplace=char_data.birthplace, current_hp=char_data.base_stats.get("current_hp", 100))
            return True
        except Exception as e:
            logger.error(f"Failed to add new character to player: {e}")
            raise
