import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

load_dotenv()

async def check_player_fields(db_user_id_str: str):
    """Check player document fields"""
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    
    try:
        user_obj_id = ObjectId(db_user_id_str)
        player = await players.find_one({"_id": user_obj_id})
        
        if player:
            print(f"✅ Found player: {db_user_id_str}")
            print(f"\nPlayer fields:")
            for key, value in player.items():
                if key not in ['_id']:
                    if isinstance(value, list):
                        print(f"  {key}: {value[:2] if len(value) > 2 else value}... ({len(value)} items)")
                    elif isinstance(value, dict):
                        print(f"  {key}: {list(value.keys())}")
                    else:
                        print(f"  {key}: {value}")
        else:
            print(f"❌ Player not found")
    
    finally:
        client.close()

async def main():
    await check_player_fields("68aae6400615b81de13db087")

asyncio.run(main())
