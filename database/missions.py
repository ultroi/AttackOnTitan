import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from database.models import CharacterStats

logger = logging.getLogger(__name__)

# Mission status enum-like constants
MISSION_STATUS_NOT_STARTED = "not_started"
MISSION_STATUS_IN_PROGRESS = "in_progress"
MISSION_STATUS_COMPLETED = "completed"
MISSION_STATUS_FAILED = "failed"
MISSION_STATUS_CANCELLED = "cancelled"

class MissionProgress(BaseModel):
    """Model to track progress of a mission for a player"""
    mission_id: int
    status: str = MISSION_STATUS_NOT_STARTED
    current_progress: int = 0
    required_progress: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    expiry_at: Optional[datetime] = None  # For time-limited missions
    unique_opponents: List[str] = Field(default_factory=list)  # Track unique opponents for PvP missions
    
    def dict(self, *args, **kwargs):
        result = super().dict(*args, **kwargs)
        # Convert datetime objects to ISO strings for MongoDB compatibility
        for key, value in result.items():
            if isinstance(value, datetime):
                result[key] = value.isoformat()
        return result

class Mission(BaseModel):
    """Model for mission definitions"""
    id: int
    title: str
    description: str
    requirement: str
    required_progress: int = 1
    reward_description: str
    rewards: Dict[str, Any]
    time_limit_hours: Optional[int] = None  # If mission has time limit
    prerequisite_missions: List[int] = Field(default_factory=list)
    unlock_level: int = 1  # Minimum player level to unlock this mission
    location_requirement: Optional[str] = None  # Specific location required for mission
    
    def dict(self, *args, **kwargs):
        result = super().dict(*args, **kwargs)
        return result

