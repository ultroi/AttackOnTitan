import asyncio
from database.db import Database
from database.db_instance import get_database

async def check():
    db = Database()
    motor_db = await get_database()
    await db.init_db(motor_db)

    # Invalidate cache for user 5956598856
    db.invalidate_player_cache('5956598856')
    print("Cache invalidated for user 5956598856")

    player = await db.players.find_one({'user_id': '5956598856'})
    if player:
        chars = player.get('owned_characters', [])
        print(f'Current characters: {chars}')
    else:
        print('Player not found')

asyncio.run(check())