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
                # Initialize the connection with explicit options
                client = motor.motor_asyncio.AsyncIOMotorClient(
                    MONGO_URI,
                    maxPoolSize=10,  # Limit connection pool size
                    connectTimeoutMS=10000,  # 10-second timeout for connection
                    serverSelectionTimeoutMS=10000  # 10-second timeout for server selection
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
    """Always create a new database client per call (for serverless compatibility)."""
    try:
        from config import MONGO_URI, DB_NAME
        import motor.motor_asyncio
        client = motor.motor_asyncio.AsyncIOMotorClient(
            MONGO_URI,
            maxPoolSize=10,
            connectTimeoutMS=10000,
            serverSelectionTimeoutMS=10000
        )
        db = client[DB_NAME]
        await db.command('ping')
        logger.info(f"Database connection established successfully to {DB_NAME}")
        return db
    except Exception as e:
        logger.error(f"Error getting database instance: {str(e)}")
        return None

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