# All mission definitions
MISSION_DEFINITIONS = [
    Mission(
        id=1,
        title="Scout's First March",
        description="Prove your worth as a Scout by exploring beyond the safety of the walls.",
        requirement="Complete 500 explores outside the current location.",
        required_progress=500,
        reward_description="15,000 Marks",
        rewards={"marks": 15000},
    ),
    Mission(
        id=2,
        title="Marksman in Training",
        description="Hone your skills with the Training Dummy to improve your attack precision.",
        requirement="Use Training Dummy 1 time.",
        required_progress=1,
        reward_description="+2 Atk permanently",
        rewards={"permanent_stat": {"ATK": 2}},
    ),
    Mission(
        id=3,
        title="Light Purse, Heavy Steps",
        description="Accumulate wealth by defeating Titans to fund your expeditions.",
        requirement="Earn 10,000 Marks by defeating Titans",
        required_progress=10000,
        reward_description="5 Training Dummies",
        rewards={"items": {"training_dummy": 2}},
    ),
    Mission(
        id=4,
        title="Sparring Rounds",
        description="Test your skills against fellow soldiers in combat training.",
        requirement="Win 3 battles against unique players in one day.",
        required_progress=3,
        reward_description="12,000 Marks",
        rewards={"marks": 12000},
        time_limit_hours=24,
    ),
    Mission(
        id=5,
        title="Reclaiming",
        description="Gather materials to help rebuild and reinforce the protective walls.",
        requirement="Collect 100 Bricks to reconstruct the wall (🧱)",
        required_progress=100,
        reward_description="80,000 Marks + 2 Valor",
        rewards={"marks": 80000, "valor": 2},
    ),
    Mission(
        id=6,
        title="Travel Test",
        description="Navigate between checkpoints to test your mobility outside the walls.",
        requirement="Move between two adjacent checkpoints (e.g., Shiganshina → Ehrmich).",
        required_progress=1,
        reward_description="12,500 Marks",
        rewards={"marks": 12500},
    ),
    Mission(
        id=7,
        title="First Bounty Attempt",
        description="Test your mettle against a specially marked Titan target.",
        requirement="Use 1 Bounty Permit.",
        required_progress=1,
        reward_description="+40 Hp when lower than 100 (only in Titan Battles)",
        rewards={"special_ability": "emergency_heal_40"},
    ),
    Mission(
        id=8,
        title="Stray ODM Gear",
        description="Find ODM Gear parts left behind by fallen scouts to assemble your own.",
        requirement="Collect 5 Stray ODM Gear parts during exploration",
        required_progress=5,
        reward_description="+2 Valor",
        rewards={"valor": 2},
    ),
    Mission(
        id=9,
        title="Endurance Run",
        description="Test your endurance by exploring far from safety without returning home.",
        requirement="Complete 1000 explores without returning to home location.",
        required_progress=1000,
        reward_description="25,000 Marks",
        rewards={"marks": 25000},
    ),
    Mission(
        id=10,
        title="Tactician's Notes",
        description="Study battle tactics by using Battle Journals to record your fights.",
        requirement="Use Battle Journal 3 times (across different fights).",
        required_progress=3,
        reward_description="Permanent +5 Accuracy",
        rewards={"permanent_stat": {"ACC": 5}},
    ),
    Mission(
        id=11,
        title="Relentless Scout",
        description="Push yourself to the limits by exploring tirelessly across the land.",
        requirement="Complete 2500 explores in a single week.",
        required_progress=2500,
        reward_description="45,000 Marks + 25 permanent Exploration Attack",
        rewards={"marks": 45000, "permanent_stat": {"ATK": 25}},
        time_limit_hours=168,  # 7 days (1 week)
    ),
    Mission(
        id=12,
        title="Last Scout's Journal",
        description="Recover the scattered journals of fallen scouts to learn from their experiences.",
        requirement="Collect 10 Journals left by scouts during exploration",
        required_progress=10,
        reward_description="20,000 Marks and +5 permanent intelligence",
        rewards={"marks": 20000, "permanent_stat": {"INT": 5}},
    ),
    Mission(
        id=13,
        title="March of the Walls",
        description="Make the perilous journey from an outer district to the relative safety of Stohess.",
        requirement="Travel from an outer district to Stohess (cross at least 3 checkpoints).",
        required_progress=1,
        reward_description="50,000 Marks + 2 Time Contract Scroll",
        rewards={"marks": 50000, "items": {"time_contract_scroll": 2}},
    ),
    Mission(
        id=14,
        title="Never Stop!",
        description="Become a true master of the entire map by exploring every region thoroughly.",
        requirement="Complete 500 explores in each place of the map.",
        required_progress=10,  # Number of areas that need to be completed
        reward_description="250,000 Marks + 1 of each Utilities +50 PvP damage",
        rewards={
            "marks": 250000, 
            "items": {
                "training_dummy": 1,
                "battle_journal": 1,
                "bounty_permit": 1,
                "time_contract": 1,
                "titan_biology_manual": 1
            },
            "pvp_damage_bonus": 50
        },
    ),
    Mission(
        id=15,
        title="Temporal Gambit",
        description="Test your skills under pressure by fighting while time contracts are active.",
        requirement="Activate 3 Time Contract Scrolls in a row, survive 5 PvP fights under their effect.",
        required_progress=5,
        reward_description="1 Titan Crystal OR +25 Atk Permanently",
        rewards={"special_reward": "crystal_or_attack"},
    ),
]

# Dictionary for quick lookups by mission ID
MISSIONS_BY_ID = {mission.id: mission for mission in MISSION_DEFINITIONS}

# Special items that may drop during exploration
MISSION_ITEMS = {
    "brick": {
        "name": "Wall Brick",
        "emoji": "🧱",
        "description": "Material needed for wall reconstruction",
        "drop_chance": 0.0087,  # ~1 per 115 explores (range 100-130)
        "mission_id": 5
    },
    "odm_gear_part": {
        "name": "ODM Gear Part",
        "emoji": "⚙️",
        "description": "Component of the ODM mobility gear",
        "drop_chance": 0.0133,  # ~1 per 75 explores (range 70-80)
        "mission_id": 8
    },
    "scout_journal": {
        "name": "Scout's Journal",
        "emoji": "📔",
        "description": "Journal left behind by a fallen scout",
        "drop_chance": 0.0154,  # ~1 per 65 explores (range 50-80)
        "mission_id": 12
    }
}

