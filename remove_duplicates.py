import asyncio
import sys
from database.db import Database
from database.db_instance import get_database

def normalize_character_name(name):
    """Normalize character names by converting spaces to underscores and making lowercase"""
    return name.lower().replace(" ", "_").replace("-", "_")

async def remove_duplicate_characters(test_user_id=None):
    """Remove duplicate characters from players' owned_characters lists"""
    db = Database()
    motor_db = await get_database()
    if motor_db is None:
        print("❌ Failed to connect to database")
        return
    await db.init_db(motor_db)

    if test_user_id:
        # Test mode: only process one user
        print(f"🧪 TEST MODE: Processing only user {test_user_id}")
        player_data = await db.players.find_one({"user_id": test_user_id})
        if not player_data:
            print(f"❌ User {test_user_id} not found!")
            return
        
        players = [player_data]
    else:
        # Production mode: process all users
        print("🔄 PRODUCTION MODE: Processing all users")
        players = await db.players.find({}).to_list(length=None)

    updated_count = 0

    for player_data in players:
        user_id = player_data.get("user_id")
        owned_characters = player_data.get("owned_characters", [])

        if not owned_characters:
            continue

        print(f"📋 Checking player {user_id}: {len(owned_characters)} characters")

        # Group characters by normalized name to find duplicates
        normalized_groups = {}
        for char in owned_characters:
            normalized = normalize_character_name(char)
            if normalized not in normalized_groups:
                normalized_groups[normalized] = []
            normalized_groups[normalized].append(char)

        # Find duplicates (groups with more than one character)
        duplicates_found = []
        unique_characters = []
        
        for normalized, chars in normalized_groups.items():
            if len(chars) > 1:
                # This is a duplicate group - keep the first occurrence
                unique_characters.append(chars[0])
                duplicates_found.extend(chars[1:])  # Mark the rest as duplicates
                print(f"   🔍 Found duplicate group '{normalized}': {chars}")
            else:
                unique_characters.append(chars[0])

        # If duplicates were found, update the player
        if duplicates_found:
            duplicates_removed = len(duplicates_found)
            print(f"✅ Player {user_id}: Found {duplicates_removed} duplicates")
            print(f"   Before: {owned_characters}")
            print(f"   After:  {unique_characters}")
            print(f"   Removed: {duplicates_found}")
            
            if test_user_id:
                # In test mode, ask for confirmation
                confirm = input(f"Update player {user_id}? (y/n): ").lower().strip()
                if confirm != 'y':
                    print(f"❌ Skipped updating player {user_id}")
                    continue
            
            await db.update_player(user_id, {"owned_characters": unique_characters})
            updated_count += 1
        else:
            print(f"ℹ️  Player {user_id}: No duplicates found")

    print(f"🎉 Cleanup complete. Updated {updated_count} players.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Test mode with specific user
        test_user_id = sys.argv[1]
        asyncio.run(remove_duplicate_characters(test_user_id))
    else:
        # Production mode
        confirm = input("⚠️  This will process ALL users. Continue? (y/n): ").lower().strip()
        if confirm == 'y':
            asyncio.run(remove_duplicate_characters())
        else:
            print("❌ Operation cancelled.")
