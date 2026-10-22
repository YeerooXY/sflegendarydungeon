"""Fixed trap samples retain missing outcomes and separate pair/side denominators."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.trap_rates import analyze, normalize_samples, render
from sfld_vod.traps import ABSENCE_CHECKS


def row(ident='one', values=(False, False), kinds=('monster', 'locked'),
        source='source', sample='sample', gems=(), stage=2):
    evidence = [{'sha256': str(i) * 64, 'source_seconds': 100 + i / 10,
                 'native_height': 720, 'role': 'native_pre_entry_trap_review'} for i in (1, 2)]
    return {'observation_id': ident, 'sample_id': sample, 'source_id': source,
        'source_seconds': 100, 'expected_fingerprint': 'f' * 64,
        'context': {'run_id': 'run', 'room': 30, 'stage': stage, 'phase': 'ordinary',
                    'gems': list(gems), 'stone_status': 'verified'},
        'evidence': evidence,
        'doors': [{'kind': kind, 'trap': value, 'visual': 'axe' if value is True else None,
                   'reason': 'Direct visual review.', 'evidence_sha256': [e['sha256'] for e in evidence],
                   'checks': {key: True for key in ABSENCE_CHECKS}}
                  for kind, value in zip(kinds, values)]}


def dataset(*rows):
    samples = {}
    for r in rows:
        s = samples.setdefault(r['sample_id'], {'sample_id': r['sample_id'], 'source_id': r['source_id'],
            'boundary_source_seconds': 90, 'expected_count': 0, 'selected_before_visual_inspection': True,
            'sampling': 'Fixed boundary, independent of outcome.', 'selected_observation_ids': []})
        s['expected_count'] += 1
        s['selected_observation_ids'].append(r['observation_id'])
    return {'schema': 1, 'scope': 'visible_pre_entry_trap_overlay', 'samples': list(samples.values()), 'records': list(rows)}


class TrapRateTests(unittest.TestCase):
    def test_unknown_is_not_absence_and_bounds_retain_all_selected(self):
        result = analyze(dataset(row('a', (None, False)), row('b', (False, False))), trap_chance=.25)
        report = result['overall_descriptive']
        self.assertEqual(report['pairs']['unknown'], 1)
        self.assertEqual(report['pairs']['absent'], 1)
        self.assertEqual(report['pairs']['known_only']['opportunities'], 1)
        self.assertEqual(report['pairs']['missing_data_fraction_bounds'], [0, .5])
        self.assertEqual(report['all_offered_sides']['opportunities'], 4)
        self.assertEqual(report['all_offered_sides']['unknown'], 1)
        text = render(result)
        self.assertIn('0.00% of resolved pairs** (0/1)', text)
        self.assertIn('all **2 selected pairs**', text)
        self.assertIn('**0.00% and 50.00%**', text)
        self.assertIn('This comparison uses `trap_chance=0.25`', text)
        self.assertNotIn('current profiles use', text)

    def test_one_visible_trap_with_other_side_unknown_is_a_positive_pair(self):
        report = analyze(dataset(row(values=(True, None))))['overall_descriptive']
        self.assertEqual(report['pairs']['present'], 1)
        self.assertEqual(report['pairs']['unknown'], 0)
        self.assertEqual(report['all_offered_sides']['unknown'], 1)

    def test_duplicate_observation_ids_fail(self):
        with self.assertRaisesRegex(ValueError, 'unique observation'):
            analyze(dataset(row(), row()))

    def test_manifest_and_review_omissions_fail(self):
        data = dataset(row('a'), row('b'))
        missing_record = deepcopy(data); missing_record['records'].pop()
        with self.assertRaisesRegex(ValueError, 'missing'):
            analyze(missing_record)
        missing_manifest = deepcopy(data)
        missing_manifest['samples'][0]['selected_observation_ids'].pop()
        missing_manifest['samples'][0]['expected_count'] = 1
        with self.assertRaisesRegex(ValueError, 'complete selection manifest'):
            analyze(missing_manifest)

    def test_full_stones_and_sources_are_not_collapsed(self):
        report = analyze(dataset(row('a'), row('b', gems=('gambler',)),
                                 row('c', source='other', sample='other')))
        self.assertEqual(len(report['by_source']), 2)
        self.assertEqual(len(report['by_full_context']), 3)
        counts = {g['context']['gambler_present']: g['pairs']['opportunities'] for g in report['by_gambler']}
        self.assertEqual(counts, {False: 2, True: 1})

    def test_two_traps_are_allowed_and_count_as_one_pair_two_sides(self):
        report = analyze(dataset(row(values=(True, True), kinds=('mystery', 'mystery'))))['overall_descriptive']
        self.assertEqual(report['pairs']['present'], 1)
        self.assertEqual(report['all_offered_sides']['present'], 2)
        self.assertEqual(report['pairs_with_two_visible_traps'], 1)

    def test_model_comparison_uses_number_of_monster_mystery_sides(self):
        one = analyze(dataset(row()))['overall_descriptive']['model_expected_trap_pairs_for_observed_pairs']
        two = analyze(dataset(row(kinds=('monster', 'mystery'))))['overall_descriptive']['model_expected_trap_pairs_for_observed_pairs']
        physical = analyze(dataset(row(kinds=('locked', 'wall'))))['overall_descriptive']['model_expected_trap_pairs_for_observed_pairs']
        forced = analyze(dataset(row(gems=('masochist',))))['overall_descriptive']['model_expected_trap_pairs_for_observed_pairs']
        self.assertAlmostEqual(one, .10); self.assertAlmostEqual(two, .19)
        self.assertEqual(physical, 0); self.assertEqual(forced, 1)

    def test_absence_requires_two_native_readable_frames(self):
        r = row(); r['doors'][0]['checks']['floor_area_visible'] = False
        with self.assertRaisesRegex(ValueError, 'Absence requires'):
            analyze(dataset(r))
        r = row(); r['evidence'][0]['native_height'] = 360
        with self.assertRaisesRegex(ValueError, 'at least 720p'):
            analyze(dataset(r))

    def test_normalization_validates_native_hashes_and_manifest_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); r = row()
            evs = []
            for i, ev in enumerate(r['evidence']):
                path = root / f'{i}.png'; path.write_bytes(f'proof-{i}'.encode())
                evs.append({**ev, 'image': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            for d in r['doors']: d['evidence_sha256'] = [e['sha256'] for e in evs]
            item = {'observation_id': r['observation_id'], 'expected_fingerprint': r['expected_fingerprint'],
                    'source_seconds': 100, 'context': r['context'], 'door_kinds': ['monster', 'locked'], 'evidence': evs}
            patch = {'observation_id': r['observation_id'], 'source_id': r['source_id'],
                     'expected_fingerprint': r['expected_fingerprint'], 'evidence': evs,
                     'set': {'door_traps': [False, False]},
                     'review': {'doors': r['doors'], 'evidence_sha256': [e['sha256'] for e in evs]}}
            manifest = {'sampling': 'Fixed boundary.', 'selected_before_visual_inspection': True, 'items': [item]}
            for name, value in [('manifest', manifest), ('queue', {'items': [item]}), ('patch', {'patches': [patch]})]:
                (root / f'{name}.json').write_text(json.dumps(value))
            specs = [{'sample_id': 'sample', 'source_id': 'source', 'expected_count': 1,
                      'boundary_source_seconds': 90, 'manifest': 'manifest.json',
                      'queue': 'queue.json', 'patch': 'patch.json'}]
            public = normalize_samples(specs, root)
            self.assertNotIn('image', public['records'][0]['evidence'][0])
            self.assertEqual(len(public['records']), 1)
            (root / '0.png').write_bytes(b'changed proof')
            with self.assertRaisesRegex(ValueError, 'changed'):
                normalize_samples(specs, root)


if __name__ == '__main__':
    unittest.main()
