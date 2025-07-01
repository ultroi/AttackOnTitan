from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
import logging
import certifi
import ssl
import dns.resolver
from bson import ObjectId
import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

DEFAULT_TITANS = [
    {
        "name": "Small Titan",
        "level": 1,
        "max_hp": 100,
        "abilities": ["Basic Attack"],
        "drop_table": {"marks": 0.5, "titan_crystals": 0.2, "valor_points": 0.1},
        "xp_reward": 50,
        "difficulty": "Easy",
        "spawn_areas": ["Trost District", "Karanes District"],
        "min_level_requirement": 1,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Abnormal Titan",
        "level": 5,
        "max_hp": 300,
        "abilities": ["Basic Attack", "Charge"],
        "drop_table": {"marks": 0.7, "titan_crystals": 0.4, "valor_points": 0.3},
        "xp_reward": 200,
        "difficulty": "Normal",
        "special_abilities": ["Roar", "Ground Slam"],
        "spawn_areas": ["Shiganshina District", "Wall Maria"],
        "min_level_requirement": 5,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Armored Titan",
        "level": 10,
        "max_hp": 1000,
        "abilities": ["Basic Attack", "Armor Break"],
        "drop_table": {"marks": 0.9, "titan_crystals": 0.6, "valor_points": 0.5},
        "xp_reward": 500,
        "difficulty": "Hard",
        "special_abilities": ["Armor Plating", "Titan Shift"],
        "weakness": "Thunder Spear",
        "spawn_areas": ["Wall Rose", "Wall Maria"],
        "min_level_requirement": 10,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Swift Titan",
        "level": 3,
        "max_hp": 150,
        "abilities": ["Quick Strike"],
        "drop_table": {"marks": 0.6, "titan_crystals": 0.3, "valor_points": 0.2},
        "xp_reward": 80,
        "difficulty": "Easy",
        "spawn_areas": ["Trost District", "Karanes District"],
        "min_level_requirement": 2,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Heavy Titan",
        "level": 7,
        "max_hp": 500,
        "abilities": ["Smash", "Stomp"],
        "drop_table": {"marks": 0.8, "titan_crystals": 0.5, "valor_points": 0.4},
        "xp_reward": 250,
        "difficulty": "Normal",
        "special_abilities": ["Tough Skin"],
        "spawn_areas": ["Shiganshina District"],
        "min_level_requirement": 6,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Colossal Titan",
        "level": 15,
        "max_hp": 2000,
        "abilities": ["Massive Strike"],
        "drop_table": {"marks": 1.0, "titan_crystals": 0.8, "valor_points": 0.7},
        "xp_reward": 1000,
        "difficulty": "Hard",
        "special_abilities": ["Steam Blast"],
        "weakness": "Nape Strike",
        "spawn_areas": ["Wall Maria"],
        "min_level_requirement": 12,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Agile Titan",
        "level": 4,
        "max_hp": 200,
        "abilities": ["Dodge", "Slash"],
        "drop_table": {"marks": 0.6, "titan_crystals": 0.3, "valor_points": 0.2},
        "xp_reward": 120,
        "difficulty": "Easy",
        "spawn_areas": ["Karanes District"],
        "min_level_requirement": 3,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Frenzied Titan",
        "level": 8,
        "max_hp": 400,
        "abilities": ["Berserk Rush"],
        "drop_table": {"marks": 0.7, "titan_crystals": 0.4, "valor_points": 0.3},
        "xp_reward": 300,
        "difficulty": "Normal",
        "special_abilities": ["Rage"],
        "spawn_areas": ["Wall Rose"],
        "min_level_requirement": 7,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Regenerating Titan",
        "level": 9,
        "max_hp": 600,
        "abilities": ["Basic Attack", "Regenerate"],
        "drop_table": {"marks": 0.8, "titan_crystals": 0.5, "valor_points": 0.4},
        "xp_reward": 350,
        "difficulty": "Normal",
        "special_abilities": ["Fast Healing"],
        "spawn_areas": ["Shiganshina District"],
        "min_level_requirement": 8,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Warhammer Titan",
        "level": 12,
        "max_hp": 1200,
        "abilities": ["Hammer Strike"],
        "drop_table": {"marks": 0.9, "titan_crystals": 0.6, "valor_points": 0.5},
        "xp_reward": 600,
        "difficulty": "Hard",
        "special_abilities": ["Weapon Creation"],
        "weakness": "Thunder Spear",
        "spawn_areas": ["Wall Rose"],
        "min_level_requirement": 10,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Stealth Titan",
        "level": 6,
        "max_hp": 250,
        "abilities": ["Ambush"],
        "drop_table": {"marks": 0.7, "titan_crystals": 0.4, "valor_points": 0.3},
        "xp_reward": 180,
        "difficulty": "Normal",
        "spawn_areas": ["Trost District"],
        "min_level_requirement": 5,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Raging Titan",
        "level": 11,
        "max_hp": 800,
        "abilities": ["Fury Swipe"],
        "drop_table": {"marks": 0.8, "titan_crystals": 0.5, "valor_points": 0.4},
        "xp_reward": 400,
        "difficulty": "Hard",
        "spawn_areas": ["Wall Maria"],
        "min_level_requirement": 9,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Crystal Titan",
        "level": 13,
        "max_hp": 900,
        "abilities": ["Crystal Shard"],
        "drop_table": {"marks": 0.9, "titan_crystals": 0.7, "valor_points": 0.5},
        "xp_reward": 550,
        "difficulty": "Hard",
        "special_abilities": ["Crystal Armor"],
        "spawn_areas": ["Wall Rose"],
        "min_level_requirement": 11,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Swift Abnormal",
        "level": 2,
        "max_hp": 120,
        "abilities": ["Fast Strike"],
        "drop_table": {"marks": 0.5, "titan_crystals": 0.2, "valor_points": 0.1},
        "xp_reward": 60,
        "difficulty": "Easy",
        "spawn_areas": ["Karanes District"],
        "min_level_requirement": 1,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Tough Titan",
        "level": 14,
        "max_hp": 1500,
        "abilities": ["Heavy Slam"],
        "drop_table": {"marks": 0.9, "titan_crystals": 0.6, "valor_points": 0.5},
        "xp_reward": 700,
        "difficulty": "Hard",
        "special_abilities": ["Iron Skin"],
        "spawn_areas": ["Shiganshina District"],
        "min_level_requirement": 12,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Elite Titan",
        "level": 16,
        "max_hp": 1800,
        "abilities": ["Precision Strike"],
        "drop_table": {"marks": 1.0, "titan_crystals": 0.7, "valor_points": 0.6},
        "xp_reward": 800,
        "difficulty": "Hard",
        "special_abilities": ["Enhanced Reflexes"],
        "spawn_areas": ["Wall Rose"],
        "min_level_requirement": 14,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Chaos Titan",
        "level": 17,
        "max_hp": 1600,
        "abilities": ["Chaotic Barrage"],
        "drop_table": {"marks": 1.0, "titan_crystals": 0.7, "valor_points": 0.6},
        "xp_reward": 850,
        "difficulty": "Hard",
        "spawn_areas": ["Wall Maria"],
        "min_level_requirement": 15,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Shadow Titan",
        "level": 18,
        "max_hp": 1400,
        "abilities": ["Stealth Strike"],
        "drop_table": {"marks": 0.9, "titan_crystals": 0.6, "valor_points": 0.5},
        "xp_reward": 900,
        "difficulty": "Normal",
        "special_abilities": ["Fade"],
        "spawn_areas": ["Trost District"],
        "min_level_requirement": 16,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Blazing Titan",
        "level": 19,
        "max_hp": 1700,
        "abilities": ["Fire Burst"],
        "drop_table": {"marks": 1.0, "titan_crystals": 0.8, "valor_points": 0.7},
        "xp_reward": 950,
        "difficulty": "Hard",
        "special_abilities": ["Flame Aura"],
        "weakness": "Nape Strike",
        "spawn_areas": ["Shiganshina District"],
        "min_level_requirement": 17,
        "created_at": datetime.datetime.utcnow()
    },
    {
        "name": "Ancient Titan",
        "level": 20,
        "max_hp": 2000,
        "abilities": ["Earth Shatter"],
        "drop_table": {"marks": 1.1, "titan_crystals": 0.9, "valor_points": 0.8},
        "xp_reward": 1000,
        "difficulty": "Hard",
        "special_abilities": ["Stone Skin"],
        "spawn_areas": ["Wall Maria"],
        "min_level_requirement": 18,
        "created_at": datetime.datetime.utcnow(),
    },
]

