"""Check planned tests against their error-rate and detection-power contracts."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from plan_trap_role_study import power,rejection_bounds,sample_size


class TrapRolePlanningTests(unittest.TestCase):
    def test_actual_false_positive_rate_is_bounded(self):
        for n in (1,10,43,200,388,639,898,1000):
            for sides in (1,2):
                self.assertLessEqual(power(n,.5,sides=sides),.05+1e-12)

    def test_two_sided_bounds_are_symmetric(self):
        lower,upper=rejection_bounds(639)
        self.assertEqual(lower,639-upper)
        self.assertGreater(upper,639/2)

    def test_sample_reaches_promised_power(self):
        for desired in (.8,.95,.99):
            plan=sample_size(desired)
            self.assertGreaterEqual(power(plan['n_trapped_pairs']),desired)
            # Checking the whole smaller range guards against a normal-curve
            # approximation presented as an exact first adequate sample.
            self.assertTrue(all(power(n)<desired for n in range(1,plan['n_trapped_pairs'])))

    def test_invalid_parameters_rejected(self):
        for n in (0,-1,1.5):
            with self.assertRaises(ValueError):rejection_bounds(n)
        with self.assertRaises(ValueError):sample_size(1)
        with self.assertRaises(ValueError):sample_size(.8,alternative=.5)


if __name__=='__main__':unittest.main()
