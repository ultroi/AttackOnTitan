import os
import random
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, List, Dict
import datetime
from dotenv import load_dotenv
from database.models import Character, Player, Titan, Equipment, AbilityInfo, CharacterStats, SPECIAL_ABILITIES, TITAN_NAME_VARIANTS, TITAN_DESCRIPTORS, HP_RANGES, generate_titan_name, generate_titan_hp, generate_titan_xp
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
            
            # Connection will be tested when first used
            logger.info("MongoDB client initialized")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        self.db = self.client.attackontitan
        self.characters = self.db.characters
        self.players = self.db.players
        self.titans = self.db.titans
        self.equipment = self.db.equipment
        self.shop_purchases = self.db.shop_purchases

    async def init_db(self):
        try:
            # Test connection first with timeout
            await self.client.admin.command('ping', serverSelectionTimeoutMS=10000)
            logger.info("Database connection verified")
            
            # Create indexes with retry logic and longer timeout
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.players.create_index("user_id", unique=True, background=True)
                    await self.equipment.create_index("name", unique=True, background=True)
                    logger.info("Database indexes created successfully")
                    break
                except Exception as index_error:
                    if attempt == max_retries - 1:
                        logger.warning(f"Failed to create indexes after {max_retries} attempts: {index_error}")
                        logger.info("Continuing without indexes - they will be created automatically on first use")
                        break
                    else:
                        logger.warning(f"Index creation attempt {attempt + 1} failed: {index_error}, retrying...")
                        await asyncio.sleep(2)  # Wait 2 seconds before retry
                        
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            logger.info("Continuing with limited functionality - database operations may be slower")
            # Don't raise the exception, allow the bot to start with degraded functionality

    # Player operations
    async def create_player(self, user_id: int, username: str, name: str) -> Player:
        try:
            player = Player(
                user_id= str(user_id),  # Store as integer
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

    async def get_character_level(self, user_id: int, character_name: str) -> int:
        """Get the level of a specific character."""
        character = await self.characters.find_one(
            {"user_id": user_id, "name": character_name},
            {"level": 1}
        )
        return character.get('level', 1) if character else 1

    # Titan operations
    def create_new_titan(self, level: int, difficulty: str, spawn_areas: List[str]) -> Titan:
        """Create a completely new titan with anime-accurate features and varied stats"""
        
        # Generate varied abilities based on difficulty
        abilities = ["Basic Attack"]
        special_abilities = None
        
        # Enhanced special ability chances with level scaling
        base_special_chance = {
            "Easy": 0.25,
            "Normal": 0.55, 
            "Hard": 0.85
        }[difficulty]
        
        # Increase chance with level (up to +20%)
        level_bonus = min(0.2, level * 0.02)
        special_chance = base_special_chance + level_bonus
        
        if random.random() < special_chance:
            ability_options = SPECIAL_ABILITIES[difficulty]
            max_abilities = {
                "Easy": 1,
                "Normal": random.randint(1, 2), 
                "Hard": random.randint(2, 3)  # Hard titans get more abilities
            }[difficulty]
            
            num_abilities = min(max_abilities, len(ability_options))
            special_abilities = random.sample(ability_options, num_abilities)
        
        # Generate unique titan with varied stats
        titan_name = generate_titan_name(difficulty)
        titan_hp = generate_titan_hp(level, difficulty)
        
        # Add size/strength variation for more realism (affects HP)
        size_modifier = random.uniform(0.85, 1.25)
        final_hp = max(50, int(titan_hp * size_modifier))  # Ensure minimum HP
        
        # Add rare chance for "Mutant" prefix for extra variety
        if random.random() < 0.05:  # 5% chance
            titan_name = f"Mutant {titan_name}"
            final_hp = int(final_hp * 1.15)  # Mutants are stronger
        
        return Titan(
            name=titan_name,
            level=level,
            max_hp=final_hp,
            abilities=abilities,
            created_at=datetime.datetime.now(datetime.timezone.utc),
            difficulty=difficulty,
            special_abilities=special_abilities,
            spawn_areas=spawn_areas,
            min_level_requirement=max(1, level - 3),
            internal_name=None  # Will be set by caller
        )

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

    

    async def get_random_titan(self, min_level: int, max_level: int, target_level: int, unlocked_areas: Optional[List[str]] = None) -> Titan:
        """Generate a new random titan each time for unique encounters"""
        
        # Generate random level within range
        level = random.randint(min_level, max_level)
        
        # Determine difficulty based on level
        if level >= 15:
            difficulty = "Hard"
        elif level >= 8:
            difficulty = "Normal"
        else:
            difficulty = "Easy"
        
        # Default spawn areas if none provided
        if not unlocked_areas:
            unlocked_areas = ["Trost District", "Karanes District", "Shiganshina District"]
        
        # Create a completely new titan each time
        new_titan = self.create_new_titan(
            level=level,
            difficulty=difficulty,
            spawn_areas=unlocked_areas
        )
        
        # Generate unique internal name for this encounter
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
        new_titan.internal_name = f"encounter_{level}_{difficulty.lower()}_{timestamp}"
        
        # Don't save to database - keep encounters unique and temporary
        logger.info(f"Generated new titan: {new_titan.name} (Level {new_titan.level}, HP: {new_titan.max_hp})")
        
        return new_titan


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

    async def get_connection_stats(self):
        """Get database connection statistics"""
        try:
            server_info = await self.client.admin.command("serverStatus")
            return {
                "connections_current": server_info.get("connections", {}).get("current", 0),
                "connections_available": server_info.get("connections", {}).get("available", 0),
                "uptime": server_info.get("uptime", 0)
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return None
