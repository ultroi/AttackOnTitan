"""
PvP Battle Test - All Characters vs All Characters
Tests real ability logic and PvP scalability
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from typing import Dict, Any, List
from database.characters import CHARACTERS, get_character_data, AbilityEffect

# Colors for terminal output
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    END = '\033[0m'

def create_battle_context(attacker_stats: Dict, defender_stats: Dict, 
                          attacker_hp: int, defender_hp: int,
                          attacker_max_hp: int, defender_max_hp: int,
                          turn: int = 1, base_damage: int = 50) -> Dict:
    """Create a PvP battle context dict"""
    hp_lost_percent = 1.0 - (attacker_hp / attacker_max_hp) if attacker_max_hp > 0 else 0
    demagogue_stacks = int(hp_lost_percent / 0.25)
    
    return {
        "character_stats": attacker_stats,
        "opponent_stats": defender_stats,
        "character_hp": attacker_hp,
        "opponent_hp": defender_hp,
        "character_max_hp": attacker_max_hp,
        "opponent_max_hp": defender_max_hp,
        "target_hp_percent": defender_hp / defender_max_hp if defender_max_hp > 0 else 1.0,
        "pvp": True,
        "is_pvp": True,
        "turn": turn,
        "gas": 500,
        "character_level": 100,
        "opponent_level": 100,
        "base_damage": base_damage,
        "ally_death_count": demagogue_stacks,
        "demagogue_stacks": demagogue_stacks,
        "flochs_last_standing": hp_lost_percent >= 0.5,
        "first_damage_taken": turn > 1,
        "dodge_count": random.randint(0, 3),
        "fear_counter": random.randint(0, 5),
        "focused_turns": random.randint(0, 3),
        "turns_not_focused": random.randint(0, 3),
        "just_dodged": random.random() < 0.2,
        "just_killed": False,
        "rapid_focus_active": random.random() < 0.3,
        "titan_hp_percent": defender_hp / defender_max_hp if defender_max_hp > 0 else 1.0,
    }

def get_all_abilities(char_data) -> List[Dict]:
    """Get all abilities from a character"""
    abilities = []
    if hasattr(char_data, 'passive_abilities'):
        abilities.extend(char_data.passive_abilities or [])
    if hasattr(char_data, 'active_abilities'):
        abilities.extend(char_data.active_abilities or [])
    if hasattr(char_data, 'ultimate_abilities'):
        abilities.extend(char_data.ultimate_abilities or [])
    return abilities

def get_ability_attr(ability, attr: str, default=None):
    """Get attribute from ability (handles both dict and Pydantic model)"""
    if isinstance(ability, dict):
        return ability.get(attr, default)
    return getattr(ability, attr, default)

def simulate_ability(ability, ctx: Dict) -> AbilityEffect:
    """Execute an ability's effect function"""
    effect_func = get_ability_attr(ability, "effect_function")
    if effect_func and callable(effect_func):
        try:
            return effect_func(ctx)
        except Exception as e:
            return AbilityEffect(message=f"ERROR: {str(e)}", damage=0, buffs={}, debuffs={})
    return AbilityEffect(message="No effect function", damage=0, buffs={}, debuffs={})

def calculate_damage(base_damage: int, attacker_atk: int, defender_def: int) -> int:
    """Calculate actual damage after defense"""
    raw_damage = base_damage + attacker_atk
    def_reduction = min(0.7, defender_def / 200)  # Max 70% reduction
    return max(1, int(raw_damage * (1 - def_reduction)))

