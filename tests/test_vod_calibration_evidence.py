import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from vod_calibration_evidence import LOOT_FLAGS, loot_summary, trap_fit


class CalibrationEvidenceTests(unittest.TestCase):
    def test_trap_fit_uses_eligible_side_hazard_not_pair_fraction(self):
        entries = {}
        reviews = []
        for n, positive_count in ((1, 30), (2, 51)):
            for i in range(100):
                ident = f'{n}:{i}'
                entries[ident] = {'offered_kinds': ['mystery', 'wall' if n == 1 else 'monster'],
                                  'context': {'gems': []}}
                reviews.append({'observation_id': ident, 'sides': [i < positive_count, False]})
        result = trap_fit(reviews, entries)
        self.assertAlmostEqual(result['eligible_side_probability_mle'], .30, places=6)
        self.assertAlmostEqual(result['observed_pair_fraction'], .405)
        self.assertLess(result['profile_likelihood_95'][0], .30)
        self.assertGreater(result['profile_likelihood_95'][1], .30)

    def test_unreadable_pairs_are_not_clear(self):
        result = trap_fit([{'observation_id': 'one', 'sides': [None, False]}],
                          {'one': {'offered_kinds': ['mystery', 'wall'], 'context': {'gems': []}}})
        self.assertEqual(result['unresolved_pairs'], 1)
        self.assertEqual(result['resolved_pairs'], 0)
        self.assertIsNone(result['eligible_side_probability_mle'])

    def test_overlapping_loot_remains_joint_outcome(self):
        flags = dict.fromkeys((*LOOT_FLAGS, 'epic_item', 'legendary_item'), False)
        flags.update(gold=True, persistent_blessing=True)
        result = loot_summary([{'reward_flags': flags, 'initial_style': 'wooden_box',
                                'source_id': 'source', 'observation_id': 'one'}])
        self.assertEqual(result['openings'], 1)
        self.assertEqual(result['joint_outcomes'], {'gold+persistent_blessing': 1})
        self.assertEqual(result['flags']['gold']['positive'], 1)
        self.assertEqual(result['flags']['persistent_blessing']['positive'], 1)
        self.assertEqual(result['flags']['nothing']['positive'], 0)


if __name__ == '__main__':
    unittest.main()
