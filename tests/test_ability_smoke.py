import unittest
from database.characters import (
    civilian_shell_effect, mocking_delay_effect, arc_net_trap_effect,
    stimulant_injection_effect
)


class TestAbilitySmoke(unittest.TestCase):
    def test_smoke_calls_no_exceptions(self):
        samples = [
            (civilian_shell_effect, {"character_stats": {"SPD": 20, "ACC": 10}, "titan_hp": 120}),
            (mocking_delay_effect, {"character_stats": {"INT": 20, "ACC": 10}, "titan_hp": 60}),
            (arc_net_trap_effect, {"character_stats": {"INT": 12, "SPD": 18}, "base_damage": 50, "titan_hp": 30}),
            (stimulant_injection_effect, {"character_max_hp": 1000, "target_is_self": True})
        ]
        for fn, ctx in samples:
            with self.subTest(fn=fn.__name__):
                eff = fn(ctx)
                self.assertIsNotNone(eff)


if __name__ == "__main__":
    unittest.main()