def apply_buffs_to_stats(base_stats: Dict, buffs: Dict, debuffs: Dict) -> Dict:
    """
    Apply buffs and debuffs to get EFFECTIVE stats
    
    Buff format:
    - Float (e.g., 1.5) = multiplier (+50%)
    - Int (e.g., 15) = additive bonus
    
    This is what makes buffs actually work!
    """
    effective_stats = base_stats.copy()
    
    # Apply buffs (from abilities like Last Bastion of War, Demagogue's Aura)
    for stat in ["ATK", "DEF", "ACC", "INT", "SPD"]:
        if stat in buffs:
            buff_value = buffs[stat]
            if isinstance(buff_value, float) and buff_value != 1.0:
                # Multiplier buff (e.g., ATK=1.5 means +50% ATK)
                effective_stats[stat] = int(effective_stats[stat] * buff_value)
            elif isinstance(buff_value, int) and buff_value > 0:
                # Additive buff (e.g., SPD=22 means +22 SPD)
                effective_stats[stat] = effective_stats[stat] + buff_value
    
    # Apply debuffs (reduce stats)
    for stat in ["ATK", "DEF", "ACC", "INT", "SPD"]:
        if stat in debuffs:
            debuff_value = debuffs[stat]
            if isinstance(debuff_value, float) and debuff_value < 1.0:
                # Multiplier debuff (e.g., DEF=0.8 means -20% DEF)
                effective_stats[stat] = int(effective_stats[stat] * debuff_value)
            elif isinstance(debuff_value, (int, float)) and debuff_value > 0:
                # Flat debuff (e.g., ACC=15 means -15 ACC)
                effective_stats[stat] = max(1, effective_stats[stat] - int(debuff_value))
    
    return effective_stats

