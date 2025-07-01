# db_instance.py
import asyncio
from database.db import Database

# Global database instance
_db = None
_initialized = False
_init_lock = asyncio.Lock()

async def initialize_database():
    """Initialize the database connection."""
    global _db, _initialized
    async with _init_lock:
        if not _initialized:
            _db = Database()
            await _db.init_db()
            _initialized = True
    return _db

async def get_database() -> Database:
    """Get the initialized database instance."""
    global _db
    if not _initialized:
        raise RuntimeError("Database not initialized. Call initialize_database() first.")
    if _db is None:
        raise RuntimeError("Database instance is None.")
    return _db