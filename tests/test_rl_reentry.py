"""Optional CPU tests for the isolated recovery-floor intervention."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import numpy as np
    import torch
    import benchmark_rl_reentry as study


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch evaluation dependencies are not installed')
class ReentryExperimentTests(unittest.TestCase):
    observations = ['phase.recovery', 'room_div100', 'hp', 'hours_left_div168']
    actions = ['reenter', 'heal_step', 'heal_full', 'restart',
               'wait_hp_20', 'wait_hp_40', 'wait_hp_60', 'wait_hp_80', 'wait_hp_100',
               'wait_boss_safe', 'wait_deadline']

    def floor(self, threshold=40):
        return study.ReentryFloor(self.observations, self.actions, threshold)

    def inputs(self, hp=.2, room=10, selected='reenter', legal=None):
        obs = np.array([[1., room / 100, hp, 1.]], dtype=np.float32)
        masks = np.zeros((1, len(self.actions)), dtype=bool)
        for name in legal or ['reenter', 'wait_hp_40', 'wait_hp_60', 'wait_hp_80', 'wait_hp_100']:
            masks[0, self.actions.index(name)] = True
        return obs, masks, np.array([self.actions.index(selected)], dtype=np.int64)

    def row(self, event=0, threshold=40, **changes):
        result = dict(event_index=event, budget=50, threshold=threshold,
                      completions=2, mushrooms=5, recovery_mushrooms=4, reroll_mushrooms=1,
                      actions=10, decisions=10, invalid_actions=0, deaths=1,
                      overrides=1, ordinary_reentries=1, ordinary_reentry_hp_sum=threshold / 100,
                      boss_reentries=1, boss_reentry_hp_sum=.65,
                      imposed_wait_hours=5., natural_wait_hours=10., elapsed_hours=168.,
                      first_completion_hours=45., terminated=True, truncated=False)
        if threshold == 20:
            result.update(overrides=0, imposed_wait_hours=0.)
        result.update(changes)
        return result

    def test_baseline_is_identity_and_does_not_alias_actions(self):
        obs, masks, actions = self.inputs()
        original = actions.copy()
        actual, changed = self.floor(20).apply(obs, masks, actions)
        np.testing.assert_array_equal(actual, original)
        self.assertFalse(changed.any())
        actual[0] = self.actions.index('heal_full')
        np.testing.assert_array_equal(actions, original)

    def test_changes_only_ordinary_reenter_below_floor_without_mutating_inputs(self):
        cases = [(.2, 10, 'reenter', 1, True),
                 (.4, 10, 'reenter', 1, False),
                 (.8, 10, 'reenter', 1, False),
                 (.2, 25, 'reenter', 1, False),
                 (.2, 50, 'reenter', 1, False),
                 (.2, 75, 'reenter', 1, False),
                 (.2, 100, 'reenter', 1, False),
                 (.2, 10, 'reenter', 0, False),
                 (.2, 10, 'heal_step', 1, False),
                 (.2, 10, 'heal_full', 1, False),
                 (.2, 10, 'wait_hp_100', 1, False),
                 (.2, 10, 'restart', 1, False)]
        obs = np.array([[recovery, room / 100, hp, 1.] for hp, room, _, recovery, _ in cases], np.float32)
        masks = np.ones((len(cases), len(self.actions)), bool)
        actions = np.array([self.actions.index(action) for _, _, action, _, _ in cases])
        before = (obs.copy(), masks.copy(), actions.copy())
        actual, changed = self.floor().apply(obs, masks, actions)
        np.testing.assert_array_equal(changed, [case[-1] for case in cases])
        self.assertEqual(actual[0], self.actions.index('wait_hp_40'))
        np.testing.assert_array_equal(actual[1:], actions[1:])
        for value, original in zip((obs, masks, actions), before):
            np.testing.assert_array_equal(value, original)

    def test_regular_wait_targets_floor_and_deadline_uses_legal_equivalent(self):
        obs, masks, actions = self.inputs()
        actual, changed = self.floor(80).apply(obs, masks, actions)
        self.assertEqual(actual[0], self.actions.index('wait_hp_80'))
        self.assertTrue(changed[0])
        # Native masks keep only the earliest menu action when all longer waits
        # are clipped to the same remaining event duration.
        obs, masks, actions = self.inputs(legal=['reenter', 'wait_hp_40'])
        obs[0, 3] = .1 / 168
        actual, changed = self.floor(100).apply(obs, masks, actions)
        self.assertEqual(actual[0], self.actions.index('wait_hp_40'))
        self.assertTrue(masks[0, actual[0]])
        self.assertTrue(changed[0])

    def test_missing_legal_wait_fails_instead_of_silently_ignoring_intervention(self):
        with self.assertRaisesRegex(RuntimeError, 'No legal wait'):
            self.floor(80).apply(*self.inputs(legal=['reenter']))

    def test_bad_schema_or_unsupported_floor_is_rejected(self):
        for threshold in (0, 30, 101):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                self.floor(threshold)
        for observations, actions in ((self.observations + ['hp'], self.actions),
                                      (self.observations, self.actions + ['reenter']),
                                      (self.observations[1:], self.actions),
                                      (self.observations, self.actions[:-1])):
            with self.assertRaises(ValueError):
                study.ReentryFloor(observations, actions, 40)

    def test_complete_rows_accept_and_wrong_or_duplicate_cohorts_fail(self):
        rows = [self.row(0), self.row(1)]
        study.validate_rows(rows, 2, 50, 40)
        for changed in ([rows[0]], [rows[0], rows[0]], [rows[0], self.row(2)]):
            with self.assertRaisesRegex(RuntimeError, 'indexed cohort'):
                study.validate_rows(changed, 2, 50, 40)
        for changes in (dict(budget=20), dict(threshold=80), dict(terminated=False),
                        dict(truncated=True), dict(invalid_actions=1), dict(elapsed_hours=167.),
                        dict(decisions=11), dict(mushrooms=6), dict(mushrooms=51, recovery_mushrooms=50)):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                study.validate_rows([self.row(**changes)], 1, 50, 40)

    def test_counter_wait_floor_and_identity_accounting_are_checked(self):
        for changes in (dict(completions=True), dict(completions=.5), dict(deaths=-1),
                        dict(actions=float('nan')), dict(overrides=float('inf')),
                        dict(imposed_wait_hours=11.), dict(natural_wait_hours=169.),
                        dict(ordinary_reentry_hp_sum=.3), dict(ordinary_reentry_hp_sum=1.1)):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                study.validate_rows([self.row(**changes)], 1, 50, 40)
        study.validate_rows([self.row(threshold=20)], 1, 50, 20)
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            study.validate_rows([self.row(threshold=20, overrides=1)], 1, 50, 20)

    def test_pairs_match_indices_not_row_order_and_preserve_difference_sign(self):
        control = [self.row(0, completions=2), self.row(1, completions=3)]
        treatment = [self.row(1, completions=1), self.row(0, completions=3)]
        result = study.paired(control, treatment)
        self.assertEqual(result['mean'], -.5)
        self.assertEqual((result['events'], result['wins'], result['ties'], result['losses']), (2, 1, 0, 1))
        self.assertAlmostEqual(result['standard_error'], 1.5)
        for changed in ([treatment[0]], [treatment[0], treatment[0]],
                        [treatment[0], self.row(0, budget=100)]):
            with self.assertRaises(ValueError):
                study.paired(control, changed)

    def test_summary_weights_reentry_health_by_entries_not_by_events(self):
        rows = [self.row(0), self.row(1, ordinary_reentries=3, ordinary_reentry_hp_sum=2.1)]
        summary = study.summarize(rows)
        self.assertAlmostEqual(summary['ordinary_mean_reentry_hp'], .625)
        self.assertEqual(summary['mean_ordinary_reentries'], 2)
        for row in rows:
            row.update(boss_reentries=0, boss_reentry_hp_sum=0)
        self.assertIsNone(study.summarize(rows)['boss_mean_reentry_hp'])

    def test_evaluation_calls_frozen_model_with_original_masks_and_counts_override(self):
        observation_names, action_names = self.observations, self.actions

        class TinyEnv:
            def __init__(self, *args, **kwargs):
                self.n_envs = 2
                self.observation_names, self.action_names = observation_names, action_names
                self.metadata = {}
                self.episode_indices = np.arange(2)
                self._episode_info = {'elapsed_hours': np.zeros(2)}
                self.steps = 0

            def reset(self):
                obs = np.array([[1., .1, .2, 1.]] * 2, np.float32)
                masks = np.zeros((2, len(action_names)), bool)
                masks[:, [action_names.index('reenter'), action_names.index('wait_hp_40')]] = True
                return obs, masks

            def step(self, actions):
                self.steps += 1
                expected = 'wait_hp_40' if self.steps == 1 else 'reenter'
                np.testing.assert_array_equal(actions, [action_names.index(expected)] * 2)
                finished = self.steps == 2
                elapsed = 168. if finished else 4.8
                self._episode_info = {'elapsed_hours': np.full(2, elapsed)}
                info = dict(episode_index=np.arange(2), elapsed_hours=np.full(2, elapsed),
                            completions=np.full(2, 2 if finished else 0), mushrooms=np.zeros(2),
                            recovery_mushrooms=np.zeros(2), reroll_mushrooms=np.zeros(2),
                            invalid_actions=np.zeros(2), actions=np.full(2, self.steps),
                            deaths=np.zeros(2), first_completion_hours=np.full(2, 45.))
                obs, masks = self.reset()
                obs[:, 2] = .4
                return obs, masks, np.full(2, 2 if finished else 0), np.full(2, finished), np.zeros(2, bool), info

            def reset_done(self, indices):
                return self.reset()

            def close(self):
                pass

        class FrozenChoice:
            def __init__(self):
                self.seen_masks = []

            def actions(self, obs, masks, greedy):
                self.seen_masks.append(masks.clone())
                return (torch.full((2,), action_names.index('reenter'), dtype=torch.int64),)

        model = FrozenChoice()
        args = SimpleNamespace(library='fixture', profile='fixture', n_envs=2, events=2, seed=1, namespace=214)
        saved = {'metadata': {'environment_signature': {}}}
        with patch.object(study, 'VectorEnv', TinyEnv), patch.object(study.train_rl, 'environment_changes', return_value={}):
            result = study.evaluate(args, saved, model, torch.device('cpu'), 50, 40)
        self.assertEqual(len(model.seen_masks), 2)
        self.assertTrue(all(bool(mask[:, action_names.index('reenter')].all()) for mask in model.seen_masks))
        self.assertEqual(result['override_wait_actions'], {'wait_hp_40': 2})
        for row in result['outcomes']:
            self.assertEqual(row['overrides'], 1)
            self.assertEqual(row['ordinary_reentries'], 1)
            self.assertAlmostEqual(row['ordinary_reentry_hp_sum'], .4)
            self.assertAlmostEqual(row['imposed_wait_hours'], 4.8)
            self.assertAlmostEqual(row['natural_wait_hours'], 4.8)


if __name__ == '__main__':
    unittest.main()