def run_pvp_battle(char1_name: str, char2_name: str, verbose: bool = True) -> Dict:
    """Simulate a full PvP battle between two characters"""
    char1_data = get_character_data(char1_name)
    char2_data = get_character_data(char2_name)
    
    if not char1_data or not char2_data:
        return {"winner": None, "error": "Character not found"}
    
    # Get BASE stats (use max_potential for fair fight)
    char1_base_stats = char1_data.max_potential.copy() if isinstance(char1_data.max_potential, dict) else char1_data.max_potential.dict()
    char2_base_stats = char2_data.max_potential.copy() if isinstance(char2_data.max_potential, dict) else char2_data.max_potential.dict()
    
    # BUFF TRACKING - this is what makes buffs actually work!
    char1_buffs = {}  # Active buffs for char1
    char2_buffs = {}  # Active buffs for char2
    char1_debuffs = {}  # Active debuffs on char1
    char2_debuffs = {}  # Active debuffs on char2
    
    char1_hp = char1_base_stats["HP"]
    char2_hp = char2_base_stats["HP"]
    char1_max_hp = char1_base_stats["HP"]
    char2_max_hp = char2_base_stats["HP"]
    
    char1_abilities = get_all_abilities(char1_data)
    char2_abilities = get_all_abilities(char2_data)
    
    turn = 0
    max_turns = 30
    battle_log = []
    
    if verbose:
        print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
        print(f"{Colors.CYAN}⚔️  {char1_name} vs {char2_name}{Colors.END}")
        print(f"{Colors.BOLD}{'='*60}{Colors.END}")
        print(f"{Colors.GREEN}{char1_name}: HP={char1_max_hp}, ATK={char1_base_stats['ATK']}, DEF={char1_base_stats['DEF']}, SPD={char1_base_stats['SPD']}{Colors.END}")
        print(f"{Colors.RED}{char2_name}: HP={char2_max_hp}, ATK={char2_base_stats['ATK']}, DEF={char2_base_stats['DEF']}, SPD={char2_base_stats['SPD']}{Colors.END}")
        print()
    
    while char1_hp > 0 and char2_hp > 0 and turn < max_turns:
        turn += 1
        
        # Apply buffs to get EFFECTIVE stats each turn
        char1_stats = apply_buffs_to_stats(char1_base_stats.copy(), char1_buffs, char1_debuffs)
        char2_stats = apply_buffs_to_stats(char2_base_stats.copy(), char2_buffs, char2_debuffs)
        
        # Per-turn effects: HP regen and reflection duration decrement
        if "hp_regen" in char1_buffs:
            regen = char1_buffs["hp_regen"]
            heal_amt = int(char1_max_hp * regen) if isinstance(regen, float) and regen < 1.0 else int(regen)
            char1_hp = min(char1_max_hp, char1_hp + heal_amt)
            if "hp_regen_turns" in char1_buffs:
                char1_buffs["hp_regen_turns"] -= 1
                if char1_buffs["hp_regen_turns"] <= 0:
                    char1_buffs.pop("hp_regen", None)
                    char1_buffs.pop("hp_regen_turns", None)
        if "hp_regen" in char2_buffs:
            regen = char2_buffs["hp_regen"]
            heal_amt = int(char2_max_hp * regen) if isinstance(regen, float) and regen < 1.0 else int(regen)
            char2_hp = min(char2_max_hp, char2_hp + heal_amt)
            if "hp_regen_turns" in char2_buffs:
                char2_buffs["hp_regen_turns"] -= 1
                if char2_buffs["hp_regen_turns"] <= 0:
                    char2_buffs.pop("hp_regen", None)
                    char2_buffs.pop("hp_regen_turns", None)

        # Decrement reflection_duration and clear damage_reflection when expired
        if "reflection_duration" in char1_buffs:
            char1_buffs["reflection_duration"] -= 1
            if char1_buffs["reflection_duration"] <= 0:
                char1_buffs.pop("damage_reflection", None)
                char1_buffs.pop("reflection_duration", None)
        if "reflection_duration" in char2_buffs:
            char2_buffs["reflection_duration"] -= 1
            if char2_buffs["reflection_duration"] <= 0:
                char2_buffs.pop("damage_reflection", None)
                char2_buffs.pop("reflection_duration", None)

        # Determine who goes first based on EFFECTIVE SPD (buffs matter!)
        if char1_stats["SPD"] >= char2_stats["SPD"]:
            first, second = (char1_name, char1_stats, char1_base_stats, char1_abilities, "char1"), (char2_name, char2_stats, char2_base_stats, char2_abilities, "char2")
        else:
            first, second = (char2_name, char2_stats, char2_base_stats, char2_abilities, "char2"), (char1_name, char1_stats, char1_base_stats, char1_abilities, "char1")
        
        for attacker_name, attacker_stats, attacker_base, attacker_abilities, attacker_key in [first, second]:
            if char1_hp <= 0 or char2_hp <= 0:
                break
                
            # Get current HP and stats (with buffs applied)
            if attacker_key == "char1":
                attacker_hp, attacker_max = char1_hp, char1_max_hp
                defender_hp, defender_max = char2_hp, char2_max_hp
                defender_stats_current = char2_stats
                defender_name = char2_name
                attacker_buffs = char1_buffs
                defender_debuffs = char2_debuffs
            else:
                attacker_hp, attacker_max = char2_hp, char2_max_hp
                defender_hp, defender_max = char1_hp, char1_max_hp
                defender_stats_current = char1_stats
                defender_name = char1_name
                attacker_buffs = char2_buffs
                defender_debuffs = char1_debuffs
            
            # Choose a random ability (prioritize actives/ultimates)
            usable_abilities = [a for a in attacker_abilities if get_ability_attr(a, "effect_function")]
            if not usable_abilities:
                # Basic attack using BUFFED ATK
                damage = calculate_damage(50, attacker_stats["ATK"], defender_stats_current["DEF"])
                if attacker_key == "char1":
                    char2_hp -= damage
                else:
                    char1_hp -= damage
                if verbose:
                    print(f"  Turn {turn}: {attacker_name} basic attack → {damage} damage")
                continue
            
            # Pick ability (weighted towards actives/ultimates)
            weights = []
            for ab in usable_abilities:
                if get_ability_attr(ab, "type") == "ultimate":
                    weights.append(3)
                elif get_ability_attr(ab, "type") == "active":
                    weights.append(2)
                else:
                    weights.append(1)
            
            ability = random.choices(usable_abilities, weights=weights, k=1)[0]
            
            # Create context with BUFFED stats
            base_dmg = get_ability_attr(ability, "base_damage", 50) or 50
            ctx = create_battle_context(
                attacker_stats, defender_stats_current,  # Using buffed stats!
                attacker_hp, defender_hp,
                attacker_max, defender_max,
                turn, base_dmg
            )
            
            # Execute ability
            effect = simulate_ability(ability, ctx)
            
            # Apply damage (using BUFFED ATK and defender's BUFFED DEF)
            total_damage = effect.damage if effect.damage else 0
            if total_damage > 0:
                # Apply ATK buff to damage
                atk_multiplier = attacker_buffs.get("ATK", 1.0) if isinstance(attacker_buffs.get("ATK", 1.0), float) else 1.0
                if atk_multiplier > 1.0:
                    total_damage = int(total_damage * atk_multiplier)

                # Apply explicit damage_multiplier buff (e.g. Floch last-stand)
                dmg_mult = attacker_buffs.get("damage_multiplier", 1.0)
                if isinstance(dmg_mult, (int, float)) and dmg_mult != 1.0:
                    total_damage = int(total_damage * dmg_mult)

                # Apply defense reduction with BUFFED DEF
                def_reduction = min(0.7, defender_stats_current["DEF"] / 200)
                actual_damage = max(1, int(total_damage * (1 - def_reduction)))
            else:
                actual_damage = 0
            
            # Apply healing
            healed = effect.healed if hasattr(effect, 'healed') and effect.healed else 0
            
            # APPLY BUFFS FROM ABILITY (this is the key part!)
            if effect.buffs:
                for buff_name, buff_value in effect.buffs.items():
                    # stat multipliers / additive stats
                    if buff_name in ["ATK", "DEF", "ACC", "INT", "SPD", "HP", "crit_rate", "evasion", "dodge_rate"]:
                        attacker_buffs[buff_name] = buff_value
                    # additional supported keys (now simulated)
                    elif buff_name in ["damage_reflection", "reflection_duration", "hp_regen", "hp_regen_turns", "damage_multiplier", "extra_action", "extra_move"]:
                        attacker_buffs[buff_name] = buff_value
                    else:
                        # keep other flags for visibility
                        attacker_buffs[buff_name] = buff_value

            # APPLY DEBUFFS TO ENEMY
            if effect.debuffs:
                for debuff_name, debuff_value in effect.debuffs.items():
                    defender_debuffs[debuff_name] = debuff_value
            
            # Update HP
            if attacker_key == "char1":
                char2_hp -= actual_damage
                char1_hp = min(char1_max_hp, char1_hp + healed)
                char1_buffs = attacker_buffs
                char2_debuffs = defender_debuffs

                # Damage reflection (if defender had it)
                refl = char2_buffs.get("damage_reflection", 0)
                if refl and actual_damage > 0:
                    reflected = int(actual_damage * refl)
                    char1_hp = max(0, char1_hp - reflected)
                    if verbose:
                        print(f"    → {char2_name} reflected {reflected} damage back to {char1_name}")

                # Extra action: consume one and grant an immediate basic attack
                if char1_buffs.get("extra_action"):
                    extra_val = char1_buffs.get("extra_action")
                    # consume
                    if isinstance(extra_val, (int, float)):
                        if extra_val > 1:
                            char1_buffs["extra_action"] = extra_val - 1
                        else:
                            del char1_buffs["extra_action"]
                    # perform an immediate basic attack
                    extra_dmg = calculate_damage(50, char1_stats["ATK"], char2_stats["DEF"])
                    # apply ATK multiplier and damage_multiplier if present
                    atk_mul = char1_buffs.get("ATK", 1.0) if isinstance(char1_buffs.get("ATK", 1.0), float) else 1.0
                    if atk_mul > 1.0:
                        extra_dmg = int(extra_dmg * atk_mul)
                    extra_dmg = int(extra_dmg * char1_buffs.get("damage_multiplier", 1.0))
                    # apply defender def
                    def_red = min(0.7, char2_stats["DEF"]/200)
                    extra_actual = max(1, int(extra_dmg * (1-def_red)))
                    char2_hp = max(0, char2_hp - extra_actual)
                    if verbose:
                        print(f"    → Extra action: {char1_name} deals {extra_actual} extra damage")

            else:
                char1_hp -= actual_damage
                char2_hp = min(char2_max_hp, char2_hp + healed)
                char2_buffs = attacker_buffs
                char1_debuffs = defender_debuffs

                # Damage reflection (if defender had it)
                refl = char1_buffs.get("damage_reflection", 0)
                if refl and actual_damage > 0:
                    reflected = int(actual_damage * refl)
                    char2_hp = max(0, char2_hp - reflected)
                    if verbose:
                        print(f"    → {char1_name} reflected {reflected} damage back to {char2_name}")

                # Extra action for defender side
                if char2_buffs.get("extra_action"):
                    extra_val = char2_buffs.get("extra_action")
                    if isinstance(extra_val, (int, float)):
                        if extra_val > 1:
                            char2_buffs["extra_action"] = extra_val - 1
                        else:
                            del char2_buffs["extra_action"]
                    extra_dmg = calculate_damage(50, char2_stats["ATK"], char1_stats["DEF"])
                    atk_mul = char2_buffs.get("ATK", 1.0) if isinstance(char2_buffs.get("ATK", 1.0), float) else 1.0
                    if atk_mul > 1.0:
                        extra_dmg = int(extra_dmg * atk_mul)
                    extra_dmg = int(extra_dmg * char2_buffs.get("damage_multiplier", 1.0))
                    def_red = min(0.7, char1_stats["DEF"]/200)
                    extra_actual = max(1, int(extra_dmg * (1-def_red)))
                    char1_hp = max(0, char1_hp - extra_actual)
                    if verbose:
                        print(f"    → Extra action: {char2_name} deals {extra_actual} extra damage")
            
            if verbose:
                ability_name = get_ability_attr(ability, "name", "Unknown")
                color = Colors.GREEN if attacker_key == "char1" else Colors.RED
                print(f"  {color}Turn {turn}: {attacker_name} uses {ability_name}{Colors.END}")
                if actual_damage > 0:
                    print(f"    → Dealt {actual_damage} damage to {defender_name}")
                if healed > 0:
                    print(f"    → Healed {healed} HP")
                if effect.buffs:
                    buffs_str = ", ".join([f"{k}={v}" for k, v in effect.buffs.items() if not k.startswith("immune")])
                    if buffs_str:
                        print(f"    → {Colors.CYAN}Buffs APPLIED: {buffs_str}{Colors.END}")
                if effect.debuffs:
                    debuffs_str = ", ".join([f"{k}={v}" for k, v in effect.debuffs.items()])
                    if debuffs_str:
                        print(f"    → {Colors.MAGENTA}Debuffs on {defender_name}: {debuffs_str}{Colors.END}")
                
                # Show EFFECTIVE stats with buffs
                eff_atk = attacker_stats["ATK"]
                base_atk = attacker_base["ATK"]
                if eff_atk != base_atk:
                    print(f"    → {Colors.YELLOW}[BUFF EFFECT] ATK: {base_atk} → {eff_atk} (+{int((eff_atk/base_atk-1)*100)}%){Colors.END}")
                
                # Show HP status
                print(f"    {Colors.YELLOW}[{char1_name}: {max(0, char1_hp)}/{char1_max_hp} HP | {char2_name}: {max(0, char2_hp)}/{char2_max_hp} HP]{Colors.END}")
    
    # Determine winner
    if char1_hp <= 0 and char2_hp <= 0:
        winner = "Draw"
    elif char1_hp <= 0:
        winner = char2_name
    elif char2_hp <= 0:
        winner = char1_name
    else:
        # Timeout - whoever has more HP% wins
        char1_pct = char1_hp / char1_max_hp
        char2_pct = char2_hp / char2_max_hp
        winner = char1_name if char1_pct > char2_pct else char2_name
    
    if verbose:
        print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
        if winner == "Draw":
            print(f"{Colors.YELLOW}🤝 DRAW! Both fighters exhausted!{Colors.END}")
        else:
            print(f"{Colors.MAGENTA}🏆 WINNER: {winner}!{Colors.END}")
        print(f"Final HP: {char1_name}={max(0, char1_hp)} | {char2_name}={max(0, char2_hp)}")
        print(f"Turns: {turn}")
        # Show final buff status
        print(f"\n{Colors.CYAN}Final Buffs:{Colors.END}")
        print(f"  {char1_name}: {char1_buffs if char1_buffs else 'None'}")
        print(f"  {char2_name}: {char2_buffs if char2_buffs else 'None'}")
        print(f"{Colors.BOLD}{'='*60}{Colors.END}")
    
    return {
        "winner": winner,
        "turns": turn,
        "char1_final_hp": max(0, char1_hp),
        "char2_final_hp": max(0, char2_hp)
    }

