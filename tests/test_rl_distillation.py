"""Optional extraction tests; no GPU, long training or model downloads."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch', 'sklearn'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import numpy as np
    import torch
    import distill_rl as distill


@unittest.skipUnless(AVAILABLE, 'Optional NumPy, Torch and scikit-learn dependencies are not installed')
class DistillationTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        self.names = ['phase.' + name for name in distill.PHASES] + ['hp', 'keys_div20']
        self.actions = ['fight', 'flee', 'interact', 'skip']

    def observations(self, hp, phase='encounter'):
        x = np.zeros((len(hp), len(self.names)), np.float32)
        x[:, distill.PHASES.index(phase)] = 1
        x[:, -2] = hp
        return x

    def data(self):
        hp = np.tile([.1, .2, .8, .9], 20)
        x = self.observations(hp)
        labels = np.where(hp < .5, 1, 0)
        return {'observations': x, 'actions': labels, 'masks': np.ones((len(x), 4), bool),
                'budgets': np.tile([0, 50], 40), 'event_indices': np.repeat(np.arange(8), 10)}

    def document(self):
        return distill.fit_trees(self.data(), self.names, self.actions, max_depth=2, max_leaves=4, min_leaf=4)

    def test_phase_tree_json_roundtrip_preserves_conditional_actions(self):
        document = self.document()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trees.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            policy = distill.TreePolicy.load(path)
            data = self.data()
            np.testing.assert_array_equal(policy.predict_numpy(data['observations'], data['masks']), data['actions'])
            self.assertEqual(distill.agreement(policy, data)['agreement'], 1)
            actions, logs, values = policy.actions(torch.from_numpy(data['observations']), torch.from_numpy(data['masks']))
            np.testing.assert_array_equal(actions.numpy(), data['actions'])
            self.assertTrue(bool((logs == 0).all() and (values == 0).all()))

    def test_illegal_leaf_top_uses_empirical_legal_alternatives(self):
        document = self.document()
        document['trees']['encounter']['nodes'] = [{'samples': 10, 'action_counts': [6, 3, 1, 0]}]
        policy = distill.TreePolicy(document)
        masks = np.array([[False, True, True, True], [False, False, True, True]], bool)
        np.testing.assert_array_equal(policy.predict_numpy(self.observations([.4, .4]), masks), [1, 2])
        self.assertEqual(policy.diagnostics['masked_top_action'], 2)
        self.assertEqual(policy.diagnostics['phase_fallback'], 0)

    def test_zero_legal_leaf_mass_uses_phase_then_global_then_first_legal(self):
        document = self.document()
        document['trees']['encounter']['nodes'] = [{'samples': 10, 'action_counts': [10, 0, 0, 0]}]
        document['trees']['encounter']['action_counts'] = [10, 4, 0, 0]
        document['global_action_counts'] = [10, 4, 5, 0]
        policy = distill.TreePolicy(document)
        masks = np.array([[False, True, True, True], [False, False, True, True], [False, False, False, True]], bool)
        np.testing.assert_array_equal(policy.predict_numpy(self.observations([.4]*3), masks), [1, 2, 3])
        self.assertEqual(policy.diagnostics['phase_fallback'], 3)
        self.assertEqual(policy.diagnostics['global_fallback'], 2)
        self.assertEqual(policy.diagnostics['unobserved_legal_fallback'], 1)

    def test_missing_phase_and_schema_ties_have_explicit_legal_fallbacks(self):
        document = self.document()
        policy = distill.TreePolicy(document)
        masks = np.array([[True, True, False, False]], bool)
        self.assertEqual(policy.predict_numpy(self.observations([.2], 'gems'), masks)[0], 0)
        with self.assertRaisesRegex(ValueError, 'legal action'):
            policy.predict_numpy(self.observations([.2]), np.zeros((1, 4), bool))
        wrong = self.observations([.2]); wrong[0, 0] = 1
        with self.assertRaisesRegex(ValueError, 'one active phase'):
            policy.predict_numpy(wrong, masks)

    def test_malformed_json_trees_cannot_cycle_or_read_invalid_features(self):
        original = self.document()
        for mutation in (lambda d: d['trees']['encounter']['nodes'][0].update(left=0),
                         lambda d: d['trees']['encounter']['nodes'][0].update(feature=999),
                         lambda d: d['trees']['encounter']['nodes'][0].update(threshold=float('nan')),
                         lambda d: d.update(global_action_counts=[-1, 0, 0, 0])):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError):
                distill.TreePolicy(bad)

    def test_fit_rejects_illegal_labels_and_bounds_tree_complexity(self):
        data = self.data(); data['masks'][:, 1] = False
        with self.assertRaisesRegex(ValueError, 'legal'):
            distill.fit_trees(data, self.names, self.actions)
        for kwargs in ({'max_depth': 7}, {'max_leaves': 33}, {'min_leaf': 0}):
            with self.assertRaises(ValueError):
                distill.fit_trees(self.data(), self.names, self.actions, **kwargs)
        document = self.document()
        self.assertLessEqual(sum('feature' not in n for n in document['trees']['encounter']['nodes']), 4)

    def test_rules_denormalize_health_and_key_thresholds(self):
        self.assertEqual(distill.condition_text('hp', .25, True), 'health <= 25%')
        self.assertEqual(distill.condition_text('keys_div20', .2, False), 'keys > 4')
        self.assertEqual(distill.condition_text('owned_gem.bull', .5, True), 'owned_gem.bull is false')
        rules = distill.readable_rules(self.document())
        self.assertIn('health', rules); self.assertIn('highest-frequency LEGAL', rules)
        self.assertIn('not success odds', rules)

    def test_balanced_quotas_and_seed_domains_keep_validation_events_separate(self):
        quotas = distill.balanced_quotas(262144, [0,20,50,100,500])
        self.assertEqual(sum(quotas), 262144)
        self.assertLessEqual(max(quotas)-min(quotas), 1)
        self.assertEqual(len(set(distill.DOMAINS.values())), 3)
        self.assertTrue(set(distill.DOMAINS.values()).isdisjoint({0xA1,0xA2,0xB2,0xC3}))
        with self.assertRaises(ValueError):
            distill.balanced_quotas(1, [0,50])

    def test_collection_uses_distinct_whole_event_domains_and_balanced_labels(self):
        test = self
        metadata = {'schema_sha256':'fixture', 'library_sha256':'binary',
                    'profiles':[{'sha256':'profile'}], 'deadline_hours':168, 'max_actions':100000}
        seen_namespaces = []
        class FakeEnv:
            def __init__(self, library, profiles, n, **kwargs):
                self.n_envs = n; self.metadata = metadata; self.next_index = 0
                self.episode_indices = np.zeros(n, np.int64); self.ticks = np.zeros(n, np.int64)
                seen_namespaces.append(kwargs['seed_namespace'])
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def observe(self): return test.observations([.5]*self.n_envs), np.ones((self.n_envs,4), bool)
            def reset(self): return self.reset_done(np.arange(self.n_envs))
            def reset_done(self, slots):
                for slot in slots:
                    self.episode_indices[slot] = self.next_index; self.next_index += 1; self.ticks[slot] = 0
                return self.observe()
            def step(self, labels):
                self.ticks += 1; done = self.ticks == 2
                info = {'completions':done.astype(float), 'invalid_actions':np.zeros(self.n_envs),
                        'elapsed_hours':np.where(done,168.,1.)}
                obs, mask = self.observe()
                return obs, mask, done.astype(float), done, np.zeros(self.n_envs,bool), info
        class Actor:
            architecture = {'observation_size':len(test.names), 'action_size':4}
            def actions(self, obs, masks, greedy):
                zeros = torch.zeros(len(obs), dtype=torch.int64)
                return zeros, zeros, zeros
        args = SimpleNamespace(budgets=[0,50], library='unused', profile='unused', n_envs=2, deadline_hours=168, max_actions=100000,
                               min_collection_events=4)
        saved = {'metadata':{'environment_signature':distill.train_rl.compact_metadata(metadata)}}
        with patch.object(distill, 'VectorEnv', FakeEnv):
            fit, fit_info = distill.collect_labels(Actor(), saved, args, torch.device('cpu'), samples=10, seed=1, namespace='distill_fit')
            held, held_info = distill.collect_labels(Actor(), saved, args, torch.device('cpu'), samples=10, seed=1, namespace='distill_agreement')
        self.assertEqual(seen_namespaces, [0xD6,0xD6,0xD8,0xD8])
        self.assertEqual(np.bincount(fit['budgets']//50).tolist(), [5,5])
        self.assertEqual(len(fit['observations']), 10)
        self.assertNotEqual(fit_info['namespace_byte'], held_info['namespace_byte'])
        self.assertGreaterEqual(fit_info['budgets'][0]['completed_collection_events'], 4)
        self.assertGreater(fit_info['budgets'][0]['executed_transitions'], 5)

    def test_reservoir_batch_matches_sequential_replacement_and_keeps_rows_together(self):
        class Random:
            def integers(self, low, high):
                np.testing.assert_array_equal(high, [4,5,6,7])
                return np.array([1,0,1,6])
        storage = {'actions':np.array([10,11,12]), 'labels':np.array([100,110,120])}
        batch = {'actions':np.array([13,14,15,16]), 'labels':np.array([130,140,150,160])}
        seen = distill.reservoir_insert(storage, batch, 3, Random())
        self.assertEqual(seen, 7)
        np.testing.assert_array_equal(storage['actions'], [14,15,12])
        np.testing.assert_array_equal(storage['labels'], [140,150,120])

    def evaluation(self, values):
        rows = [{'profile':'fixture', 'budget':0, 'event_index':i, 'completions':value,
                 'terminated':True, 'truncated':False, 'invalid_actions':0, 'mushrooms':0} for i,value in enumerate(values)]
        return {'root_seed':'9200002','namespace_byte':0xD7,'outcomes':rows,
                'environment_metadata':[{'schema_sha256':'fixture','library_sha256':'binary',
                                         'profiles':[{'sha256':'profile'}], 'deadline_hours':168, 'max_actions':100000}]}

    def test_paired_completion_loss_is_gameplay_outcome_not_label_agreement(self):
        actor, tree = self.evaluation([3,2,4,1]), self.evaluation([2,2,2,2])
        paired = distill.paired_gameplay(actor, tree)[0]
        self.assertEqual(paired['neural_completions_mean'], 2.5)
        self.assertEqual(paired['tree_completions_mean'], 2)
        self.assertEqual(paired['paired_completion_loss']['mean'], .5)
        self.assertGreater(paired['paired_completion_loss']['standard_error'], 0)
        same = distill.paired_gameplay(actor, actor)[0]['paired_completion_loss']
        self.assertEqual(same['mean'], 0); self.assertEqual(same['standard_error'], 0)

    def test_pairing_rejects_mismatched_horizon_seed_missing_or_failed_events(self):
        actor = self.evaluation([3,2,4])
        mutations = (lambda e: e.update(root_seed='elsewhere'),
                     lambda e: e['environment_metadata'][0].update(deadline_hours=24),
                     lambda e: e['outcomes'].pop(),
                     lambda e: e['outcomes'][0].update(truncated=True),
                     lambda e: e['outcomes'].append(e['outcomes'][0]))
        for mutate in mutations:
            changed = copy.deepcopy(actor); mutate(changed)
            with self.assertRaises(ValueError):
                distill.paired_gameplay(actor, changed)
        gap = copy.deepcopy(actor); gap['outcomes'][0]['event_index'] = 10
        with self.assertRaisesRegex(ValueError, 'complete cohort'):
            distill.paired_gameplay(gap, gap)


if __name__ == '__main__':
    unittest.main()
