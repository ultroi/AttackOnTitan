import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

async def fix_player(db_user_id_str: str):
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
        owned_chars = player.get("owned_characters", [])
        print(f"   Owned characters: {owned_chars}")
        
        # Check which characters exist in database
        missing = []
        for char_name in owned_chars:
            char_id = f"{db_user_id_str}_{char_name}"
            char_doc = await characters.find_one({"_id": char_id})
            
            if not char_doc:
                print(f"   ⚠️ Missing: {char_name}")
                missing.append(char_name)
            else:
                print(f"   ✅ Exists: {char_name}")
        
        if missing:
            print(f"\n🔧 Removing {len(missing)} missing characters from owned_characters...")
            new_owned = [c for c in owned_chars if c not in missing]
            
            result = await players.update_one(
                {"_id": user_obj_id},
                {"$set": {
                    "owned_characters": new_owned,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            if result.modified_count > 0:
                print(f"✅ Removed {len(missing)} missing characters")
                print(f"   New owned_characters: {new_owned}")
            
            # Fix team if necessary
            team = player.get("team", [])
            if team:
                team_chars = [t.get("character_name") for t in team]
                team_missing = [c for c in team_chars if c in missing]
                
                if team_missing:
                    print(f"\n⚠️ Team has missing characters: {team_missing}")
                    
                    if new_owned:
                        print(f"🔧 Auto-assigning first valid character to team...")
                        new_team = [{
                            "character_name": new_owned[0],
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
                            print(f"✅ Team fixed: {new_owned[0]} at position 1")
                    else:
                        print(f"⚠️ No characters left! Clearing team...")
                        result = await players.update_one(
                            {"_id": user_obj_id},
                            {"$set": {
                                "team": [],
                                "updated_at": datetime.now(timezone.utc)
                            }}
                        )
                        if result.modified_count > 0:
                            print(f"✅ Team cleared")
        else:
            print(f"✅ All characters exist in database!")
        
        print(f"\n✅ Player {db_user_id_str} fixed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        client.close()

async def main():
    print("=" * 50)
    print("Fixing player 1: 68aae6400615b81de13db087")
    print("=" * 50)
    await fix_player("68aae6400615b81de13db087")
    
    print("\n" + "=" * 50)
    print("Fixing player 2: 68ac76f1e69b8ed099fc12ae")
    print("=" * 50)
    await fix_player("68ac76f1e69b8ed099fc12ae")

asyncio.run(main())
