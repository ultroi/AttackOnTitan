import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

async def restore_player_characters(db_user_id_str: str, characters_list: list):
    """Restore characters for a player"""
    mongo_url = os.getenv('MONGODB_URI')
    client = AsyncIOMotorClient(mongo_url)
    db = client['attackontitan']
    players = db['players']
    
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
        
        print(f"\n⚠️  NOTE: Character documents need to be created by the bot!")
        print(f"           Users should /start fresh or spin to auto-generate character data")
        print(f"\n✅ Player {db_user_id_str} database updated!")
        
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
