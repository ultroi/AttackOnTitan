import unittest
from datetime import datetime
from database.models import Character, Titan
from game.battle_system import BattleSystem
from database.characters import civilian_shell_effect, stimulant_injection_effect, nape_cutter_dash_effect


class TestBattleContextAndAbilities(unittest.TestCase):
    def test_battle_build_context_applies_buffs(self):
        # Create a character using an existing character_type so stats initialize predictably
        char = Character(user_id="1", name="Hero", character_type="Mina Carolina", current_hp=650)
        titan = Titan(name="Test Titan", level=1, max_hp=1000, abilities=[], created_at=datetime.now(), spawn_areas=[], xp_reward=0)
        battle = BattleSystem(char, titan, None, None, 0)
        # set a buff directly and verify build_context reflects it
        battle.buffs["ATK"] = 2.0
        ctx = battle.build_context("ability_use")
        self.assertEqual(ctx["character_stats"]["ATK"], int(char.stats.ATK * 2.0))

    def test_ability_effects_accept_dict_and_pvp_flag(self):
        ctx = {"character_stats": {"SPD": 20, "ACC": 15}, "first_damage_taken": False, "titan_hp": 120, "is_pvp": True}
        eff = civilian_shell_effect(ctx)
        self.assertIsNotNone(eff)
        self.assertTrue(hasattr(eff, 'buffs'))

        ctx2 = {"character_max_hp": 1000, "target_is_self": True, "is_pvp": True, "opponent_hp": 20, "opponent_max_hp": 100}
        eff2 = stimulant_injection_effect(ctx2)
        self.assertTrue(hasattr(eff2, 'healed'))

    def test_nape_cutter_damage_scales_with_ATK_in_pvp(self):
        ctx_low = {"character_stats": {"ATK": 10, "SPD": 30}, "is_pvp": True, "base_damage": 180, "titan_hp": 50}
        ctx_high = {"character_stats": {"ATK": 100, "SPD": 30}, "is_pvp": True, "base_damage": 180, "titan_hp": 50}
        eff_low = nape_cutter_dash_effect(ctx_low)
        eff_high = nape_cutter_dash_effect(ctx_high)
        self.assertTrue(eff_high.damage >= eff_low.damage)


if __name__ == "__main__":
    unittest.main()
