"""Rule-layer loot: the free sarcophagus, and the cursed chest when leaving kills anyway."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from sfld_rl.numpy_policy import load  # noqa: E402
from sfld_rl.player_ideas import profile_values  # noqa: E402
from sfld_rl.practical import Practical  # noqa: E402
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA  # noqa: E402


class FreeGoldTests(unittest.TestCase):
    def setUp(self):
        info, _ = load(CHECKPOINT, CHECKPOINT_SHA)
        self.names, self.actions = info['observation_names'], info['action_names']
        self.rule = Practical(self.names, self.actions, 'learned', profile_values(ROOT/'profiles/synthetic.profile'))

    def choose(self, encounter, allowed, proposed, hp=.6, **features):
        obs = np.zeros((1, len(self.names)), np.float32)
        for name in ('phase.encounter', 'encounter.' + encounter):obs[0, self.names.index(name)] = 1
        obs[0, self.names.index('hp')] = hp;obs[0, self.names.index('room_div100')] = .4
        for name, value in features.items():obs[0, self.names.index(name)] = value
        mask = np.zeros((1, len(self.actions)), bool)
        for name in allowed:mask[0, self.actions.index(name)] = True
        return self.actions[int(self.rule.apply(obs, mask, np.array([self.actions.index(proposed)]))[0][0])]

    def test_free_sarcophagus_is_always_opened(self):
        self.assertEqual(self.choose('sarcophagus', ('interact', 'skip'), 'skip'), 'interact')

    def test_locked_sarcophagus_stays_the_policy_choice(self):
        self.assertEqual(self.choose('locked_sarcophagus', ('interact', 'skip'), 'skip'), 'skip')

    def test_cursed_chest_is_opened_only_when_leaving_kills(self):
        poison = {'curses.0.present': 1, 'curses.0.active': 1, 'curses.0.kind.poison': 1, 'curses.0.magnitude': .05}
        self.assertEqual(self.choose('cursed_chest', ('interact', 'skip'), 'skip', hp=.006, **poison), 'interact')
        self.assertEqual(self.choose('cursed_chest', ('interact', 'skip'), 'skip', hp=.3, **poison), 'skip')
        recovery = {'blessings.0.present': 1, 'blessings.0.active': 1, 'blessings.0.kind.recovery': 1, 'blessings.0.magnitude': .1}
        self.assertEqual(self.choose('cursed_chest', ('interact', 'skip'), 'skip', hp=.006, **poison, **recovery), 'skip')


if __name__ == '__main__':
    unittest.main()
