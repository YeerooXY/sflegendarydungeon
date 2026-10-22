"""CPU checks for the isolated frozen-policy stone-exclusion experiment."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import numpy as np
    import benchmark_stone_exclusion as study


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are not installed')
class StoneExclusionStudyTests(unittest.TestCase):
    def test_profiles_change_only_mechanic_without_renaming(self):
        old = 'schema=1\nid=original\nmonster=17\n'
        new = old + '# explanation\nexclude_offered_gems=true\n'
        profiles = study.arm_profiles(new, old)
        left, right = map(study.settings, (profiles['held_only'], profiles['all_offered']))
        self.assertEqual(left.pop('exclude_offered_gems'), 'false')
        self.assertEqual(right.pop('exclude_offered_gems'), 'true')
        self.assertEqual(left, right)
        self.assertEqual(left['id'], 'original')
        with self.assertRaisesRegex(ValueError, 'beyond stone exclusion'):
            study.arm_profiles(new.replace('monster=17', 'monster=18'), old)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            study.arm_profiles(new + 'id=renamed\n', old)

    def metadata(self):
        return {'library_sha256': 'old', 'profiles': [{'path': 'old.profile', 'sha256': 'profile-old'}],
                'schema': {'engine': '0.6.5', 'observation_names': ['hp'], 'action_names': ['a', 'b'], 'reward': 'completion_increment'},
                'schema_sha256': 'schema-old', 'observation_dim': 1, 'action_dim': 2,
                'deadline_hours': 168, 'max_actions': 100000}

    def test_interface_is_fixed_while_engine_and_profile_differences_are_explicit(self):
        original = self.metadata()
        saved = {'architecture': {'observation_size': 1, 'action_size': 2},
                 'metadata': {'environment_signature': study.train_rl.compact_metadata(original)}}
        current = copy.deepcopy(original)
        current.update(library_sha256='new', schema_sha256='schema-new')
        current['schema']['engine'] = '0.6.6'
        current['profiles'][0]['sha256'] = 'profile-new'
        result = study.compatibility(saved, current)
        self.assertTrue(result['identical_neural_interface'])
        self.assertFalse(result['history_features_added'])
        for key, value in [('deadline_hours', 24), ('max_actions', 90000), ('observation_dim', 2)]:
            changed = copy.deepcopy(current); changed[key] = value
            with self.assertRaises(ValueError):
                study.compatibility(saved, changed)
        current['schema']['observation_names'].append('rejected_stone_history')
        with self.assertRaisesRegex(ValueError, 'schema changed'):
            study.compatibility(saved, current)

    def row(self, i=0, **changes):
        result = dict(event_index=i, budget=20, completions=2, mushrooms=10,
                      recovery_mushrooms=9, reroll_mushrooms=1, actions=100, decisions=100,
                      invalid_actions=0, deaths=5, elapsed_hours=167.999,
                      first_completion_hours=62., terminated=True, truncated=False,
                      stone_runs=[{'run_number': 1, 'run_class': 'first', 'menus': [
                          {'selection': 1, 'offered': ['a', 'b', 'c'], 'chosen': 'a'},
                          {'selection': 2, 'offered': ['d', 'e', 'f'], 'chosen': 'd'},
                          {'selection': 3, 'offered': ['g', 'h', 'i'], 'chosen': None}]}])
        result.update(changes)
        return result

    def test_terminal_near_deadline_allowed_but_accounting_failures_rejected(self):
        rows = [self.row(0), self.row(1)]
        study.validate_rows(rows, 2, 20, 'all_offered')
        for key, value in [('elapsed_hours', float('nan')), ('elapsed_hours', 169), ('deaths', -1),
                           ('actions', 100.5), ('decisions', 101), ('completions', True),
                           ('mushrooms', 21), ('recovery_mushrooms', 11), ('truncated', True),
                           ('invalid_actions', 1), ('first_completion_hours', float('inf'))]:
            modified = copy.deepcopy(rows); modified[1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                study.validate_rows(modified, 2, 20, 'all_offered')
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            study.validate_rows([self.row(0), self.row(0)], 2, 20, 'all_offered')

    def test_telemetry_recycling_distinguishes_arms_and_held_stones_never_return(self):
        row = self.row()
        row['stone_runs'][0]['menus'][1]['offered'][2] = 'c'
        study.validate_rows([row], 1, 20, 'held_only')
        with self.assertRaisesRegex(ValueError, 'recycled'):
            study.validate_rows([row], 1, 20, 'all_offered')
        row['stone_runs'][0]['menus'][1]['offered'][2] = 'a'
        with self.assertRaisesRegex(ValueError, 'offer/choice'):
            study.validate_rows([row], 1, 20, 'held_only')

    def test_observation_telemetry_deduplicates_heal_revisit_and_uses_actual_class(self):
        names = ['run_number_div10', 'first_event_run', 'phase.gems', 'owned_gem.a', 'owned_gem.b']
        names += [f'gem_offers.{i}.{gem}' for i in range(3) for gem in ('a', 'b', 'c')]
        collector = study.OfferTelemetry(names, ['heal_full', 'gem_1'], 1)
        obs = np.zeros((1, len(names)), np.float32)
        obs[0, :3] = [.1, 1, 1]
        for i, gem in enumerate(('a', 'b', 'c')):
            obs[0, names.index(f'gem_offers.{i}.{gem}')] = 1
        collector.record(obs, np.array([0]), np.array([True]))
        collector.record(obs, np.array([1]), np.array([True]))
        run = collector.runs[0][0]
        self.assertEqual(len(run['menus']), 1)
        self.assertEqual(run['menus'][0]['chosen'], 'b')
        self.assertEqual(run['run_class'], 'first')
        # Classification comes from the explicit observation, not the number.
        collector.reset([0]); obs[0, 1] = 0
        collector.record(obs, np.array([0]), np.array([True]))
        self.assertEqual(collector.runs[0][0]['run_class'], 'later')

    def test_pairing_uses_indices_and_reports_both_intervals(self):
        old = [self.row(0, completions=1), self.row(1, completions=3)]
        new = [self.row(1, completions=2), self.row(0, completions=3)]
        result = study.paired(old, new)
        self.assertEqual(result['completions']['mean'], .5)
        self.assertEqual((result['wins'], result['ties'], result['losses']), (1, 0, 1))
        self.assertLess(result['completions']['normal_99_ci'][0], result['completions']['normal_95_ci'][0])
        with self.assertRaisesRegex(ValueError, 'indices differ'):
            study.paired(old, [self.row(2), self.row(0)])

    def test_exact_probabilities_and_new_seed_root(self):
        first, later = study.target_probabilities(11), study.target_probabilities(20)
        self.assertEqual(first['held_only_exact'], '109/165')
        self.assertEqual(first['all_offered_exact'], '9/11')
        self.assertEqual(later['held_only_exact'], '23/57')
        self.assertEqual(later['all_offered_exact'], '9/20')
        self.assertNotIn(study.fingerprint(9601001), {study.fingerprint(s) for s in study.PRIOR_ROOTS})
        self.assertEqual(study.fingerprint(9011001), 96159355)


if __name__ == '__main__':
    unittest.main()
