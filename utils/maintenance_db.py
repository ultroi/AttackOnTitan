from database.db_instance import get_database

MAINTENANCE_COLLECTION = "maintenance_status"

async def set_maintenance_db(on: bool):
    db = await get_database()
    if db is None:
        raise Exception("Database not available")
    await db[MAINTENANCE_COLLECTION].update_one(
        {"_id": "maintenance"},
        {"$set": {"on": on}},
        upsert=True
    )

async def is_maintenance_db() -> bool:
    db = await get_database()
    if db is None:
        return False
    doc = await db[MAINTENANCE_COLLECTION].find_one({"_id": "maintenance"})
    return bool(doc and doc.get("on", False))