# Default equipment
DEFAULT_EQUIPMENT = [
    {
        "name": "Standard ODM Gear",
        "type": "ODM",
        "rarity": "Common",
        "durability": 100,
        "weight": 15.0,
        "attributes": {"gas_efficiency": 1.0, "wire_length": 1.0, "control_precision": 1.0}
    },
    {
        "name": "Standard Blades",
        "type": "Blade",
        "rarity": "Common",
        "durability": 100,
        "weight": 5.0,
        "attributes": {"sharpness": 1.0, "durability": 1.0}
    },
    {
        "name": "Standard Gas Tank",
        "type": "GasTank",
        "rarity": "Common",
        "durability": 100,
        "weight": 10.0,
        "attributes": {"capacity": 1.0, "efficiency": 1.0}
    },
    {
        "name": "Cadet Uniform",
        "type": "Uniform",
        "rarity": "Common",
        "durability": 100,
        "weight": 8.0,
        "attributes": {"defense": 1.0, "mobility": 1.0}
    }
]

# Default character abilities
DEFAULT_ABILITIES = {
    "active": [
        {
            "name": "Basic Attack",
            "type": "active",
            "description": "A basic attack with ODM gear",
            "level_required": 0
        },
        {
            "name": "Vertical Maneuvering",
            "type": "active",
            "description": "Advanced vertical movement technique",
            "level_required": 5
        }
    ],
    "passive": [
        {
            "name": "Gas Efficiency",
            "type": "passive",
            "description": "Reduces gas consumption by 10%",
            "level_required": 3
        },
        {
            "name": "Combat Instinct",
            "type": "passive",
            "description": "Increases critical hit chance by 5%",
            "level_required": 7
        }
    ]
}

