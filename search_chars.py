import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def main():
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    
    # Just search for any players with matching character issues
    print("Searching for players with Floch Forster...")
    result = await players.find_one({"owned_characters": "Floch Forster"})
    
    if result:
        print(f"Found player: {result['_id']}")
        print(f"Owned chars: {result.get('owned_characters', [])}")
        print(f"Team: {result.get('team', [])}")
    else:
        print("No players found with Floch Forster")
    
    print("\n\nSearching for players with Commander Pixis...")
    result = await players.find_one({"owned_characters": "Commander Pixis"})
    
    if result:
        print(f"Found player: {result['_id']}")
        print(f"Owned chars: {result.get('owned_characters', [])}")
        print(f"Team: {result.get('team', [])}")
    else:
        print("No players found with Commander Pixis")
    
    client.close()

asyncio.run(main())
