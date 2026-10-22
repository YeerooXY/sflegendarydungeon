"""Confidence, group leakage and native evidence integrity regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.door_recognition import MaskedDoorRecognizer, stable_doors
from sfld_vod.recognition_benchmark import classification_metrics, validate_manifest, summarize_predictions, build_manifest
from sfld_vod.media import sha256


def manifest():
    return {'schema': 1, 'split_unit': 'source_id', 'records': [
        {'observation_id': 'a', 'source_id': 'stream-a', 'partition': 'development',
         'context': {'run_id': 'one', 'room': 4}, 'doors': ['locked', 'monster'],
         'images': [{'sha256': 'a1'}, {'sha256': 'a2'}]},
        {'observation_id': 'b', 'source_id': 'stream-b', 'partition': 'test',
         'context': {'run_id': 'one', 'room': 4}, 'doors': ['mystery', 'mystery'],
         'images': [{'sha256': 'b1'}, {'sha256': 'b2'}]}]}


class BenchmarkTests(unittest.TestCase):
    def test_masked_matcher_ignores_transparent_background_and_abstains_on_blank(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest('Native OpenCV dependencies not installed.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rng = np.random.default_rng(24)
            sprites = []
            for name in ('locked', 'double_locked'):
                sprite = np.zeros((130, 60, 4), dtype=np.uint8)
                sprite[:, :, :3] = rng.integers(20, 235, (130, 60, 3), dtype=np.uint8)
                sprite[:, :, 3] = 255
                sprite[20:100, :18, 3] = 0
                self.assertTrue(cv2.imwrite(str(root/f'{name}.png'), sprite))
                sprites.append(sprite)
            (root/'manifest.json').write_text(json.dumps({'assets': {'door.locked': 'locked.png',
                                                                        'door.double_locked': 'double_locked.png'}}))
            (root/'layout.json').write_text(json.dumps({'schema': 1, 'template_heights': [130],
                    'door_search': [[49, 29, 88, 158], [194, 29, 88, 158]]}))
            frame = np.full((224, 320, 3), 245, dtype=np.uint8)
            for index, sprite in enumerate((sprites[0], cv2.flip(sprites[1], 1))):
                target = frame[40:170, 55+index*145:115+index*145]
                visible = sprite[:, :, 3] == 255
                target[visible] = sprite[:, :, :3][visible]
            reader = MaskedDoorRecognizer(root/'layout.json', root)
            self.assertEqual([p['kind'] for p in reader.doors(frame)], ['locked', 'double_locked'])
            self.assertEqual([p['kind'] for p in reader.doors(np.zeros_like(frame))], [None, None])

    def test_unknowns_stay_in_denominator_and_false_positives_count(self):
        m = classification_metrics([('locked', None), ('locked', 'double_locked'),
                                    ('locked', 'locked'), ('monster', 'monster')])
        self.assertEqual((m['total'], m['accepted'], m['correct'], m['incorrect'], m['unknown']), (4, 3, 2, 1, 1))
        self.assertEqual(m['per_class']['locked']['unknown'], 1)
        self.assertEqual(m['confusion']['locked']['double_locked'], 1)
        self.assertEqual(m['correct_fraction_all'], .5)
        self.assertEqual(m['precision_when_accepted'], 2/3)

    def test_unlabeled_truth_does_not_become_empty_or_negative(self):
        m = classification_metrics([(None, 'locked'), ('monster', None)])
        self.assertEqual(m['total'], 1)
        self.assertIsNone(m['precision_when_accepted'])

    def test_source_split_keeps_whole_runs_and_all_adjacent_frames_together(self):
        valid = manifest()
        validate_manifest(valid)
        invalid = deepcopy(valid)
        invalid['records'][1]['source_id'] = 'stream-a'
        with self.assertRaisesRegex(ValueError, 'leakage'):
            validate_manifest(invalid)

    def test_image_reuse_cannot_cross_offers_or_count_as_repeated_evidence(self):
        duplicate = manifest()
        duplicate['records'][0]['images'][1]['sha256'] = 'a1'
        with self.assertRaisesRegex(ValueError, 'Repeated image'):
            validate_manifest(duplicate)
        duplicate = manifest()
        duplicate['records'][1]['images'][0]['sha256'] = 'a1'
        with self.assertRaisesRegex(ValueError, 'different offers'):
            validate_manifest(duplicate)

    def test_no_test_partition_cannot_be_reported_as_heldout(self):
        invalid = manifest()
        invalid['records'][1]['partition'] = 'development'
        with self.assertRaisesRegex(ValueError, 'Both development'):
            validate_manifest(invalid)

    def test_temporal_conflict_abstains_even_with_majority(self):
        pairs = [[{'kind': 'locked'}, {'kind': 'mystery'}],
                 [{'kind': 'locked'}, {'kind': 'mystery'}],
                 [{'kind': 'double_locked'}, {'kind': None}]]
        r = stable_doors(pairs)
        self.assertIsNone(r[0]['kind'])
        self.assertEqual(r[0]['conflicting_labels'], ['double_locked', 'locked'])
        self.assertEqual(r[1]['kind'], 'mystery')

    def test_one_good_frame_is_not_enough(self):
        r = stable_doors([[{'kind': 'locked'}, {'kind': None}],
                          [{'kind': None}, {'kind': None}]])
        self.assertIsNone(r[0]['kind'])
        self.assertEqual(r[0]['supporting_frames'], 1)
        with self.assertRaises(ValueError):
            stable_doors([], minimum_agreement=1)

    def test_metrics_separate_holdout_from_development_and_complete_pairs(self):
        records = manifest()['records']
        for row in records:
            row['predictions'] = {'candidate': {'frames': [[{'kind': k} for k in row['doors']]],
                                                'stable': [{'kind': k} for k in row['doors']]}}
            row['gems'] = ['greasy']
            row['gem_predictions'] = {'stable_gems': ['greasy']}
        records[1]['predictions']['candidate']['stable'][1]['kind'] = None
        r = summarize_predictions(records)
        self.assertEqual(r['development']['methods']['candidate']['complete_pairs_correct'], 1)
        self.assertEqual(r['test']['methods']['candidate']['complete_pairs_correct'], 0)
        self.assertEqual(r['test']['methods']['candidate']['stable_offer_sides']['unknown'], 1)
        self.assertEqual(r['test']['gems']['complete_stone_sets_asserted'], 0)

    def test_manifest_build_checks_hash_and_never_omits_a_labeled_offer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels, queues = [], []
            for source in ('a', 'b'):
                context = {'run_id': 'one', 'room': 1, 'gems': [], 'stone_status': 'verified'}
                evidence = []
                for n in range(2):
                    image = root / f'{source}-{n}.png'
                    image.write_bytes(f'native-{source}-{n}'.encode())
                    evidence.append({'image': image.name, 'sha256': sha256(image),
                                     'source_seconds': n, 'native_height': 720})
                labels.append({'observation_id': source, 'source_id': source, 'context': context,
                               'doors': [{'kind': 'locked', 'trap': False}, {'kind': 'monster', 'trap': None}],
                               'evidence': evidence})
                q = root/f'{source}.json'
                q.write_text(json.dumps({'items': [{'id': source, 'context': context, 'evidence': evidence}]}))
                queues.append(q)
            lab = root/'labels.json'
            lab.write_text(json.dumps({'records': labels}))
            value = build_manifest(lab, queues, root, {'b'})
            self.assertEqual(len(value['records']), 2)
            with self.assertRaisesRegex(ValueError, 'cover every'):
                build_manifest(lab, queues[:1], root, {'b'})
            (root/'a-0.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'differs'):
                build_manifest(lab, queues, root, {'b'})


if __name__ == '__main__':
    unittest.main()
