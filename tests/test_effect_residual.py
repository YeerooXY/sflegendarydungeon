"""Residual policy boundaries, door symmetry and training/serving parity."""
from pathlib import Path
import importlib.util
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
AVAILABLE = importlib.util.find_spec('numpy') is not None
if AVAILABLE:
    import numpy as np
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectLab, position, BLESSINGS
    from sfld_rl.effect_residual import EffectFeatures, EffectResidual, predict_tree, export_tree

LIB = next((p for p in (Path(os.environ.get('SFLD_RL_LIBRARY', '__absent__')),
    ROOT/'build-perf/Release/sfld_rl.dll', ROOT/'build-effects/Release/sfld_rl.dll',
    ROOT/'build/libsfld_rl.so') if p.is_file()), None)


@unittest.skipUnless(AVAILABLE and LIB, 'Optional NumPy/native replay library absent')
class EffectResidualTests(unittest.TestCase):
    def setUp(self):
        self.env = VectorEnv(LIB, [ROOT/'profiles/synthetic.profile'], 1, budgets=[100], auto_reset=False)
        self.env.reset(); self.lab = EffectLab(self.env)
        self.features = EffectFeatures(self.env.observation_names, self.env.action_names)

    def tearDown(self): self.env.close()

    def observe(self, progress):
        with self.lab.fixture(progress, 100) as snapshot: self.lab.restore(0, snapshot, 1337)
        return self.lab.observe()

    def constant(self, value):
        artifact = {'schema': 'sfld-effect-residual-v1', 'features': self.features.feature_names, 'margin': .005,
                    'trees': [{'left': [-1], 'right': [-1], 'feature': [-2], 'threshold': [-2.], 'value': [value]}]}
        return EffectResidual(self.env.observation_names, self.env.action_names, artifact)

    def test_door_orientation_does_not_change_values(self):
        encodings = []
        for doors in ('blessing:trap,monster', 'monster,blessing:trap'):
            obs, masks = self.observe(position(blessings=BLESSINGS['recovery_oldest'], doors=doors))
            family, first, second = self.features.opportunities(obs, masks)
            self.assertEqual(family[0], 2)
            actual, _ = self.constant(-.1).apply(obs, masks, first)
            self.assertEqual(actual[0], second[0])
            encodings.append(self.features.encode(obs, family, first))
        np.testing.assert_array_equal(*encodings)

    def test_early_trap_preference_and_healing_are_preserved(self):
        progress = position(room=10, gems='', blessings=BLESSINGS['recovery_oldest'], doors='blessing:trap,monster')
        obs, masks = self.observe(progress)
        proposed = np.array([self.features.ai['door_1']])
        actual, _ = self.constant(1).apply(obs, masks, proposed)
        np.testing.assert_array_equal(actual, proposed)
        obs, masks = self.observe(position(blessings=BLESSINGS['recovery_oldest'], encounter='crate'))
        heal = np.array([self.features.ai['heal_full']])
        self.assertTrue(masks[0, heal[0]])
        actual, _ = self.constant(-1).apply(obs, masks, heal)
        np.testing.assert_array_equal(actual, heal)

    def test_single_effect_and_shops_remain_parent_decisions(self):
        obs, masks = self.observe(position(blessings='recovery:strong:5', encounter='crate'))
        proposed = np.array([self.features.ai['interact']])
        actual, info = self.constant(-1).apply(obs, masks, proposed)
        self.assertEqual(info['family'][0], -1)
        np.testing.assert_array_equal(actual, proposed)

    def test_confidence_margin_keeps_parent(self):
        obs, masks = self.observe(position(blessings=BLESSINGS['recovery_oldest'], encounter='crate'))
        for name in ('interact', 'skip'):
            proposed = np.array([self.features.ai[name]])
            actual, _ = self.constant(.003).apply(obs, masks, proposed)
            np.testing.assert_array_equal(actual, proposed)

    def test_exported_tree_agrees_off_training_points(self):
        if importlib.util.find_spec('sklearn') is None: self.skipTest('Optional fitting dependency absent')
        from sklearn.tree import DecisionTreeRegressor
        rng = np.random.default_rng(891)
        x = rng.normal(size=(250, 10)).astype(np.float32)
        y = (x[:, 0] > 0) * x[:, 3] + x[:, 9]
        model = DecisionTreeRegressor(max_depth=7, random_state=41).fit(x, y)
        artifact = export_tree(model)
        arrays = tuple(np.asarray(artifact[k]) for k in ('left', 'right', 'feature', 'threshold', 'value'))
        unseen = rng.normal(size=(500, 10)).astype(np.float32)
        np.testing.assert_array_equal(predict_tree(arrays, unseen), model.predict(unseen))

    def test_effect_expiry_and_active_healing_summaries(self):
        from sfld_rl.effect_refinement import EffectSummaries
        from sfld_rl.effect_lab import CURSES
        features = EffectSummaries(self.env.observation_names, self.env.action_names)
        obs, masks = self.observe(position(blessings=BLESSINGS['recovery_oldest'],
            curses=CURSES['short_curses'], encounter='cursed_chest'))
        group, first, _ = features.opportunities(obs, masks)
        values = dict(zip(features.feature_names, features.encode(obs, group, first)[0]))
        self.assertEqual(values['summary.curses.max_remaining'], 1)
        self.assertEqual(values['summary.curses.min_remaining'], 1)
        self.assertEqual(values['summary.recovery.slot'], 0)
        self.assertEqual(values['summary.recovery.remaining'], 5)
        self.assertAlmostEqual(float(values['summary.guaranteed_exit_hp']), .65, places=6)

    def test_auxiliary_target_cannot_become_deployment_value(self):
        if importlib.util.find_spec('sklearn') is None: self.skipTest('Optional fitting dependency absent')
        from sklearn.tree import DecisionTreeRegressor
        rng = np.random.default_rng(617)
        x = rng.normal(size=(200, 5)).astype(np.float32)
        y = np.column_stack([x[:, 0]*.01, 10+x[:, 1]])
        fitted = DecisionTreeRegressor(max_depth=5, random_state=2).fit(x, y)
        artifact = export_tree(fitted)
        arrays = tuple(np.asarray(artifact[k]) for k in ('left', 'right', 'feature', 'threshold', 'value'))
        unseen = rng.normal(size=(300, 5)).astype(np.float32)
        np.testing.assert_array_equal(predict_tree(arrays, unseen), fitted.predict(unseen)[:, 0])


if __name__ == '__main__': unittest.main()
