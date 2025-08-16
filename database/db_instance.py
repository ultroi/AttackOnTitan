import asyncio
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorDatabase
from config import MONGO_URI, DB_NAME
import logging
from typing import Optional

# Configure logging
logger = logging.getLogger(__name__)

# Global database instance
_db_instance: Optional[AsyncIOMotorDatabase] = None
_initialized: bool = False
_init_lock = asyncio.Lock()

async def initialize_database() -> AsyncIOMotorDatabase:
    """Initialize the database connection with proper error handling."""
    global _db_instance, _initialized
    
    async with _init_lock:
        if not _initialized:
            try:
                # Initialize the connection with optimized options for faster response
                client = motor.motor_asyncio.AsyncIOMotorClient(
                    MONGO_URI,
                    maxPoolSize=25,  # Increased for more concurrent connections
                    minPoolSize=5,   # Keep minimum connections open
                    connectTimeoutMS=3000,  # Reduced timeout for faster failure
                    serverSelectionTimeoutMS=3000,  # Reduced timeout for faster failure
                    socketTimeoutMS=3000,  # Reduced socket timeout
                    maxIdleTimeMS=60000,  # Keep connections alive longer
                    waitQueueTimeoutMS=2000  # Fail faster on connection queue timeouts
                )
                _db_instance = client[DB_NAME]
                
                # Test the connection
                await _db_instance.command('ping')
                logger.info(f"Database connection established successfully to {DB_NAME}")
                
                # Optional: Verify key collection exists (e.g., 'characters')
                collections = await _db_instance.list_collection_names()
                if 'characters' not in collections:
                    logger.warning("Characters collection not found; may need initialization")
                
                _initialized = True
                return _db_instance
                
            except motor.motor_asyncio.ServerSelectionTimeoutError as e:
                logger.error(f"Server selection timeout: {str(e)}")
                _initialized = False
                _db_instance = None
                raise ConnectionError(f"Database initialization failed: {str(e)}")
            except motor.motor_asyncio.ConnectionFailure as e:
                logger.error(f"Connection failure: {str(e)}")
                _initialized = False
                _db_instance = None
                raise ConnectionError(f"Database initialization failed: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error during database initialization: {str(e)}")
                _initialized = False
                _db_instance = None
                raise ConnectionError(f"Database initialization failed: {str(e)}")
    
    return _db_instance

async def get_database() -> Optional[AsyncIOMotorDatabase]:
    """Get the persistent global database instance (recommended for all bot operations)."""
    return await get_persistent_database()

async def get_persistent_database() -> Optional[AsyncIOMotorDatabase]:
    """Get the persistent global database instance (for persistent servers)."""
    global _db_instance, _initialized
    if not _initialized or _db_instance is None:
        await initialize_database()
    return _db_instance

async def close_connection() -> None:
    """Close the database connection."""
    global _db_instance, _initialized
    if _db_instance is not None:
        _db_instance.client.close()
        logger.info("Database connection closed successfully")
        _db_instance = None
        _initialized = False