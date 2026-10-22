"""Strict cached-cohort checks for the continuation-study orchestrator."""
import copy
import importlib.util
from pathlib import Path
import statistics
import sys
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import finetune_rl_study as study


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are not installed')
class FineTuneStudyTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'library_sha256':'binary', 'profile_sha256':'profile', 'n_envs':256}
        profile = str(Path(__file__).resolve().parents[1] / 'profiles/synthetic.profile')
        metadata = []
        rows = []
        summaries = []
        for budget in study.BUDGETS:
            metadata.append({'library_sha256':'binary', 'profiles':[{'path':profile, 'sha256':'profile'}],
                             'schema':{'schema':1}, 'schema_sha256':'schema', 'deadline_hours':168., 'max_actions':100000,
                             'budgets':[budget], 'seed_namespace':0xB2, 'n_envs':3, 'auto_reset':False})
            selected = []
            for index in range(3):
                row = {'profile':profile, 'budget':budget, 'event_index':index,
                       'completions':2 + index % 2, 'mushrooms':budget//2,
                       'recovery_mushrooms':budget//2, 'reroll_mushrooms':0, 'actions':800,
                       'invalid_actions':0, 'elapsed_hours':168., 'terminated':True, 'truncated':False}
                selected.append(row); rows.append(row)
            summaries.append({'profile':profile, 'budget':budget, 'events':3, 'failed_events':0, 'invalid_actions':0,
                              'truncations':0, 'completions':study.train_rl.interval([r['completions'] for r in selected]),
                              'mean_mushrooms':budget//2, 'mean_recovery_mushrooms':budget//2, 'mean_reroll_mushrooms':0})
        self.result = {'root_seed':'9302001', 'namespace':'validation', 'namespace_byte':0xB2,
                       'checkpoint_sha256':'weights', 'library_sha256':'binary', 'profile_sha256':'profile',
                       'action_policy':'greedy', 'outcomes':rows, 'summaries':summaries,
                       'selection_score':statistics.mean(r['completions'] for r in rows), 'environment_metadata':metadata,
                       'checkpoint_training_metadata':{'environment_signature':study.train_rl.compact_metadata(metadata[0])}}

    def validate(self, result=None):
        return study.validate_evaluation(result or self.result, self.plan, 9302001, 'validation', 3, 'weights')

    def rejected(self, mutate):
        changed = copy.deepcopy(self.result); mutate(changed)
        with self.assertRaises(RuntimeError):
            self.validate(changed)

    def test_complete_cohort_and_legitimate_early_deadline_terminal_are_accepted(self):
        self.assertAlmostEqual(self.validate(), 7/3)
        # Actual old final results include true terminals about0.8seconds early:
        # the remaining horizon cannot fit another ordinary two-second action.
        self.result['outcomes'][0]['elapsed_hours'] = 167.99977777777605
        self.assertAlmostEqual(self.validate(), 7/3)

    def test_cache_identity_requires_exact_seed_domain_policy_and_checkpoint(self):
        for key, value in [('root_seed','9302002'), ('namespace','final'), ('namespace_byte',0xC3),
                           ('checkpoint_sha256','other'), ('library_sha256','other'), ('profile_sha256','other'),
                           ('action_policy','sample')]:
            with self.subTest(key=key):
                self.rejected(lambda r: r.update({key:value}))

    def test_missing_duplicate_or_substituted_event_indices_are_rejected(self):
        self.rejected(lambda r: r['outcomes'].pop())
        self.rejected(lambda r: r['outcomes'].__setitem__(1, r['outcomes'][0].copy()))
        self.rejected(lambda r: r['outcomes'][0].update(event_index=99))
        self.rejected(lambda r: r['outcomes'][0].update(budget=75))

    def test_all_counters_must_be_finite_nonnegative_integers(self):
        fields = ('budget','event_index','completions','mushrooms','recovery_mushrooms','reroll_mushrooms','actions','invalid_actions')
        for field in fields:
            for value in (float('nan'), float('inf'), -1, .5, True):
                with self.subTest(field=field,value=value):
                    self.rejected(lambda r: r['outcomes'][0].update({field:value}))

    def test_spending_parts_budget_and_action_cap_are_enforced(self):
        self.rejected(lambda r: r['outcomes'][0].update(mushrooms=1, recovery_mushrooms=1))
        self.rejected(lambda r: r['outcomes'][3].update(recovery_mushrooms=0))
        self.rejected(lambda r: r['outcomes'][0].update(actions=100000))
        self.rejected(lambda r: r['outcomes'][0].update(actions=0))
        self.rejected(lambda r: r['outcomes'][0].update(completions=801))

    def test_bad_terminal_states_and_nonfinite_or_outside_horizon_times_are_rejected(self):
        for field,value in [('terminated',False),('terminated',1),('truncated',True),('truncated',0),('invalid_actions',1)]:
            with self.subTest(field=field,value=value):
                self.rejected(lambda r: r['outcomes'][0].update({field:value}))
        for value in (float('nan'),float('inf'),-1,0,169):
            with self.subTest(time=value):
                self.rejected(lambda r: r['outcomes'][0].update(elapsed_hours=value))

    def test_nested_environment_must_match_plan_and_checkpoint(self):
        for key,value in [('library_sha256','other'), ('deadline_hours',24), ('max_actions',99999),
                          ('schema_sha256','other'), ('seed_namespace',0xC3), ('auto_reset',True), ('n_envs',1)]:
            with self.subTest(key=key):
                self.rejected(lambda r: r['environment_metadata'][0].update({key:value}))
        self.rejected(lambda r: r['environment_metadata'][0]['profiles'][0].update(sha256='other'))
        self.rejected(lambda r: r['environment_metadata'][0].update(budgets=[20]))
        self.rejected(lambda r: r['environment_metadata'].pop())
        self.rejected(lambda r: r.pop('checkpoint_training_metadata'))
        self.rejected(lambda r: r['outcomes'][0].update(profile='other.profile'))

    def test_selection_score_and_published_summaries_are_recomputed(self):
        for value in (float('nan'),float('inf'),2,True):
            with self.subTest(score=value):
                self.rejected(lambda r: r.update(selection_score=value))
        self.rejected(lambda r: r['summaries'][0]['completions'].update(mean=99))
        self.rejected(lambda r: r['summaries'][0]['completions'].update(standard_error=99))
        self.rejected(lambda r: r['summaries'][0]['completions'].update(normal_95_ci=[0,99]))
        self.rejected(lambda r: r['summaries'][0].update(mean_mushrooms=99))
        self.rejected(lambda r: r['summaries'].pop())

    def test_planned_roots_have_distinct_native_seed_fingerprints(self):
        roots = [9301001,9301002,9301003,9301004,9302001,9302002,9303001,
                 9011001,9011002,9011003,9100001,9100002,9200001,9200002,9200003]
        self.assertEqual(len(set(study.fingerprint(seed) for seed in roots)),len(roots))
        for seed in roots:
            self.assertEqual(study.fingerprint(seed), study.train_rl.seed_fingerprint(seed))


if __name__ == '__main__':
    unittest.main()