async def get_available_missions(db, player):
    """Get list of available missions for a player based on level and prerequisites"""
    player_missions = getattr(player, "missions", [])
    completed_missions = [pm["mission_id"] for pm in player_missions 
                         if pm["status"] == MISSION_STATUS_COMPLETED]
    
    available_missions = []
    for mission in MISSION_DEFINITIONS:
        # No level requirement: show all missions
        # Check if mission already completed
        if mission.id in completed_missions:
            continue
        # Check prerequisites
        if all(prereq in completed_missions for prereq in mission.prerequisite_missions):
            available_missions.append(mission)
    return available_missions

async def get_active_missions(db, player):
    """Get player's currently active missions"""
    player_missions = getattr(player, "missions", [])
    active_missions = []
    
    for mission_progress in player_missions:
        if mission_progress["status"] == MISSION_STATUS_IN_PROGRESS:
            # Get mission definition
            mission_def = MISSIONS_BY_ID.get(mission_progress["mission_id"])
            if mission_def:
                active_missions.append({
                    "definition": mission_def,
                    "progress": mission_progress
                })
    
    return active_missions

async def update_mission_progress(db, player, mission_id: int, progress_amount: int):
    """Update progress for a specific mission"""
    if progress_amount <= 0:
        return None
        
    # Get current missions from database to ensure we have the latest data
    current_player = await db.get_player(player.user_id)
    player_missions = getattr(current_player, "missions", [])
    
    # Find the mission in player's active missions
    for i, mission_progress in enumerate(player_missions):
        if (mission_progress["mission_id"] == mission_id and 
            mission_progress["status"] == MISSION_STATUS_IN_PROGRESS):
            # Get mission definition
            mission_def = MISSIONS_BY_ID.get(mission_id)
            if not mission_def:
                return None
            # Update progress
            previous_progress = mission_progress["current_progress"]
            current_progress = min(previous_progress + progress_amount, mission_progress["required_progress"])
            # If no change, don't update or notify
            if current_progress == previous_progress:
                return None
            player_missions[i]["current_progress"] = current_progress
            # Save progress to DB after every update
            await db.update_player(player.user_id, {"missions": player_missions})
            # Check if mission is completed
            if current_progress >= mission_progress["required_progress"]:
                # Mark mission as completed but keep it in the missions list
                player_missions[i]["status"] = MISSION_STATUS_COMPLETED
                player_missions[i]["completed_at"] = datetime.now(timezone.utc)
                await db.update_player(player.user_id, {"missions": player_missions})
                # Apply rewards
                if mission_def:
                    await apply_mission_rewards(db, player, mission_def)
                    return f"🎉 *Mission Completed!* 🎉\n\n*{mission_def.title}*\nYou've earned: {mission_def.reward_description}"
            exploration_mission_ids = {1, 9, 11, 14}
            if mission_id in exploration_mission_ids:
                return None
            return None
            
    return None 

async def start_mission(db, player, mission_id: int):
    """Start a mission for a player"""
    # Check if mission exists
    if mission_id not in MISSIONS_BY_ID:
        return False, "Mission not found"
        
    mission = MISSIONS_BY_ID[mission_id]
    
    # Check if player already has this mission active or completed
    player_missions = getattr(player, "missions", [])
    for pm in player_missions:
        if pm["mission_id"] == mission_id:
            if pm["status"] == MISSION_STATUS_COMPLETED:
                return False, "You have already completed this mission"
            if pm["status"] == MISSION_STATUS_IN_PROGRESS:
                return False, "This mission is already in progress"
    
    # Check prerequisites
    for prereq_id in mission.prerequisite_missions:
        prereq_completed = False
        for pm in player_missions:
            if pm["mission_id"] == prereq_id and pm["status"] == MISSION_STATUS_COMPLETED:
                prereq_completed = True
                break
        if not prereq_completed:
            prereq_mission = MISSIONS_BY_ID.get(prereq_id)
            if prereq_mission:
                return False, f"You need to complete '{prereq_mission.title}' first"
            return False, f"You need to complete mission #{prereq_id} first"
    
    # Create mission progress object
    now = datetime.now(timezone.utc)
    expiry_at = None
    if mission.time_limit_hours:
        expiry_at = now + timedelta(hours=mission.time_limit_hours)

    mission_progress = {
        "mission_id": mission_id,
        "status": MISSION_STATUS_IN_PROGRESS,
        "current_progress": 0,
        "required_progress": mission.required_progress,
        "started_at": now,
        "expiry_at": expiry_at,
        "unique_opponents": []
    }

    # For Mission 1, set starting_location in mission progress
    # Get current missions from database to avoid overwriting
    current_player = await db.get_player(int(player.user_id))
    current_missions = getattr(current_player, "missions", [])
    update_fields = {"missions": current_missions + [mission_progress]}
    await db.update_player(int(player.user_id), update_fields)
    return True, f"Mission '{mission.title}' started!"

