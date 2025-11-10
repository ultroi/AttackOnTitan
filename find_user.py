import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def find_user_by_telegram_id(telegram_id: int):
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    
    # Search all players for telegram_id field
    player = await players.find_one({"telegram_id": telegram_id})
    
    if player:
        print(f'✅ Found player with telegram_id {telegram_id}')
        print(f'  Database ID: {player["_id"]}')
        print(f'  Characters: {player.get("owned_characters", [])}')
        print(f'  Team: {player.get("team", [])}')
        print(f'  Game started: {player.get("game_started", False)}')
        return player["_id"]
    else:
        print(f'❌ Player with telegram_id {telegram_id} not found')
        return None
    
    client.close()

async def main():
    # Search for your user IDs
    await find_user_by_telegram_id(6620217176)
    print()
    await find_user_by_telegram_id(5956598856)

asyncio.run(main())
