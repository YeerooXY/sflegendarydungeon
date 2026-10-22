import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.shop_timing import audit


def row(ident, room=35, source='current', run='run', **extra):
    result = {'id': ident, 'source_id': source, 'source_seconds': 1,
              'status': 'decision', 'verified_fields': ['doors'],
              'context': {'room': room, 'stage': (room - 1) // 25 + 1 if room else 2,
                          'run_id': run}, 'doors': [{'kind': 'shop'}, {'kind': 'wall'}]}
    result.update(extra)
    return result


class ShopTimingTests(unittest.TestCase):
    def test_duplicate_menu_visit_and_historical_are_separate(self):
        report = audit([row('a'), row('b'), row('c', source='youtube-DCb80VikGM0')])
        self.assertEqual(report['current_event']['visits'], 1)
        self.assertEqual(report['historical_separate']['visits'], 1)
        self.assertEqual(report['excluded']['repeated_shop_visit'], 1)

    def test_unknown_counters_are_not_imputed(self):
        report = audit([row('a', room=None), row('b', room=None), row('c')])
        self.assertEqual(report['current_event']['visits'], 3)
        self.assertEqual(report['current_event']['exact_room_count'], 1)

    def test_two_shops_in_one_stage_are_not_collapsed(self):
        report = audit([row('a', 31), row('b', 39)])
        self.assertEqual(report['current_event']['visits'], 2)
        self.assertEqual(report['current_event']['by_stage']['2']['median_room'], 35)

    def test_unverified_or_duplicate_labels_are_excluded(self):
        report = audit([row('a', verified_fields=[]), row('b', status='duplicate')])
        self.assertEqual(report['current_event']['visits'], 0)

    def test_counter_complete_requires_every_counter_and_verified_doors(self):
        rows = [row(str(n), n, doors=[{'kind': 'monster'}, {'kind': 'mystery'}]) for n in range(1, 26)]
        self.assertEqual(audit(rows)['stage1_counter_complete_summary']['without_observed_shop'], 1)
        rows[4]['verified_fields'] = []
        self.assertEqual(audit(rows)['stage1_counter_complete_summary']['passages'], 0)

    def test_bad_stage_or_duplicate_id_raises(self):
        with self.assertRaises(ValueError):
            audit([row('a', context={'room': 35, 'stage': 3, 'run_id': 'run'})])
        with self.assertRaises(ValueError):
            audit([row('a'), row('a')])


if __name__ == '__main__':
    unittest.main()