async def cancel_mission(db, player, mission_id: int):
    """Cancel an active mission and reset its progress"""
    # Get current missions from database to ensure we have the latest data
    current_player = await db.get_player(int(player.user_id))
    player_missions = getattr(current_player, "missions", [])

    # Find the mission in player's active missions
    for i, mission_progress in enumerate(player_missions):
        if (mission_progress["mission_id"] == mission_id and
            mission_progress["status"] == MISSION_STATUS_IN_PROGRESS):

            # Cancel the mission and reset progress
            player_missions[i]["status"] = MISSION_STATUS_CANCELLED
            player_missions[i]["cancelled_at"] = datetime.now(timezone.utc)
            player_missions[i]["current_progress"] = 0  # Reset progress to 0
            player_missions[i]["unique_opponents"] = []  # Reset unique opponents list

            # Update player in database
            await db.update_player(int(player.user_id), {"missions": player_missions})

            return True, f"Mission #{mission_id} has been cancelled and progress reset."

    return False, f"No active mission with ID {mission_id} found."

async def apply_mission_rewards(db, player, mission):
    """Apply the rewards from a completed mission"""
    rewards = mission.rewards
    update_data = {}
    
    # Apply marks
    if "marks" in rewards:
        update_data["marks"] = player.marks + rewards["marks"]
    
    # Apply valor
    if "valor" in rewards:
        update_data["valor"] = player.valor + rewards["valor"]
    
    # Apply items
    if "items" in rewards:
        inventory = getattr(player, "inventory", {})
        for item_key, amount in rewards["items"].items():
            if item_key not in inventory:
                inventory[item_key] = 0
            inventory[item_key] += amount
        update_data["inventory"] = inventory
    
    # Apply permanent stat boosts
    if "permanent_stat" in rewards:
        team = getattr(player, "team", [])
        if team:
            # Get the first character in team
            character_name = team[0].character_name if hasattr(team[0], "character_name") else team[0]
            character = await db.get_character(int(player.user_id), character_name)
            
            if character:
                stats = character.stats.dict()
                for stat, value in rewards["permanent_stat"].items():
                    stats[stat] = stats.get(stat, 0) + value
                
                # Update character stats
                await db.update_character_stats(int(player.user_id), character_name, stats)
    
    # Apply PvP damage bonus
    if "pvp_damage_bonus" in rewards:
        pvp_bonuses = getattr(player, "pvp_bonuses", {})
        if not pvp_bonuses:
            pvp_bonuses = {}
        
        # Add damage bonus
        current_damage_bonus = pvp_bonuses.get("damage_bonus", 0)
        pvp_bonuses["damage_bonus"] = current_damage_bonus + rewards["pvp_damage_bonus"]
        update_data["pvp_bonuses"] = pvp_bonuses
    
    # Apply special abilities if any
    if "special_ability" in rewards:
        # This would be handled elsewhere depending on the ability
        pass
    
    # Apply special rewards if any
    if "special_reward" in rewards:
        # These would be handled case by case
        if rewards["special_reward"] == "crystal_or_attack":
            # For now, just give a crystal
            if "crystal" not in update_data:
                update_data["crystal"] = player.crystal + 1
    
    # Update player in database
    if update_data:
        await db.update_player(int(player.user_id), update_data)
    
    return True

# Function to add to DB class
async def update_character_stats(db_instance, user_id, character_name, stats):
    """Update a character's stats (to be added to Database class)"""
    character = await db_instance.get_character(user_id, character_name)
    if character:
        character.stats = CharacterStats(**stats)
        await db_instance.update_character(character)
        return True
    return False

