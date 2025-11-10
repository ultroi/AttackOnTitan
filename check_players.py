import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def check():
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    
    # Find all players
    all_players = await players.find().to_list(length=None)
    print(f'Total players: {len(all_players)}')
    print()
    
    # Show first 10 user IDs
    for p in all_players[:10]:
        user_id = p["_id"]
        owned_chars = p.get("owned_characters", [])
        team = p.get("team", [])
        team_names = [t.get("character_name") for t in team] if team else []
        print(f'User: {user_id}')
        print(f'  Characters: {owned_chars}')
        print(f'  Team: {team_names}')
        print()
    
    client.close()

asyncio.run(check())
