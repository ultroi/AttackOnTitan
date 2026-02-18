import unittest
from utils.stats import apply_stat_buffs, get_effective_stat


class TestStatsUtils(unittest.TestCase):
    def test_apply_multiplier_and_additive(self):
        raw = {"ATK": 100, "DEF": 50, "ACC": 20, "INT": 10, "SPD": 30, "HP": 1000}
        buffs = {"ATK": 1.5, "DEF": 5, "ACC": 2.0, "HP": -100}
        res = apply_stat_buffs(raw, buffs)
        self.assertEqual(res["ATK"], 150)
        self.assertEqual(res["DEF"], 55)
        self.assertEqual(res["ACC"], 40)
        self.assertEqual(res["HP"], 900)
        # original dict must not be mutated
        self.assertEqual(raw["ATK"], 100)

    def test_get_effective_stat_missing(self):
        raw = {"ATK": 50}
        buffs = {}
        self.assertEqual(get_effective_stat(raw, buffs, "ATK"), 50)
        self.assertEqual(get_effective_stat(raw, buffs, "DEF"), 0)


if __name__ == "__main__":
    unittest.main()