# Function to check for mission item drops during exploration
async def check_mission_item_drops(player):
    """Check if any mission items drop during exploration"""
    drops = []
    # Get active mission IDs for the player
    active_mission_ids = set()
    player_missions = getattr(player, "missions", [])
    for mission in player_missions:
        if mission.get("status") == "in_progress":
            active_mission_ids.add(mission.get("mission_id"))

    # Only allow drops for items whose missions are active
    for item_key, item_data in MISSION_ITEMS.items():
        # Only drop if the mission is active
        if item_data["mission_id"] in active_mission_ids:
            if random.random() < item_data["drop_chance"]:
                # Item dropped!
                drops.append({
                    "key": item_key,
                    "name": item_data["name"],
                    "emoji": item_data["emoji"],
                    "mission_id": item_data["mission_id"]
                })
    return drops

# Function to add mission items to player inventory
async def add_mission_item(db, player, item_key):
    """Add a mission item to player inventory and update related mission progress"""
    if item_key not in MISSION_ITEMS:
        return False, "Invalid item"
    
    # Add to inventory
    inventory = getattr(player, "inventory", {})
    if item_key not in inventory:
        inventory[item_key] = 0
    inventory[item_key] += 1
    
    # Update player
    await db.update_player(int(player.user_id), {"inventory": inventory})
    
    # Check for mission progress
    item_data = MISSION_ITEMS[item_key]
    mission_id = item_data["mission_id"]

    # Only show progress notification for missions 5, 8, 12 (collect missions)
    notify_progress = mission_id in (5, 8, 12)

    # Get current missions from database
    current_player = await db.get_player(player.user_id)
    player_missions = getattr(current_player, "missions", [])
    
    for mission_progress in player_missions:
        if (mission_progress["mission_id"] == mission_id and 
            mission_progress["status"] == MISSION_STATUS_IN_PROGRESS):
            notification = await update_mission_progress(db, player, mission_id, 1) if notify_progress else None
            if notify_progress:
                return True, f"Found {item_data['emoji']} {item_data['name']}! \n{notification if notification else ''}"
            else:
                return True, f"Found {item_data['emoji']} {item_data['name']}! "

    # Mission not active, just add to inventory
    return True, f"Found {item_data['emoji']} {item_data['name']}!"

