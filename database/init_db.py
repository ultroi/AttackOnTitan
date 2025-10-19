import logging
from database.db_instance import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase
from dotenv import load_dotenv
import os
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

async def initialize_database() -> None:
    """Initialize the database with required collections and initial data."""
    try:
        
        # Get the database instance
        db = await get_database()
        if db is None:
            raise ValueError("Database instance is not available")
        # Check if the database is connected
        if not db.client:
            raise ConnectionError("MongoDB client is not connected")
    

        # Test the connection
        await db.command('ismaster')  # Better for replica set checks
        logger.info("Successfully connected to MongoDB")

        # Create indexes
        await db.players.create_index("user_id", unique=True, background=True)
        await db.titans.create_index([("name", 1), ("level", 1)], unique=True, background=True)
        await db.titans.create_index("spawn_areas", background=True)
        await db.equipment.create_index("name", unique=True, background=True)
        await db.characters.create_index([("user_id", 1), ("name", 1)], unique=True, background=True)
        logger.info("Created database indexes")

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
                            "bsonType": "string",
                            "description": "must be a string and is required"
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
                        "crystal": { 
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
                            "bsonType": "string",
                            "description": "must be a string and is required"
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
                        "ultimate_abilities": {
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
                        "difficulty": {"enum": ["Easy", "Normal", "Hard", "Boss"]},
                        # ...existing code...
                        "weakness": {"bsonType": ["string", "null"]},
                        "resistance": {"bsonType": ["string", "null"]},
                        "spawn_areas": {"bsonType": "array", "items": {"bsonType": "string"}},
                        "min_level_requirement": {"bsonType": "int", "minimum": 1},
                        "created_at": {"bsonType": "date"},
                        "is_template": {"bsonType": ["bool", "null"]},
                        "internal_name": {"bsonType": ["string", "null"]}
                    }
                }
            }
        })
        logger.info("Updated titan collection validation schema")

    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise

if __name__ == "__main__":
    import asyncio
    asyncio.run(initialize_database())