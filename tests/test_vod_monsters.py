"""Exact configured pair probabilities, independent of observed VOD frequencies."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from analyze_vod_monsters import pair_kernel, profile, weights


class OrdinaryPairKernel(unittest.TestCase):
    def fixture(self, **positive):
        p = profile(ROOT / 'profiles/synthetic.profile')
        table = dict.fromkeys(weights(p['doors']), 0)
        table.update(positive)
        p['doors'] = ','.join(f'{k}:{v}' for k, v in table.items())
        return p

    def test_equal_monster_mystery_draws_have_quarter_double_mystery(self):
        # M first always gets ?; ? first draws M or ? equally: P(M offered)=3/4.
        result = pair_kernel(self.fixture(monster=1, mystery=1), [], 2, False)
        self.assertAlmostEqual(result['visible_monster_offer_probability'], 3 / 4)
        self.assertAlmostEqual(result['double_mystery_probability'], 1 / 4)

    def test_mystery_only_table_is_not_replaced_by_monster_fallback(self):
        result = pair_kernel(self.fixture(mystery=1), [], 2, False)
        self.assertEqual(result['visible_monster_offer_probability'], 0)
        self.assertEqual(result['double_mystery_probability'], 1)

    def test_incompatible_only_tables_keep_the_core_fallback(self):
        for table in [dict(monster=1), dict(locked=1, cursed=1)]:
            with self.subTest(table=table):
                result = pair_kernel(self.fixture(**table), [], 2, False)
                self.assertEqual(result['visible_monster_offer_probability'], 1)
                self.assertEqual(result['double_mystery_probability'], 0)

    def test_golden_only_table_remains_compatible(self):
        result = pair_kernel(self.fixture(golden=1), [], 2, False)
        self.assertEqual(result['visible_monster_offer_probability'], 0)
        self.assertEqual(result['double_mystery_probability'], 0)

    def test_mystery_weight_stones_change_both_draws(self):
        p = self.fixture(monster=1, mystery=1)
        for gems, expected_double in [(['greed'], 4 / 9), (['explorer'], 1 / 9)]:
            with self.subTest(gems=gems):
                result = pair_kernel(p, gems, 2, False)
                self.assertAlmostEqual(result['double_mystery_probability'], expected_double)
                self.assertAlmostEqual(result['visible_monster_offer_probability'], 1 - expected_double)

    def test_pendant_keeps_a_locked_choice_and_first_stage_gates_still_apply(self):
        result = pair_kernel(self.fixture(monster=1, mystery=1), ['pendant'], 2, False)
        self.assertAlmostEqual(result['visible_monster_offer_probability'], 1 / 2)
        self.assertEqual(result['double_mystery_probability'], 0)
        result = pair_kernel(self.fixture(monster=1, mystery=1, cursed=10, sacrifice=10), [], 1, True)
        self.assertAlmostEqual(result['visible_monster_offer_probability'], 3 / 4)
        self.assertAlmostEqual(result['double_mystery_probability'], 1 / 4)


if __name__ == '__main__':
    unittest.main()