# Function to process explore mission progress
async def process_explore_mission_progress(db, player, area=None):
    """Update mission progress related to exploration with batched updates"""
    player_missions = getattr(player, "missions", [])
    notifications = []
    batch_updates = {}  # Collect all updates to batch them
    
    for pm in player_missions:
        if pm["status"] != MISSION_STATUS_IN_PROGRESS:
            continue
            
        mission_id = pm["mission_id"]
        mission = MISSIONS_BY_ID.get(mission_id)
        
        if not mission:
            continue
            
        # Mission 1: Scout's First March (500 explores outside starting district)
        if mission_id == 1:
            starting_location = pm.get("starting_location")
            if area and starting_location and area != starting_location:
                # Update progress without immediate DB call
                previous_progress = pm["current_progress"]
                current_progress = min(previous_progress + 1, pm["required_progress"])
                if current_progress != previous_progress:
                    pm["current_progress"] = current_progress
                    batch_updates["missions"] = player_missions
                    
                    if current_progress >= pm["required_progress"]:
                        pm["status"] = MISSION_STATUS_COMPLETED
                        pm["completed_at"] = datetime.now(timezone.utc)
                        notifications.append(f"🎉 *Mission Completed!* 🎉\n\n*{mission.title}*\nYou've earned: {mission.reward_description}")
                
        # Mission 9: Endurance Run (1000 explores without returning home)
        if mission_id == 9:
            previous_progress = pm["current_progress"]
            current_progress = min(previous_progress + 1, pm["required_progress"])
            if current_progress != previous_progress:
                pm["current_progress"] = current_progress
                batch_updates["missions"] = player_missions
                
                if current_progress >= pm["required_progress"]:
                    pm["status"] = MISSION_STATUS_COMPLETED
                    pm["completed_at"] = datetime.now(timezone.utc)
                    notifications.append(f"🎉 *Mission Completed!* 🎉\n\n*{mission.title}*\nYou've earned: {mission.reward_description}")
                
        # Mission 11: Relentless Scout (2500 explores in a single week)
        if mission_id == 11:
            previous_progress = pm["current_progress"]
            current_progress = min(previous_progress + 1, pm["required_progress"])
            if current_progress != previous_progress:
                pm["current_progress"] = current_progress
                batch_updates["missions"] = player_missions
                
                if current_progress >= pm["required_progress"]:
                    pm["status"] = MISSION_STATUS_COMPLETED
                    pm["completed_at"] = datetime.now(timezone.utc)
                    notifications.append(f"🎉 *Mission Completed!* 🎉\n\n*{mission.title}*\nYou've earned: {mission.reward_description}")
                
        # Mission 14: Never Stop! (500 explores in each place of the map)
        if mission_id == 14:
            REQUIRED_AREAS = [
                "Orvud", "Krolva", "Mitras", "Royal Capital", "Utopia",
                "Karanes", "Stohess", "Trost", "Shiganshina", "Ehrmich"
            ]

            explore_counts = getattr(player, "area_explore_counts", {})
            if not explore_counts:
                explore_counts = {}

            if area:
                matching_area = None
                for required_area in REQUIRED_AREAS:
                    if area.lower() == required_area.lower() or required_area.lower() in area.lower():
                        matching_area = required_area
                        break

                if matching_area:
                    old_count = explore_counts.get(matching_area, 0)
                    explore_counts[matching_area] = old_count + 1
                    
                    if explore_counts[matching_area] == 500:
                        completed_areas = sum(1 for area_name in REQUIRED_AREAS
                                             if explore_counts.get(area_name, 0) >= 500)
                        
                        previous_progress = pm["current_progress"]
                        current_progress = min(previous_progress + 1, pm["required_progress"])
                        pm["current_progress"] = current_progress
                        batch_updates["missions"] = player_missions
                        batch_updates["area_explore_counts"] = explore_counts
                        
                        if current_progress >= pm["required_progress"]:
                            pm["status"] = MISSION_STATUS_COMPLETED
                            pm["completed_at"] = datetime.now(timezone.utc)
                            notifications.append(f"🎉 *Mission Completed!* 🎉\n\n*{mission.title}*\nYou've earned: {mission.reward_description}")
                        else:
                            notifications.append(f"🗺️ Area explored: {matching_area} - 500 explores reached!\n"
                                               f"Mission 14 Progress: {completed_areas}/{len(REQUIRED_AREAS)} areas completed")
                    else:
                        batch_updates["area_explore_counts"] = explore_counts
    
    # Apply all batched updates in a single operation
    if batch_updates:
        asyncio.create_task(db.batch_update_player(int(player.user_id), batch_updates))
    
    return notifications

# Function to process mission progress for PVP battles
async def process_pvp_mission_progress(db, player, won=True, opponent_id=None):
    """Update mission progress related to PvP battles"""
    player_missions = getattr(player, "missions", [])
    notifications = []
    updated = False
    
    # Check if won is True, as we only count wins for the mission
    if not won or not opponent_id:
        return notifications
        
    for pm in player_missions:
        if pm["status"] != MISSION_STATUS_IN_PROGRESS:
            continue
            
        mission_id = pm["mission_id"]
        
        # Mission 4: Sparring Rounds (Win 3 battles against unique players in one day)
        if mission_id == 4:
            # Check if opponent is already faced
            unique_opponents = pm.get("unique_opponents", [])
            if opponent_id not in unique_opponents:
                # New unique opponent, add to list and increment progress
                unique_opponents.append(opponent_id)
                pm["unique_opponents"] = unique_opponents
                notification = await update_mission_progress(db, player, mission_id, 1)
                updated = True
                if notification:
                    notifications.append(notification)
                
        # Mission 15: Temporal Gambit - check if Time Contract Scroll is active
        if mission_id == 15:
            # Check if player has active effects from pvp_system
            active_effects = getattr(player, "active_effects", {})
            if active_effects.get("time_contract"):
                notification = await update_mission_progress(db, player, mission_id, 1)
                updated = True
                if notification:
                    notifications.append(notification)
    
    # If updates were made, refresh player data
    if updated:
        updated_player = await db.get_player(player.user_id)
        if updated_player:
            for key, value in updated_player.__dict__.items():
                setattr(player, key, value)
    
    return notifications

