# Character System Documentation

## Character Stats

### Base Stats
Each character has four primary stats that determine their capabilities:

1. **Strength**
   - Affects Attack Power
   - Influences Carrying Capacity
   - Impacts Blade Damage

2. **Agility**
   - Affects Dodge Rate
   - Influences ODM Gear Control
   - Impacts Movement Speed

3. **Intelligence**
   - Affects Critical Rate
   - Influences Resource Management
   - Impacts Skill Effectiveness

4. **Endurance**
   - Affects Defense
   - Influences Gas Tank Efficiency
   - Impacts Stamina Recovery

### Derived Stats
These stats are calculated from the base stats:

1. **Attack Power**
   - Formula: Base + (Strength × 2) + (Agility × 0.5)
   - Affects damage dealt to titans

2. **Defense**
   - Formula: Base + (Endurance × 2) + (Strength × 0.5)
   - Reduces damage taken from titans

3. **Critical Rate**
   - Formula: Base + (Intelligence × 1.5) + (Agility × 0.5)
   - Chance to deal increased damage

4. **Dodge**
   - Formula: Base + (Agility × 2) + (Intelligence × 0.5)
   - Chance to avoid titan attacks

5. **Carrying Capacity**
   - Formula: Base + (Strength × 1.5) + (Endurance × 0.5)
   - Determines maximum gear weight

## Combat System

### Battle Interface
```
🛑 Titan Appeared 🛑
[Visual of the titan]

⚔️ THE BATTLE HAS STARTED ⚔️
"[Character Name] inflicted the titan's attack and is ready to fight back!"

| [Titan Name] (Lv. [Level]) |
HP: [Current]/[Max]  
[Health Bar]

| [Character Name] |
HP: [Current]/[Max]  
[Health Bar]
Gas: [Current]/[Max]

[Attack Buttons]
```

### Combat Mechanics
1. **Turn-Based System**
   - Player attacks first
   - Titan counter-attacks
   - Special abilities can be used based on character

2. **Resource Management**
   - Gas consumption affects ODM gear usage
   - Blade durability decreases with attacks
   - Stamina affects special moves

3. **Combat Rewards**
   - XP Distribution:
     - 50% to player account
     - 50% to character
   - Currency Drops:
     - Marks (Common)
     - Crystals (Uncommon)
     - Valor Points (Rare)

4. **Failure Penalties**
   - Small random amount of Valor Points lost
   - No permanent stat decreases
   - Gear durability may decrease

## Character Progression

### Leveling System
- XP gained from:
  - Titan defeats
  - Mission completion
  - Training exercises
  - Special events

### Rank Advancement
1. **Cadet**
   - Starting rank
   - Basic skills unlocked
   - Limited area access

2. **Soldier**
   - Intermediate skills
   - Expanded area access
   - Basic formations available

3. **Scout**
   - Advanced skills
   - Full area access
   - Team commands unlocked

4. **Veteran**
   - Expert skills
   - Special missions available
   - Advanced formations

5. **Elite**
   - Master skills
   - Elite missions
   - Leadership abilities

6. **Squad Leader**
   - All skills unlocked
   - Command capabilities
   - Special privileges

### Training System
- Daily training quotas
- Skill-specific training facilities
- Progressive difficulty levels
- Resource costs for training 