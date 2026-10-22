"""Validate probability models used for the exploratory room-generator analysis."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
try:
    import numpy as np
    from analyze_room_generation_2026_10_12 import (
        PAIR_INDICES, PAIR_ORDER, fit_shared_monster_probability, fit_two_pools,
        joint_as_conditional, sequential_probabilities, two_pool_probabilities,
    )
    AVAILABLE = True
except ModuleNotFoundError as exc:
    if exc.name.split('.')[0] not in {'numpy', 'scipy'}:
        raise
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Offline fitting requires NumPy and SciPy')
class GeneratorAnalysisTests(unittest.TestCase):
    def test_conditional_representation_preserves_arbitrary_observed_pairs(self):
        observed = np.asarray([.55, .17, .15, .04, .05, .025, .01, .005])
        first, conditional = joint_as_conditional(observed)
        joint = first[:, None] * conditional
        recovered = [joint[a, b] if a == b else joint[a, b] + joint[b, a] for a, b in PAIR_INDICES]
        np.testing.assert_allclose(recovered, observed, atol=1e-12)
        self.assertEqual(joint[0, 0], 0.)
        self.assertEqual(joint[1, 1], 0.)

    def test_both_models_normalize_for_different_pool_weights(self):
        for z in ([0., 0., 0.], [2., -1., 3.], [-3., 2., 0.]):
            self.assertAlmostEqual(float(sequential_probabilities(z).sum()), 1.)
        for z in ([0., 0., 0., 0.], [2., 3., 3., 2.], [-3., 2., 1., -2.]):
            self.assertAlmostEqual(float(two_pool_probabilities(z).sum()), 1.)

    def test_fit_recovers_synthetic_two_pool_distribution(self):
        expected = two_pool_probabilities([2., 3., 3., 2.])
        result = fit_two_pools(expected * 100000)
        fitted = [result['fitted_probabilities'][k] for k in PAIR_ORDER]
        np.testing.assert_allclose(fitted, expected, atol=1e-7)

    def test_shared_monster_probability_recovers_ninety_forty_five_pattern(self):
        result = fit_shared_monster_probability({'k': 900, 'n': 1000}, {'k': 450, 'n': 1000})
        self.assertAlmostEqual(result['p'], .9, places=6)
        self.assertLess(result['profile_likelihood_95'][0], .9)
        self.assertGreater(result['profile_likelihood_95'][1], .9)


if __name__ == '__main__':
    unittest.main()