# Function to process mission progress for Titan rewards
async def process_titan_reward_mission_progress(db, player, marks_earned):
    """Update mission progress related to earning marks from Titans"""
    player_missions = getattr(player, "missions", [])
    notifications = []
    updated = False
    
    for pm in player_missions:
        if pm["status"] != MISSION_STATUS_IN_PROGRESS:
            continue
            
        mission_id = pm["mission_id"]
        
        # Mission 3: Light Purse, Heavy Steps (Accumulate 10,000 Marks from Titans)
        if mission_id == 3:
            notification = await update_mission_progress(db, player, mission_id, marks_earned)
            updated = True
            if notification:
                notifications.append(notification)
    
    # If updates were made, refresh player data
    if updated:
        updated_player = await db.get_player(player.user_id)
        if updated_player:
            for key, value in updated_player.__dict__.items():
                setattr(player, key, value)
    
    return notifications

# Function to process mission progress for travel actions
async def process_travel_mission_progress(db, player, from_location, to_location):
    """Update mission progress related to travel"""
    player_missions = getattr(player, "missions", [])
    notifications = []
    updated = False
    
    for pm in player_missions:
        if pm["status"] != MISSION_STATUS_IN_PROGRESS:
            continue
            
        mission_id = pm["mission_id"]
        
        # Mission 6: Travel Test (Move between two adjacent checkpoints)
        if mission_id == 6:
            # We consider any travel as completing this mission
            notification = await update_mission_progress(db, player, mission_id, 1)
            updated = True
            if notification:
                notifications.append(notification)
                
        # Mission 13: March of the Walls (Travel from outer district to Stohess)
        if mission_id == 13 and to_location == "Stohess":
            # Define outer districts - any travel from these to Stohess counts
            outer_districts = ["Shiganshina", "Orvud", "Karanes", "Trost", "Ehrmich"]
            
            # Get the player's travel history or create if not exists
            travel_history = getattr(player, "travel_history", [])
            if not travel_history:
                travel_history = []
            
            # Add the current travel to history
            travel_history.append({
                "from": from_location,
                "to": to_location,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # Save updated travel history
            await db.update_player(int(player.user_id), {"travel_history": travel_history})
            updated = True
            
            # Check if coming from outer district or if player has passed through multiple checkpoints
            if from_location in outer_districts or len(travel_history) >= 3:
                notification = await update_mission_progress(db, player, mission_id, 1)
                if notification:
                    notifications.append(notification)
    
    # If updates were made, refresh player data
    if updated:
        updated_player = await db.get_player(player.user_id)
        if updated_player:
            for key, value in updated_player.__dict__.items():
                setattr(player, key, value)
    
    return notifications

# Function to process mission progress for item usage
async def process_item_use_mission_progress(db, player, item_key):
    """Update mission progress related to using items"""
    player_missions = getattr(player, "missions", [])
    notifications = []
    updated = False
    
    for pm in player_missions:
        if pm["status"] != MISSION_STATUS_IN_PROGRESS:
            continue
            
        mission_id = pm["mission_id"]
        
        # Mission 2: Marksman in Training (Use Training Dummy 1 time)
        if mission_id == 2 and item_key == "training_dummy":
            notification = await update_mission_progress(db, player, mission_id, 1)
            updated = True
            if notification:
                notifications.append(notification)
                
        # Mission 7: First Bounty Attempt (Use 1 Bounty Permit)
        if mission_id == 7 and item_key == "bounty_permit":
            notification = await update_mission_progress(db, player, mission_id, 1)
            updated = True
            if notification:
                notifications.append(notification)
                
        # Mission 10: Tactician's Notes (Use Battle Journal 3 times)
        if mission_id == 10 and item_key == "battle_journal":
            notification = await update_mission_progress(db, player, mission_id, 1)
            updated = True
            if notification:
                notifications.append(notification)
                
    # If updates were made, refresh player data
    if updated:
        updated_player = await db.get_player(player.user_id)
        if updated_player:
            for key, value in updated_player.__dict__.items():
                setattr(player, key, value)
    
    return notifications
