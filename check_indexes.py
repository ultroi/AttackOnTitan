import asyncio
from database.db import Database

async def check_indexes():
    db = Database()
    await db.init_db()

    # Check current indexes
    print('=== PLAYERS INDEXES ===')
    player_indexes = await db.players.index_information()
    for idx_name, idx_info in player_indexes.items():
        print(f'{idx_name}: {idx_info}')

    print('\n=== CHARACTERS INDEXES ===')
    char_indexes = await db.characters.index_information()
    for idx_name, idx_info in char_indexes.items():
        print(f'{idx_name}: {idx_info}')

    print('\n=== TITANS INDEXES ===')
    titan_indexes = await db.titans.index_information()
    for idx_name, idx_info in titan_indexes.items():
        print(f'{idx_name}: {idx_info}')

if __name__ == "__main__":
    asyncio.run(check_indexes())
