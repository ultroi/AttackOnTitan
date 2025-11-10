"""
Manual user data fix script
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def fix_user(user_id: int):
    """Fix a specific user's data"""
    mongo_url = os.getenv('MONGODB_URI')
    db_name = os.getenv('DB_NAME', 'attackontitan')
    
    if not mongo_url:
        print("❌ MONGODB_URI not found in .env")
        return
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    players = db['players']
    characters = db['characters']
    
    try:
        # Get player
        player = await players.find_one({"_id": user_id})
        
        if not player:
            print(f"❌ User {user_id} not found in database!")
            return
        
        print(f"✅ Found player: {user_id}")
        print(f"   Owned characters: {player.get('owned_characters', [])}")
        print(f"   Team: {player.get('team', [])}")
        
        # Check if any owned characters are missing
        owned_chars = player.get('owned_characters', [])
        missing_chars = []
        
        for char_name in owned_chars:
            char_doc = await characters.find_one({
                "_id": f"{user_id}_{char_name}"
            })
            if not char_doc:
                missing_chars.append(char_name)
                print(f"   ⚠️ Missing character document: {char_name}")
        
        if not missing_chars:
            print(f"   ✅ All character documents exist")
        
        # Check team
        team = player.get('team', [])
        if not team and owned_chars:
            print(f"   ⚠️ Player has NO team but has characters!")
            print(f"   🔧 Auto-assigning first character to team...")
            
            # Add first character to team
            first_char = owned_chars[0]
            new_team = [{
                "character_name": first_char,
                "position": 1
            }]
            
            result = await players.update_one(
                {"_id": user_id},
                {"$set": {
                    "team": new_team,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            if result.modified_count > 0:
                print(f"   ✅ Auto-assigned {first_char} to team position 1")
            else:
                print(f"   ❌ Failed to update team")
        elif team:
            print(f"   ✅ Player has team: {[t.get('character_name') for t in team]}")
        
        # Check if player has game started
        game_started = player.get('game_started', False)
        print(f"   Game started: {game_started}")
        
        if not game_started:
            print(f"   🔧 Marking game as started...")
            result = await players.update_one(
                {"_id": user_id},
                {"$set": {
                    "game_started": True,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            if result.modified_count > 0:
                print(f"   ✅ Game marked as started")
        
        print(f"\n✅ User {user_id} fixed successfully!")
        
    except Exception as e:
        print(f"❌ Error fixing user {user_id}: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python manual_user_fix.py <user_id>")
        print("Example: python manual_user_fix.py 123456789")
        sys.exit(1)
    
    user_id = int(sys.argv[1])
    asyncio.run(fix_user(user_id))
