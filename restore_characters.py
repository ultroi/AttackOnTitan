import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

async def restore_player_characters(db_user_id_str: str, characters_list: list):
    """Restore characters for a player by recreating character documents"""
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    characters = db['characters']
    
    try:
        user_obj_id = ObjectId(db_user_id_str)
        player = await players.find_one({"_id": user_obj_id})
        
        if not player:
            print(f"❌ Player {db_user_id_str} not found")
            return
        
        print(f"✅ Found player: {db_user_id_str}")
        print(f"   Restoring characters: {characters_list}")
        
        # Add characters to owned_characters
        result = await players.update_one(
            {"_id": user_obj_id},
            {"$set": {
                "owned_characters": characters_list,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        
        if result.modified_count > 0:
            print(f"✅ Added {len(characters_list)} characters to owned_characters")
        
        # Set first character as team
        if characters_list:
            new_team = [{
                "character_name": characters_list[0],
                "position": 1
            }]
            
            result = await players.update_one(
                {"_id": user_obj_id},
                {"$set": {
                    "team": new_team,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            if result.modified_count > 0:
                print(f"✅ Set {characters_list[0]} as team member at position 1")
        
        # Create character documents
        print(f"\n🔧 Creating character documents...")
        created_count = 0
        
        for char_name in characters_list:
            char_id = f"{db_user_id_str}_{char_name}"
            
            # Check if already exists
            existing = await characters.find_one({"_id": char_id})
            if existing:
                print(f"   ✅ {char_name} document already exists")
                continue
            
            # Create basic character document
            char_doc = {
                "_id": char_id,
                "user_id": db_user_id_str,
                "character_name": char_name,
                "level": 1,
                "experience": 0,
                "health": 100,
                "max_health": 100,
                "active_abilities": [],
                "passive_abilities": [],
                "ultimate_abilities": [],
                "unlocked_abilities": {},
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            
            result = await characters.insert_one(char_doc)
            if result.inserted_id:
                print(f"   ✅ Created {char_name} document")
                created_count += 1
        
        print(f"\n✅ Restored {created_count} character documents!")
        print(f"✅ Player {db_user_id_str} fully restored!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        client.close()

async def main():
    print("=" * 60)
    print("Restoring Player 1: 68aae6400615b81de13db087")
    print("=" * 60)
    await restore_player_characters(
        "68aae6400615b81de13db087",
        ["Mina Carolina", "Floch Forster", "Hitch Dreyse", "Daz"]
    )
    
    print("\n" + "=" * 60)
    print("Restoring Player 2: 68ac76f1e69b8ed099fc12ae")
    print("=" * 60)
    await restore_player_characters(
        "68ac76f1e69b8ed099fc12ae",
        ["Hitch Dreyse", "Commander Pixis", "Daz"]
    )

asyncio.run(main())
