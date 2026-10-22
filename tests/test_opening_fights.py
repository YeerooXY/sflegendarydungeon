"""Intervention boundaries for the opening two-fight study, on the native engine."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from visual_lab import default_engine
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.interaction_cases import state
from sfld_rl.player_ideas import profile_values
from sfld_rl.opening_fights import OpeningFightPolicy


class OpeningFightTests(unittest.TestCase):
    def setUp(self):
        self.profile = ROOT/'profiles/synthetic.profile'
        self.env = VectorEnv(default_engine().with_name('sfld_rl.dll'), [self.profile], 1,
                             budgets=[100], auto_reset=False)
        self.env.reset()
        self.lab = EffectLab(self.env)

    def tearDown(self):
        self.env.close()

    def policy(self, variant='two_all'):
        return OpeningFightPolicy(self.env, profile_values(self.profile), variant)

    def choose(self, rule, proposed='door_1', **changes):
        fields = dict(room=1, hp=100, keys=0, gems='none', doors='monster,mystery', run_number=1)
        fields.update(changes)
        if fields.get('phase') == 'encounter':
            fields.pop('doors')
        with self.lab.fixture(state(**fields), 100) as snapshot:
            self.lab.restore(0, snapshot, 17)
        obs, masks = self.lab.observe()
        actions, _, _ = rule.apply(obs, masks, np.array([self.env.action_names.index(proposed)]))
        return self.env.action_names[int(actions[0])]

    def test_only_opening_mystery_or_destiny_alternatives(self):
        for doors, expected in [('monster,mystery','door_0'), ('monster,destiny','door_0'),
                                ('monster,blessing','door_1'), ('monster,wood','door_1')]:
            self.assertEqual(self.choose(self.policy(), doors=doors, resources='10,10,10,10,10,10'), expected, doors)
        self.assertEqual(self.choose(self.policy(), 'door_0', doors='mystery,monster'), 'door_1')
        self.assertEqual(self.choose(self.policy(), room=6), 'door_1')
        with self.assertRaisesRegex(RuntimeError, 'Rooms 1-5 cannot contain traps'):
            self.choose(self.policy(), doors='monster:trap,mystery')

    def test_survives_hit_and_exit_ticks(self):
        self.assertEqual(self.choose(self.policy(), hp=14), 'door_1')
        self.assertEqual(self.choose(self.policy(), hp=16, curses='poison:weak:4'), 'door_1')
        self.assertEqual(self.choose(self.policy(), hp=16, curses='poison:weak:4',
                                     blessings='recovery:weak:3'), 'door_0')
        self.assertEqual(self.choose(self.policy(), hp=3, blessings='one_hit:weak:3'), 'door_0')
        self.assertEqual(self.choose(self.policy(), hp=20, curses='broken_armor:weak:4'), 'door_1')

    def test_two_fights_then_stop_and_reset_for_new_run(self):
        rule = self.policy()
        for room in (1, 2):
            self.assertEqual(self.choose(rule, room=room), 'door_0')
            self.assertEqual(self.choose(rule, 'flee', room=room, phase='encounter', encounter='monster'), 'fight')
        self.assertEqual(int(rule.fights[0]), 2)
        self.assertEqual(self.choose(rule, room=3), 'door_1')
        self.assertEqual(self.choose(rule, room=1, run_number=2), 'door_0')
        self.assertEqual(int(rule.fights[0]), 0)

    def test_never_forces_fight_in_unselected_encounter(self):
        self.assertEqual(self.choose(self.policy(), 'flee', phase='encounter', encounter='monster'), 'flee')

    def test_variant_scopes(self):
        for variant, first, later in [('control','door_1','door_1'),
                                      ('two_first','door_0','door_1'), ('two_later','door_1','door_0')]:
            self.assertEqual(self.choose(self.policy(variant), run_number=1), first)
            self.assertEqual(self.choose(self.policy(variant), run_number=2), later)
        for blessings, expected in [('none','door_1'), ('one_hit:weak:3','door_0'),
                                    ('recovery:weak:3','door_1'), ('recovery:strong:6','door_0')]:
            self.assertEqual(self.choose(self.policy('two_buffed'), blessings=blessings), expected)


if __name__ == '__main__':
    unittest.main()
