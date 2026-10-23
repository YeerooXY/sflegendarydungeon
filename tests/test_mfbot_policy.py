"""Pin the MFBot port to MFBotCore 6.35 LegendaryDungeonTasker's scoring rules."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_rl.mfbot_policy import (MIN, can_take_damage, healing_target, score_blessing, score_door,  # noqa: E402
                                  GEM_SCORE)


class MFBotRuleTests(unittest.TestCase):
    def test_door_scores(self):
        # ScoreDoor: stage-one monster bonus, mystery bonus, trap penalty, key-gated doors.
        self.assertEqual(score_door('monster', False, 0, 10, 100), 1900)
        self.assertEqual(score_door('monster', False, 0, 30, 100), 1400)
        self.assertEqual(score_door('mystery', True, 0, 30, 100), 1150)
        self.assertEqual(score_door('destiny', False, 0, 30, 100), 1600)
        self.assertEqual(score_door('epic', False, 1, 30, 100), MIN)
        self.assertEqual(score_door('epic', False, 2, 30, 100), 2100)
        self.assertEqual(score_door('locked', False, 0, 30, 100), MIN)
        self.assertEqual(score_door('locked', False, 35, 30, 100), 1550)  # 35*2 >= 100-30
        self.assertEqual(score_door('locked', False, 34, 30, 100), 1350)
        self.assertEqual(score_door('sacrifice', False, 0, 30, 29), 300)
        self.assertEqual(score_door('boss', False, 0, 24, 100), 10499)

    def test_fight_thresholds(self):
        # Band threshold +5, strict; pre-boss rooms (floor % 25 in 20..24) use boss thresholds.
        self.assertFalse(can_take_damage(19, 10));self.assertTrue(can_take_damage(20, 10))
        self.assertFalse(can_take_damage(25, 22));self.assertTrue(can_take_damage(26, 22))
        self.assertFalse(can_take_damage(35, 45));self.assertTrue(can_take_damage(36, 45))
        self.assertFalse(can_take_damage(27, 60));self.assertTrue(can_take_damage(28, 60))
        self.assertFalse(can_take_damage(55, 97));self.assertTrue(can_take_damage(56, 97))
        self.assertTrue(can_take_damage(31, 80))

    def test_healing_targets(self):
        self.assertEqual(healing_target(49, False), 20)
        self.assertEqual(healing_target(10, True), 80)
        self.assertEqual(healing_target(24, True), 80)
        self.assertEqual(healing_target(49, True), 35)
        self.assertEqual(healing_target(99, True), 55)
        self.assertEqual(healing_target(30, True), 27)
        self.assertEqual(healing_target(25, True), 20)

    def test_shop_and_gems(self):
        self.assertGreater(score_blessing('recovery', 5, 3, 100, 9), score_blessing('elixir', 50, 1, 40, 9))
        self.assertEqual(score_blessing('elixir', 50, 1, 60, 9), 40505)
        self.assertEqual(score_blessing('key_moment', 0, 0, 100, 4), 90000)
        self.assertLess(score_blessing('poison', 0, 0, 100, 4), 0)
        self.assertEqual(max(GEM_SCORE, key=GEM_SCORE.get), 'rabbit')
        self.assertEqual(min(GEM_SCORE, key=GEM_SCORE.get), 'lodestone')


if __name__ == '__main__':
    unittest.main()