def test_all_abilities():
    """Test all ability effect functions individually"""
    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.CYAN}🔬 TESTING ALL ABILITY EFFECT FUNCTIONS{Colors.END}")
    print(f"{Colors.BOLD}{'='*70}{Colors.END}\n")
    
    total_abilities = 0
    passed = 0
    failed = 0
    
    for char_name, char_data in CHARACTERS.items():
        print(f"\n{Colors.YELLOW}📋 {char_name}'s Abilities:{Colors.END}")
        
        stats = char_data.max_potential.copy() if isinstance(char_data.max_potential, dict) else char_data.max_potential.dict()
        
        abilities = get_all_abilities(char_data)
        
        for ability in abilities:
            total_abilities += 1
            name = get_ability_attr(ability, "name", "Unknown")
            ab_type = get_ability_attr(ability, "type", "?")
            
            # Create test context
            ctx = create_battle_context(
                stats, {"ATK": 100, "DEF": 100, "ACC": 100, "INT": 100, "SPD": 100, "HP": 1000},
                stats["HP"], 800,
                stats["HP"], 1000,
                turn=3, base_damage=get_ability_attr(ability, "base_damage", 50) or 50
            )
            
            try:
                effect = simulate_ability(ability, ctx)
                
                # Validate effect
                if effect and hasattr(effect, 'message'):
                    if "ERROR" in effect.message:
                        print(f"  {Colors.RED}❌ {name} ({ab_type}): {effect.message}{Colors.END}")
                        failed += 1
                    else:
                        dmg_str = f"dmg={effect.damage}" if effect.damage else ""
                        buff_count = len(effect.buffs) if effect.buffs else 0
                        debuff_count = len(effect.debuffs) if effect.debuffs else 0
                        print(f"  {Colors.GREEN}✅ {name} ({ab_type}): {dmg_str} buffs={buff_count} debuffs={debuff_count}{Colors.END}")
                        passed += 1
                else:
                    print(f"  {Colors.RED}❌ {name} ({ab_type}): Invalid effect returned{Colors.END}")
                    failed += 1
            except Exception as e:
                print(f"  {Colors.RED}❌ {name} ({ab_type}): Exception - {str(e)}{Colors.END}")
                failed += 1
    
    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"Total Abilities: {total_abilities}")
    print(f"{Colors.GREEN}Passed: {passed}{Colors.END}")
    print(f"{Colors.RED}Failed: {failed}{Colors.END}")
    print(f"{Colors.BOLD}{'='*70}{Colors.END}")
    
    return passed, failed

