# db_instance.py
import asyncio
import motor.motor_asyncio
from config import MONGO_URI, DB_NAME  
import logging

# Configure logging
logger = logging.getLogger(__name__)

# Global database instance
_db_instance = None
_initialized = False
_init_lock = asyncio.Lock()

async def initialize_database():
    """Initialize the database connection with proper error handling."""
    global _db_instance, _initialized
    
    async with _init_lock:
        if not _initialized:
            try:
                # Initialize the connection
                client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
                _db_instance = client[DB_NAME]  # Use the configured database name
                
                # Test the connection
                await _db_instance.command('ping')
                logger.info("Database connection established successfully")
                
                _initialized = True
                return _db_instance
                
            except Exception as e:
                logger.error(f"Failed to initialize database: {str(e)}")
                _initialized = False
                _db_instance = None
                raise ConnectionError(f"Database initialization failed: {str(e)}")
    
    return _db_instance

async def get_database():
    """Get the database instance, initializing if necessary."""
    global _db_instance, _initialized
    
    if _db_instance is None or not _initialized:
        try:
            _db_instance = await initialize_database()
        except Exception as e:
            logger.error(f"Error getting database instance: {str(e)}")
            return None
            
    return _db_instance

async def close_connection():
    """Close the database connection."""
    global _db_instance, _initialized
    if _db_instance is not None:
        _db_instance.client.close()
        _db_instance = None
        _initialized = False
        logger.info("Database connection closed")