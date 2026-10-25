"""The adviser always opens the free (unlocked) sarcophagus: gold for one action."""
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

    def choose(self, encounter, allowed, proposed):
        obs = np.zeros((1, len(self.names)), np.float32)
        for name in ('phase.encounter', 'encounter.' + encounter):obs[0, self.names.index(name)] = 1
        obs[0, self.names.index('hp')] = .6;obs[0, self.names.index('room_div100')] = .4
        mask = np.zeros((1, len(self.actions)), bool)
        for name in allowed:mask[0, self.actions.index(name)] = True
        return self.actions[int(self.rule.apply(obs, mask, np.array([self.actions.index(proposed)]))[0][0])]

    def test_free_sarcophagus_is_always_opened(self):
        self.assertEqual(self.choose('sarcophagus', ('interact', 'skip'), 'skip'), 'interact')

    def test_locked_sarcophagus_stays_the_policy_choice(self):
        self.assertEqual(self.choose('locked_sarcophagus', ('interact', 'skip'), 'skip'), 'skip')


if __name__ == '__main__':
    unittest.main()
