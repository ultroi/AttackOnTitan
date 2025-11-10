import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def find_user_any_field(user_id_int: int):
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    
    # Check both collections
    for collection_name in ['players', 'characters']:
        collection = db[collection_name]
        
        # Try different ID formats
        queries = [
            {"_id": user_id_int},  # Direct int
            {"_id": str(user_id_int)},  # String
            {"user_id": user_id_int},  # user_id field
            {"telegram_id": user_id_int},  # telegram_id field
            {"_id": {"$regex": str(user_id_int)}}  # Contains
        ]
        
        for query in queries:
            try:
                result = await collection.find_one(query)
                if result:
                    print(f'✅ Found in {collection_name} with query {query}')
                    print(f'   ID: {result["_id"]}')
                    if "owned_characters" in result:
                        print(f'   Characters: {result.get("owned_characters", [])}')
                    print()
            except:
                pass
    
    client.close()

async def main():
    await find_user_any_field(6620217176)
    await find_user_any_field(5956598856)

asyncio.run(main())
