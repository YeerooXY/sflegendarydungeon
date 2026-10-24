"""Optional Torch tests for reward boundaries, masking, PPO and evaluation."""
import importlib.util
import copy
import json
import io
from contextlib import redirect_stderr, redirect_stdout
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import numpy as np
    import torch
    import train_rl
    from sfld_rl.ppo import ActorCritic, PPOConfig, advantages, cloning_update, ppo_update


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch training dependencies are not installed')
class RLTrainingTests(unittest.TestCase):
    def test_effect_sampling_state_cannot_silently_resume_as_ordinary_training(self):
        for flag in ('effect_curriculum', 'effect_exploration'):
            with patch.object(train_rl, 'load_checkpoint', return_value=({'metadata': {flag: True}}, None)):
                with self.assertRaisesRegex(ValueError, 'cannot resume'):
                    train_rl.training(SimpleNamespace(output='unused', resume='dummy'), torch.device('cpu'))

    def test_curriculum_rejects_resume_and_cloning_at_argument_validation(self):
        for extra in (['--resume', 'dummy'], ['--bc-steps', '1']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                train_rl.parse(['--output', 'unused', '--effect-curriculum', *extra])

    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(123)

    def gae(self, reward, next_value, done, truncated=None):
        rewards = np.array(reward, np.float32)[:, None]
        return advantages(rewards, np.zeros_like(rewards), np.array(next_value, np.float32)[:, None],
                          np.array(done, bool)[:, None],
                          np.array(truncated or [False] * len(done), bool)[:, None], 1)[0][:, 0]

    def test_finite_rollout_bootstraps_undiscounted_state_value(self):
        np.testing.assert_allclose(self.gae([0, 2], [0, .5], [False, False]), [2.5, 2.5])

    def test_deadline_stops_credit_before_next_event(self):
        np.testing.assert_allclose(self.gae([1, 2, 99], [0, 1000, 0], [False, True, True]), [3, 2, 99])

    def test_death_and_completed_run_keep_episode_credit_flowing(self):
        np.testing.assert_allclose(self.gae([0, 1, 0, 1], [0, 0, 0, 0],
                                            [False, False, False, True]), [2, 2, 1, 1])

    def test_truncation_bootstraps_but_never_links_to_reset_event(self):
        np.testing.assert_allclose(self.gae([0, 99], [3, 0], [False, True], [True, False]), [3, 99])

    def test_training_rejects_shaping_invalid_actions_and_safety_caps(self):
        info = {'completions': np.array([1]), 'invalid_actions': np.array([0]),
                'elapsed_hours': np.array([10.])}
        train_rl.transition_guard([1], [False], [False], info, np.array([0]))
        with self.assertRaisesRegex(RuntimeError, 'without shaping'):
            train_rl.transition_guard([2], [False], [False], info, np.array([0]))
        with self.assertRaisesRegex(RuntimeError, 'not a deadline terminal'):
            train_rl.transition_guard([1], [False], [True], info, np.array([0]))
        with self.assertRaisesRegex(RuntimeError, 'invalid actions'):
            train_rl.transition_guard([1], [False], [False], {**info, 'invalid_actions': [1]}, np.array([0]))

    def test_masks_override_extreme_illegal_logits(self):
        model = ActorCritic(4, 3, (8,))
        with torch.no_grad():
            model.actor.bias[:] = torch.tensor([1000., 999., -1000.])
        obs = torch.zeros(128, 4)
        masks = torch.tensor([[False, False, True]]).expand(128, -1)
        self.assertTrue(bool((model.actions(obs, masks)[0] == 2).all()))
        self.assertTrue(bool((model.actions(obs, masks, greedy=True)[0] == 2).all()))
        with self.assertRaisesRegex(ValueError, 'legal action mask'):
            model.actions(obs, torch.zeros_like(masks))

    def test_ppo_improves_rewarded_action_probability(self):
        model = ActorCritic(4, 2, (8,))
        obs = torch.zeros(64, 4)
        masks = torch.ones(64, 2, dtype=torch.bool)
        actions = torch.tensor([0, 1] * 32)
        with torch.no_grad():
            dist, _ = model.distribution(obs, masks)
            before = float(dist.probs[0, 1])
            logs = dist.log_prob(actions).numpy()
        batch = {'observations': obs.numpy(), 'masks': masks.numpy(), 'actions': actions.numpy(),
                 'log_probs': logs, 'advantages': (actions.float() * 2 - 1).numpy(),
                 'returns': actions.float().numpy()}
        config = PPOConfig(learning_rate=.03, epochs=4, minibatch_size=64, target_kl=0)
        result = ppo_update(model, torch.optim.Adam(model.parameters(), lr=.03), batch, config, 'cpu')
        after = float(model.distribution(obs, masks)[0].probs[0, 1].detach())
        self.assertGreater(after, before + .02)
        self.assertEqual(result['gradient_updates'], 4)
        self.assertTrue(all(math.isfinite(result[k]) for k in ('policy_loss', 'value_loss', 'entropy')))

    def test_cloning_rejects_unavailable_teacher_and_learns_legal_labels(self):
        model = ActorCritic(4, 2, (8,))
        obs = np.zeros((32, 4), np.float32)
        masks = np.ones((32, 2), bool)
        optimizer = torch.optim.Adam(model.parameters(), lr=.05)
        with self.assertRaisesRegex(ValueError, 'unavailable action'):
            cloning_update(model, optimizer, obs, masks, np.full(32, -1), 'cpu')
        illegal = masks.copy(); illegal[:, 1] = False
        with self.assertRaisesRegex(ValueError, 'illegal action'):
            cloning_update(model, optimizer, obs, illegal, np.ones(32), 'cpu')
        before = float(model.distribution(torch.from_numpy(obs), torch.from_numpy(masks))[0].probs[0, 1].detach())
        cloning_update(model, optimizer, obs, masks, np.ones(32), 'cpu')
        after = float(model.distribution(torch.from_numpy(obs), torch.from_numpy(masks))[0].probs[0, 1].detach())
        self.assertGreater(after, before)

    def test_checkpoint_roundtrip_and_no_overwrite(self):
        model = ActorCritic(4, 2, (8,))
        optimizer = torch.optim.Adam(model.parameters())
        with tempfile.TemporaryDirectory(prefix='sfld-ppo-test-') as directory:
            path = Path(directory) / 'model.pt'
            train_rl.checkpoint(path, model, optimizer, {'next_episode_index': 18}, {'fixture': True}, PPOConfig())
            saved, restored = train_rl.load_checkpoint(path, torch.device('cpu'))
            self.assertEqual(saved['state']['next_episode_index'], 18)
            for key, value in model.state_dict().items():
                torch.testing.assert_close(value, restored.state_dict()[key], rtol=0, atol=0)
            with self.assertRaises(FileExistsError):
                train_rl.checkpoint(path, model, optimizer, {}, {}, PPOConfig())

    def test_resume_rejects_changed_profile_horizon_safety_or_selection(self):
        original = {'schema_sha256': 'schema', 'library_sha256': 'library',
                    'profiles': [{'path': 'a', 'sha256': 'first'}, {'path': 'b', 'sha256': 'second'}],
                    'deadline_hours': 168., 'max_actions': 100000}
        signature = train_rl.compact_metadata(original)
        self.assertEqual(train_rl.environment_changes(signature, original, resume=True), {})
        mutations = [('profile', lambda value: value['profiles'][0].update(sha256='changed')),
                     ('profile_order', lambda value: value['profiles'].reverse()),
                     ('horizon', lambda value: value.update(deadline_hours=24.)),
                     ('safety_cap', lambda value: value.update(max_actions=100)),
                     ('library', lambda value: value.update(library_sha256='changed'))]
        for label, mutate in mutations:
            changed = copy.deepcopy(original); mutate(changed)
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'Resume environment'):
                train_rl.environment_changes(signature, changed, resume=True)
        settings = dict(seed=42, budgets=[0, 50], n_envs=8, rollout_steps=64,
                        validation_events=128, eval_action='greedy')
        metadata = {**settings, 'seed': '42'}
        train_rl.validate_resume_settings(SimpleNamespace(**settings), metadata)
        for key, value in [('validation_events', 512), ('eval_action', 'sample')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                train_rl.validate_resume_settings(SimpleNamespace(**{**settings, key: value}), metadata)

    def test_evaluation_reports_alternate_profile_but_rejects_changed_binary(self):
        original = {'schema_sha256': 'schema', 'library_sha256': 'binary',
                    'profiles': [{'sha256': 'training'}], 'deadline_hours': 168., 'max_actions': 100000}
        signature = train_rl.compact_metadata(original)
        changed = {**original, 'profiles': [{'sha256': 'sensitivity'}]}
        self.assertEqual(train_rl.environment_changes(signature, changed),
                         {'profile_sha256': {'training': ['training'], 'current': ['sensitivity']}})
        with self.assertRaisesRegex(ValueError, 'library_sha256'):
            train_rl.environment_changes(signature, {**original, 'library_sha256': 'changed'})

    def parent_checkpoint(self, directory):
        model = ActorCritic(4, 2, (8,))
        optimizer = torch.optim.Adam(model.parameters(), lr=.003, eps=1e-5)
        # Real moments and a nonzero Adam step are required to test transfer.
        logits, values = model(torch.ones(3, 4))
        (logits.square().sum() + values.square().sum()).backward(); optimizer.step()
        environment = {'schema_sha256': 'schema', 'library_sha256': 'library',
                       'profiles': [{'sha256': 'profile'}], 'deadline_hours': 168., 'max_actions': 100000}
        metadata = {'seed': '123', 'budgets': [0], 'n_envs': 2, 'rollout_steps': 1,
                    'validation_events': 2, 'eval_action': 'greedy', 'environment': environment,
                    'environment_signature': train_rl.compact_metadata(environment),
                    'sources': {'tools/train_rl.py': 'deliberately old trainer version',
                                'src/engine.cpp': train_rl.sha(train_rl.ROOT / 'src/engine.cpp')},
                    'finetune': {'ancestor_root_seeds': ['122'], 'cumulative_parent_counts': {'steps': 50,
                                                                                           'completed_training_events': 2}}}
        state = {'steps': 100, 'updates': 10, 'bc_steps': 20, 'bc_completed_events': 1,
                 'completed_training_events': 4, 'next_episode_index': 9999, 'best_score': 99.}
        path = Path(directory) / 'parent.pt'
        train_rl.checkpoint(path, model, optimizer, state, metadata, PPOConfig(learning_rate=.003))
        args = train_rl.parse(['--mode', 'train', '--output', str(Path(directory) / 'child'),
                              '--finetune-from', str(path), '--seed', '456', '--n-envs', '2',
                              '--rollout-steps', '1', '--total-steps', '2', '--validation-events', '2',
                              '--minibatch-size', '2', '--epochs', '1', '--learning-rate', '.00001', '--device', 'cpu'])
        return path, model, optimizer, metadata, args

    def test_finetune_preserves_moments_applies_actual_lr_and_leaves_parent_immutable(self):
        with tempfile.TemporaryDirectory(prefix='sfld-finetune-test-') as directory:
            path, parent, optimizer, metadata, args = self.parent_checkpoint(directory)
            before = path.read_bytes()
            config = PPOConfig(learning_rate=args.learning_rate)
            model, transferred, provenance = train_rl.initialize_finetune(
                args, SimpleNamespace(metadata=metadata['environment']), config, torch.device('cpu'))
            self.assertEqual(model.architecture, parent.architecture)
            self.assertEqual([g['lr'] for g in transferred.param_groups], [.00001])
            for left, right in zip(optimizer.state_dict()['state'].values(), transferred.state_dict()['state'].values()):
                for key in ('step', 'exp_avg', 'exp_avg_sq'):
                    torch.testing.assert_close(left[key], right[key], rtol=0, atol=0)
            for key, value in parent.state_dict().items():
                torch.testing.assert_close(value, model.state_dict()[key], rtol=0, atol=0)
            fresh_rng = torch.get_rng_state().clone(); torch.manual_seed(456)
            torch.testing.assert_close(fresh_rng, torch.get_rng_state(), rtol=0, atol=0)
            self.assertEqual(provenance['cumulative_parent_counts']['steps'], 150)
            self.assertEqual(provenance['cumulative_parent_counts']['completed_training_events'], 6)
            self.assertEqual(provenance['changed_ppo_hyperparameters']['learning_rate'], {'parent': .003, 'new': .00001})
            self.assertEqual(path.read_bytes(), before)

    def test_finetune_rejects_changed_environment_dependency_or_reused_parent_seed(self):
        with tempfile.TemporaryDirectory(prefix='sfld-finetune-test-') as directory:
            path, _, _, metadata, args = self.parent_checkpoint(directory)
            env = SimpleNamespace(metadata=copy.deepcopy(metadata['environment']))
            env.metadata['profiles'][0]['sha256'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'environment'):
                train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))
            env.metadata = metadata['environment']
            args.seed = 123
            with self.assertRaisesRegex(ValueError, 'fresh root-seed'):
                train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))
            args.seed = 122
            with self.assertRaisesRegex(ValueError, 'fresh root-seed'):
                train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))
            args.seed = 456
            actual_sha = train_rl.sha
            with patch.object(train_rl, 'sha', side_effect=lambda p: 'changed' if Path(p).name == 'engine.cpp' else actual_sha(p)):
                with self.assertRaisesRegex(ValueError, 'dependency source changed'):
                    train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))

    def test_environment_transfer_allows_new_build_or_profile_only_with_identical_schema(self):
        with tempfile.TemporaryDirectory(prefix='sfld-finetune-test-') as directory:
            path, _, _, metadata, args = self.parent_checkpoint(directory)
            schema = {'schema': 1, 'engine': '0.6.11', 'observation_names': ['a'], 'action_names': ['x']}
            metadata['environment']['schema'] = schema
            metadata['environment_signature'] = train_rl.compact_metadata(metadata['environment'])
            train_rl.checkpoint(Path(directory)/'p2.pt', *train_rl.load_checkpoint(path, 'cpu')[1:2],
                                torch.optim.Adam(train_rl.load_checkpoint(path, 'cpu')[1].parameters()), {}, metadata, PPOConfig())
            args.finetune_from = Path(directory)/'p2.pt';args.transfer_environment = True
            env = SimpleNamespace(metadata=copy.deepcopy(metadata['environment']))
            env.metadata.update(library_sha256='new', schema={**schema, 'engine': '0.6.13'}, schema_sha256='new')
            env.metadata['profiles'][0]['sha256'] = 'v6'
            actual_sha = train_rl.sha
            with patch.object(train_rl, 'sha', side_effect=lambda p: 'changed' if Path(p).name == 'engine.cpp' else actual_sha(p)):
                _, _, provenance = train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))
            moved = provenance['environment_transfer']
            self.assertEqual(set(moved['changed']), {'library_sha256', 'profile_sha256', 'schema_sha256', 'engine'})
            self.assertIn('src/engine.cpp', moved['changed_sources'])
            env.metadata['schema'] = {**schema, 'engine': '0.6.13', 'observation_names': ['a', 'b']}
            with self.assertRaisesRegex(ValueError, 'observation_names'):
                train_rl.initialize_finetune(args, env, PPOConfig(), torch.device('cpu'))
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            train_rl.parse(['--mode', 'train', '--output', 'x', '--transfer-environment'])

    def test_finetune_starts_new_counts_and_revalidates_step_zero(self):
        with tempfile.TemporaryDirectory(prefix='sfld-finetune-test-') as directory:
            path, _, _, metadata, args = self.parent_checkpoint(directory)
            parent_sha = train_rl.sha(path)
            class Env:
                next_episode_index = 2
                closed = False
                def __init__(self): self.metadata = metadata['environment']
                def reset(self): return np.zeros((2, 4), np.float32), np.ones((2, 2), bool)
                def step(self, actions):
                    return (*self.reset(), np.ones(2), np.ones(2, bool), np.zeros(2, bool),
                            {'completions': np.ones(2), 'invalid_actions': np.zeros(2), 'elapsed_hours': np.full(2, 168.)})
                def reset_done(self, indices): self.next_episode_index += len(indices); return self.reset()
                def close(self): self.closed = True
            env = Env()
            fresh_metadata = {**metadata, 'seed': '456'}; fresh_metadata.pop('finetune')
            with patch.object(train_rl, 'make_env', return_value=env) as make, \
                 patch.object(train_rl, 'source_metadata', return_value=fresh_metadata), \
                 patch.object(train_rl, 'evaluate_model', side_effect=[{'selection_score': 1.25, 'summaries': []},
                                                                     {'selection_score': 1.5, 'summaries': []}]) as evaluate, \
                 redirect_stdout(io.StringIO()):
                train_rl.training(args, torch.device('cpu'))
            make.assert_called_once_with(args, initial_index=0)
            self.assertEqual(evaluate.call_count, 2)
            self.assertTrue(all(call.kwargs['namespace'] == 'validation' for call in evaluate.call_args_list))
            initial, _ = train_rl.load_checkpoint(Path(args.output) / 'checkpoint-000000000000.pt', torch.device('cpu'))
            self.assertEqual(initial['state']['steps'], 0)
            self.assertEqual(initial['state']['completed_training_events'], 0)
            self.assertEqual(initial['state']['bc_steps'], 0)
            self.assertEqual(initial['state']['best_score'], 1.25)
            self.assertEqual(initial['state']['next_episode_index'], 2)
            self.assertEqual(initial['optimizer']['param_groups'][0]['lr'], .00001)
            summary = json.loads((Path(args.output) / 'training-summary.json').read_text())
            self.assertEqual(summary['state']['steps'], 2)
            self.assertEqual(summary['state']['completed_training_events'], 2)
            self.assertEqual(summary['state']['next_episode_index'], 4)
            self.assertEqual(parent_sha, train_rl.sha(path))
            self.assertTrue(env.closed)

    def test_finetune_cli_forbids_resume_and_cloning(self):
        base = ['--mode', 'train', '--output', 'unused', '--finetune-from', 'parent.pt']
        for extra in (['--resume', 'other.pt'], ['--bc-steps', '10']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                train_rl.parse(base + extra)

    def test_evaluation_indexes_exact_events_and_preserves_training_rng(self):
        class FakeEnv:
            def __init__(self, n, budget):
                self.n = n; self.budget = budget; self.next_episode_index = 0
                self.metadata = {'schema_sha256': 'fixture'}
                self.episode_indices = np.zeros(n, np.int64)
                self.episode_budgets = np.full(n, budget)
                self.ticks = np.zeros(n, np.int64)
                self.closed = False

            def observe(self):
                return np.zeros((self.n, 4), np.float32), np.ones((self.n, 2), bool)

            def reset(self):
                return self.reset_done(np.arange(self.n))

            def reset_done(self, slots):
                for slot in slots:
                    self.episode_indices[slot] = self.next_episode_index
                    self.next_episode_index += 1
                    self.ticks[slot] = 0
                return self.observe()

            def step(self, actions):
                self.ticks += 1
                done = self.ticks >= self.episode_indices % 3 + 1
                completions = self.episode_indices % 3 + 1
                info = {'episode_index': self.episode_indices.copy(), 'completions': completions,
                        'mushrooms': np.zeros(self.n), 'recovery_mushrooms': np.zeros(self.n),
                        'reroll_mushrooms': np.zeros(self.n), 'invalid_actions': np.zeros(self.n),
                        'elapsed_hours': np.where(done, 168., 1.), 'actions': self.ticks.copy()}
                obs, masks = self.observe()
                return obs, masks, np.zeros(self.n), done, np.zeros(self.n, bool), info

            def close(self):
                self.closed = True

        args = SimpleNamespace(profiles=['fixture.profile'], budgets=[0, 50], seed=42,
                               n_envs=3, eval_action='greedy')
        envs = []

        def make(args, **kwargs):
            self.assertEqual(kwargs['namespace'], 'validation')
            env = FakeEnv(kwargs['n_envs'], kwargs['budgets'][0]); envs.append(env)
            return env

        model = ActorCritic(4, 2, (8,))
        rng = torch.get_rng_state().clone()
        with patch.object(train_rl, 'make_env', side_effect=make):
            result = train_rl.evaluate_model(model, args, torch.device('cpu'), namespace='validation', events=5)
        torch.testing.assert_close(torch.get_rng_state(), rng, rtol=0, atol=0)
        self.assertTrue(all(env.closed for env in envs))
        self.assertEqual(len(result['outcomes']), 10)
        for budget in args.budgets:
            rows = [r for r in result['outcomes'] if r['budget'] == budget]
            self.assertEqual([r['event_index'] for r in rows], list(range(5)))
            self.assertEqual([r['completions'] for r in rows], [1, 2, 3, 1, 2])
        self.assertEqual(result['selection_score'], 1.8)


if __name__ == '__main__':
    unittest.main()