def run_tournament():
    """Run a round-robin tournament between all characters"""
    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.CYAN}🏆 PVP TOURNAMENT - ALL vs ALL{Colors.END}")
    print(f"{Colors.BOLD}{'='*70}{Colors.END}\n")
    
    char_names = list(CHARACTERS.keys())
    wins = {name: 0 for name in char_names}
    losses = {name: 0 for name in char_names}
    draws = {name: 0 for name in char_names}
    
    total_battles = 0
    
    for i, char1 in enumerate(char_names):
        for char2 in char_names[i+1:]:
            total_battles += 1
            print(f"\n{Colors.MAGENTA}Battle {total_battles}: {char1} vs {char2}{Colors.END}")
            
            result = run_pvp_battle(char1, char2, verbose=True)
            
            if result["winner"] == "Draw":
                draws[char1] += 1
                draws[char2] += 1
            elif result["winner"] == char1:
                wins[char1] += 1
                losses[char2] += 1
            else:
                wins[char2] += 1
                losses[char1] += 1
    
    # Print standings
    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.CYAN}🏆 FINAL STANDINGS{Colors.END}")
    print(f"{Colors.BOLD}{'='*70}{Colors.END}\n")
    
    # Sort by wins
    standings = sorted(char_names, key=lambda x: (wins[x], -losses[x]), reverse=True)
    
    for rank, name in enumerate(standings, 1):
        emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        print(f"{emoji} {rank}. {name}: {wins[name]}W - {losses[name]}L - {draws[name]}D")
    
    return wins, losses, draws

if __name__ == "__main__":
    print(f"{Colors.BOLD}")
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║       ATTACK ON TITAN - PVP BATTLE TEST SYSTEM                ║")
    print("║       Testing All Character Abilities & Combat Logic          ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print(f"{Colors.END}")
    
    # Test 1: Validate all ability functions
    print("\n[1/2] Testing all ability effect functions...")
    passed, failed = test_all_abilities()
    
    if failed > 0:
        print(f"\n{Colors.RED}⚠️  Some abilities have issues! Check the errors above.{Colors.END}")
    else:
        print(f"\n{Colors.GREEN}✅ All abilities working correctly!{Colors.END}")
    
    # Test 2: Run tournament
    print("\n[2/2] Running PvP Tournament...")
    wins, losses, draws = run_tournament()
    
    print(f"\n{Colors.GREEN}✅ PvP Battle Test Complete!{Colors.END}")
