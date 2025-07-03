# Attack on Titan Game Bot

## Core Game Mechanics

### Character Selection
Players can choose from the following characters:
- Hitch Dreyse
- Daz
- Mins Carolina

### Birthplace Selection
Each birthplace provides unique stat adjustments:
- **Shiganshina**: +Strength, -Intelligence
- **Karanes**: +Intelligence, -Strength
- **Trost**: +Agility, -Endurance
- **Krolva**: +Endurance, -Agility

### Core Stats System
Primary Stats:
- Strength
- Agility
- Intelligence
- Endurance

Derived Attributes:
- Attack Power
- Defense
- Critical Rate
- Dodge
- Carrying Capacity

### Progression System
Ranks (in order):
1. Cadet
2. Soldier
3. Scout
4. Veteran
5. Elite
6. Squad Leader

Skill Trees:
1. Combat (Weapon mastery, precision attacks)
2. Survival (Resource efficiency, recovery)
3. Leadership (Team buffs, formation strategies)
4. Technical (Gear durability, crafting, engineering)

### Currency System
- Titan Crystals
- Valor Points
- Marks

### Gear System
Equipment Categories:
- ODM Gear (Movement)
- Blades (Offense)
- Gas Tanks (Range)
- Uniforms (Defense)
- Accessories (Bonuses)

Gear Attributes:
- Durability
- Weight
- Quality/Rank
- Rarity (Common to Legendary)

### Exploration System
Areas:
- Three Walls (Maria, Rose, Sina)
- Districts (Shiganshina, Trost)
- Open Fields
- Forests
- Underground Cities
- Special Zones (Echo Bastion, Hollow Exchange)

Environmental Effects:
- Day/Night Cycle
- Weather Systems (Fog, Rain, Wind)

### Titan Combat System
Titan Types:
1. Bearded Titan
2. Potbellied Titan
3. Goofy Grinning Titan
4. Tiny-Armed Titan
5. Long-Nosed Titan
6. Crawling Lizard-Like Titan
7. Tall Toothless Titan
8. Tree Hanger Titan
9. Gaping Mouth Titan
10. Small Round Titan
11. Double-Jawed Titan
12. One-Eyed Titan
13. Long-Limbed Wall Climber
14. Thin-Legged Titan
15. Titan with Half a Face

Combat Rewards:
- XP (100-500 per titan)
  - 50% to player account
  - 50% to character
- Currency drops (weighted chances)
  - Marks (Common)
  - Crystals (Uncommon)
  - Valor Points (Rare)

### Commands
- `/inv` or `/profile`: View player profile
  - Player ID
  - Name
  - Level
  - Total XP
  - Birthplace
  - Currencies
  - Exploration Count 

## Environment Variables

You must create a `.env` file in the project root with the following variables for MongoDB connection:

```
MONGODB_URI=mongodb://localhost:27017
DB_NAME=attackontitan
```

- If you use MongoDB Atlas or a remote server, replace the URI accordingly.
- The bot will not work if these are missing or incorrect. 