async def initialize_database():
    """Initialize the database with required collections and initial data."""
    # Get MongoDB URI from environment
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        raise ValueError("MONGODB_URI environment variable is not set")
    
    client = None
    try:
        # Configure SSL context
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        # Configure DNS resolver
        dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
        dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4'] 
        dns.resolver.default_resolver.timeout = 5  # seconds
        dns.resolver.default_resolver.lifetime = 30  # total seconds
        
        # Connect to MongoDB with SSL configuration
        client = AsyncIOMotorClient(
            mongodb_uri,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=20000,
            socketTimeoutMS=60000,
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

        # More thorough connection test
        await client.admin.command('ismaster')  # Better for replica set checks
        logger.info("Successfully connected to MongoDB")
        
        db = client.attackontitan
        
        # Create indexes
        await db.players.create_index("user_id", unique=True, background=True)
        await db.titans.create_index([("name", 1), ("level", 1)], unique=True, background=True)
        await db.titans.create_index("spawn_areas", background=True)
        await db.equipment.create_index("name", unique=True, background=True)
        
        # Drop existing validation rules first (if they exist)
        try:
            await db.command({
                "collMod": "players",
                "validator": {}
            })
            logger.info("Cleared existing player validation rules")
        except Exception as e:
            logger.info(f"No existing player validation rules to clear: {e}")
        
        try:
            await db.command({
                "collMod": "characters",
                "validator": {}
            })
            logger.info("Cleared existing character validation rules")
        except Exception as e:
            logger.info(f"No existing character validation rules to clear: {e}")
        
        # Create corrected validation schemas for collections
        await db.command({
            "collMod": "players",
            "validator": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": [
                        "user_id",
                        "username", 
                        "name", 
                        "level", 
                        "xp", 
                        "total_xp", 
                        "gas", 
                        "valor", 
                        "crystal", 
                        "marks", 
                        "explore_count"
                    ],
                    "properties": {
                        "user_id": { 
                            "bsonType": ["int", "long"],
                            "description": "must be a string, int or long and is required"
                        },
                        "username": { 
                            "bsonType": "string",
                            "description": "must be a string and is required"
                        },
                        "name": { 
                            "bsonType": "string",
                            "description": "must be a string and is required"
                        },
                        "level": { 
                            "bsonType": "int", 
                            "minimum": 1,
                            "description": "must be an integer greater than 0 and is required"
                        },
                        "xp": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "total_xp": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "gas": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "valor": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "crystal": {  # Ensure this matches the Player model
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "marks": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "explore_count": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "owned_characters": { 
                            "bsonType": "array", 
                            "items": { "bsonType": "string" },
                            "description": "must be an array of strings"
                        },
                        "created_at": { 
                            "bsonType": "date",
                            "description": "must be a date"
                        },
                        "updated_at": { 
                            "bsonType": "date",
                            "description": "must be a date"
                        },
                        "daily_streak": { 
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0"
                        },
                        "last_daily_claim": { 
                            "bsonType": ["date", "null"],
                            "description": "must be a date or null"
                        },
                        "inventory": { 
                            "bsonType": "object",
                            "description": "must be an object"
                        },
                        "unlocked_areas": { 
                            "bsonType": "array", 
                            "items": { "bsonType": "string" },
                            "description": "must be an array of strings"
                        },
                        "completed_quests": { 
                            "bsonType": "array", 
                            "items": { "bsonType": "string" },
                            "description": "must be an array of strings"
                        }
                    }
                }
            }
        })
        logger.info("Updated player collection validation schema")

        await db.command({
            "collMod": "characters",
            "validator": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["user_id", "name", "character_type", "level", "current_hp", "xp", "total_xp", "stats"],
                    "properties": {
                        "user_id": { 
                            "bsonType": ["int", "long"],
                            "description": "must be a string, int or long and is required"
                        },
                        "name": {
                            "bsonType": "string",
                            "description": "must be a string and is required"
                        },
                        "character_type": {
                            "bsonType": "string",
                            "description": "must be a string and is required"
                        },
                        "level": {
                            "bsonType": "int", 
                            "minimum": 1,
                            "description": "must be an integer greater than 0 and is required"
                        },
                        "current_hp": {
                            "bsonType": "int",
                            "minimum": 0,
                            "description": "must be an integer >= 0 and is required"
                        },
                        "xp": {
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "total_xp": {
                            "bsonType": "int", 
                            "minimum": 0,
                            "description": "must be an integer greater than or equal to 0 and is required"
                        },
                        "stats": {
                            "bsonType": "object",
                            "required": ["ATK", "DEF", "ACC", "INT", "SPD"],
                            "properties": {
                                "ATK": {
                                    "bsonType": "int", 
                                    "minimum": 1,
                                    "description": "must be an integer greater than 0"
                                },
                                "DEF": {
                                    "bsonType": "int", 
                                    "minimum": 1,
                                    "description": "must be an integer greater than 0"
                                },
                                "ACC": {
                                    "bsonType": "int", 
                                    "minimum": 1,
                                    "description": "must be an integer greater than 0"
                                },
                                "INT": {
                                    "bsonType": "int", 
                                    "minimum": 1,
                                    "description": "must be an integer greater than 0"
                                },
                                "SPD": {
                                    "bsonType": "int", 
                                    "minimum": 1,
                                    "description": "must be an integer greater than 0"
                                }
                            },
                            "description": "must be an object with required stats"
                        },
                        "active_abilities": {
                            "bsonType": "array",
                            "items": {
                                "bsonType": "object",
                                "required": ["name", "type", "description", "level_required", "unlocked"],
                                "properties": {
                                    "name": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "type": {
                                        "bsonType": "string", 
                                        "enum": ["active"],
                                        "description": "must be 'active' and is required"
                                    },
                                    "description": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "level_required": {
                                        "bsonType": "int", 
                                        "minimum": 0,
                                        "description": "must be an integer greater than or equal to 0 and is required"
                                    },  
                                    "unlocked": {
                                        "bsonType": "bool",
                                        "description": "must be a boolean and is required"
                                    }
                                }
                            },
                            "description": "must be an array of active ability objects"
                        },
                        "passive_abilities": {
                            "bsonType": "array",
                            "items": {
                                "bsonType": "object",
                                "required": ["name", "type", "description", "level_required", "unlocked"],
                                "properties": {
                                    "name": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "type": {
                                        "bsonType": "string", 
                                        "enum": ["passive"],
                                        "description": "must be 'passive' and is required"
                                    },
                                    "description": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "level_required": {
                                        "bsonType": "int", 
                                        "minimum": 0,
                                        "description": "must be an integer greater than or equal to 0 and is required"
                                    },
                                    "unlocked": {
                                        "bsonType": "bool",
                                        "description": "must be a boolean and is required"
                                    }
                                }
                            },
                            "description": "must be an array of passive ability objects"
                        },
                        "ultimate_abilities": {  # Add this new section
                            "bsonType": "array",
                            "items": {
                                "bsonType": "object",
                                "required": ["name", "type", "description", "level_required", "unlocked"],
                                "properties": {
                                    "name": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "type": {
                                        "bsonType": "string", 
                                        "enum": ["ultimate"],
                                        "description": "must be 'ultimate'"
                                    },
                                    "description": {
                                        "bsonType": "string",
                                        "description": "must be a string and is required"
                                    },
                                    "level_required": {
                                        "bsonType": "int", 
                                        "minimum": 0,
                                        "description": "must be an integer greater than or equal to 0 and is required"
                                    },
                                    "unlocked": {
                                        "bsonType": "bool",
                                        "description": "must be a boolean and is required"
                                    }
                                }
                            },
                            "description": "must be an array of ultimate ability objects"
                        }
                    }
                }
            }
        })
        logger.info("Updated character collection validation schema")

        await db.command({
            "collMod": "titans",
            "validator": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": [
                        "name", "level", "max_hp", "abilities", "drop_table",
                        "xp_reward", "difficulty", "spawn_areas", "min_level_requirement", "created_at"
                    ],
                    "properties": {
                        "name": {"bsonType": "string"},
                        "level": {"bsonType": "int", "minimum": 1},
                        "max_hp": {"bsonType": "int", "minimum": 1},
                        "abilities": {"bsonType": "array", "items": {"bsonType": "string"}},
                        "drop_table": {"bsonType": "object"},
                        "xp_reward": {"bsonType": "int", "minimum": 0},
                        "difficulty": {"enum": ["Easy", "Normal", "Hard"]},
                        "special_abilities": {"bsonType": ["array", "null"], "items": {"bsonType": "string"}},
                        "weakness": {"bsonType": ["string", "null"]},
                        "resistance": {"bsonType": ["string", "null"]},
                        "spawn_areas": {"bsonType": "array", "items": {"bsonType": "string"}},
                        "min_level_requirement": {"bsonType": "int", "minimum": 1},
                        "created_at": {"bsonType": "date"},
                        "is_template": {"bsonType": ["bool", "null"]}
                    }
                }
            }
        })
        logger.info("Updated titan collection validation schema")

        # Add initial titans if they don't exist
        for titan_data in DEFAULT_TITANS:
            existing_titan = await db.titans.find_one({
                "name": titan_data["name"],
                "level": titan_data["level"],
                "is_template": titan_data.get("is_template", False)
            })
            if not existing_titan:
                await db.titans.insert_one(titan_data)
                logger.info(f"Added titan: {titan_data['name']} (Lv. {titan_data['level']})")
            else:
                await db.titans.update_one(
                    {
                        "name": titan_data["name"],
                        "level": titan_data["level"],
                        "is_template": titan_data.get("is_template", False)
                    },
                    {"$set": titan_data}
                )
                logger.info(f"Updated titan: {titan_data['name']} (Lv. {titan_data['level']})")
        # Verify Generic Titan
        generic_titan = await db.titans.find_one({"name": "Generic Titan", "is_template": True})
        if generic_titan:
            logger.info("Generic Titan template verified in database")
        else:
            logger.error("Failed to find Generic Titan template after initialization")
        
        # Add initial equipment if they don't exist
        for equipment_data in DEFAULT_EQUIPMENT:
            existing_equipment = await db.equipment.find_one({"name": equipment_data["name"]})
            if not existing_equipment:
                await db.equipment.insert_one(equipment_data)
                logger.info(f"Added equipment: {equipment_data['name']}")
        
        logger.info("Database initialization completed successfully!")
        return client
        
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise
    finally:
        if client:
            client.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(initialize_database())