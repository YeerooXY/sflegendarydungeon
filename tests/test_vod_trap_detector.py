import copy
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.trap_detector import DEFAULT_CONFIG, TrapDetector, config_hash, consensus, main
from sfld_vod.trap_benchmark import metrics, validate_manifest

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


def hit(label='shark', score=.92, x=150, y=360):
    return {'visual': label, 'score': score, 'bbox': [x, y, 80, 40]}


def frame(left=(), right=(), usable=True):
    return {'usable': usable, 'sides': [{'candidates': list(left)}, {'candidates': list(right)}]}


class TrapConsensusTests(unittest.TestCase):
    def test_stable_positive_and_unmatched_side_is_unknown(self):
        result = consensus([frame([hit()]), frame([hit(x=153)])], DEFAULT_CONFIG)
        self.assertIs(result[0]['present'], True)
        self.assertEqual(result[0]['visual'], 'shark')
        self.assertEqual(result[0]['support_frames'], 2)
        self.assertIsNone(result[1]['present'])

    def test_early_frame_or_single_hit_does_not_promote(self):
        result = consensus([frame([hit()]), frame(usable=False)], DEFAULT_CONFIG)
        self.assertIsNone(result[0]['present'])
        self.assertEqual(result[0]['reason'], 'insufficient_usable_frames')

    def test_one_frame_cannot_vote_twice(self):
        result = consensus([frame([hit(), hit(x=151)]), frame()], DEFAULT_CONFIG)
        self.assertIsNone(result[0]['present'])
        self.assertEqual(result[0]['support_frames'], 1)

    def test_moving_or_conflicting_matches_abstain(self):
        self.assertIsNone(consensus([frame([hit()]), frame([hit(x=210)])], DEFAULT_CONFIG)[0]['present'])
        conflicting = [frame([hit(), hit('cleaver')]), frame([hit(), hit('cleaver')])]
        result = consensus(conflicting, DEFAULT_CONFIG)[0]
        self.assertIsNone(result['present'])
        self.assertEqual(result['reason'], 'conflicting_subtypes')

    def test_uncertain_frame_is_not_false_absence(self):
        result = consensus([frame([hit(score=.79)]), frame([hit(score=.79)])], DEFAULT_CONFIG)
        self.assertIsNone(result[0]['present'])
        self.assertEqual(result[0]['reason'], 'no_consistent_positive_match')

    def test_abstentions_count_as_recall_misses(self):
        result = metrics([(True, True), (True, None), (False, True), (False, None), (None, True)])
        self.assertEqual(result['recall'], .5)
        self.assertEqual(result['precision'], .5)
        self.assertEqual(result['coverage'], .5)
        self.assertEqual(result['predicted_positive_unlabelled'], 1)

    def manifest(self, directory):
        records = []
        for index, (source, split) in enumerate([('a', 'development'), ('b', 'held_out')]):
            evidence = []
            for suffix in range(2):
                path = directory/f'{index}-{suffix}.bin'; path.write_bytes(f'{index}-{suffix}'.encode())
                evidence.append({'image': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'source_seconds': suffix})
            records.append({'observation_id': str(index), 'source_id': source, 'run_id': 'run', 'split': split,
                            'truth': [True, False], 'evidence': evidence})
        return {'schema': 1, 'frozen_config': copy.deepcopy(DEFAULT_CONFIG), 'frozen_config_sha256': config_hash(DEFAULT_CONFIG), 'records': records}

    def test_benchmark_rejects_source_leakage_and_changed_config(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); manifest = self.manifest(directory)
            validate_manifest(manifest, directory)
            manifest['records'][1]['source_id'] = 'a'
            with self.assertRaisesRegex(ValueError, 'Source leakage'):
                validate_manifest(manifest, directory)
            manifest['records'][1]['source_id'] = 'b'
            manifest['frozen_config']['positive_threshold'] = .1
            with self.assertRaisesRegex(ValueError, 'settings changed'):
                validate_manifest(manifest, directory)

    def test_benchmark_rejects_changed_frames_and_duplicate_visits(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); manifest = self.manifest(directory)
            manifest['records'][1]['observation_id'] = '0'
            with self.assertRaisesRegex(ValueError, 'Duplicate visit'):
                validate_manifest(manifest, directory)
            manifest['records'][1]['observation_id'] = '1'
            (directory/'1-0.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                validate_manifest(manifest, directory)

    def test_benchmark_rejects_copied_proof_with_different_name_and_time(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); manifest = self.manifest(directory)
            first, second = manifest['records'][0]['evidence']
            (directory/second['image']).write_bytes((directory/first['image']).read_bytes())
            second['sha256'] = first['sha256']
            self.assertNotEqual(first['image'], second['image'])
            self.assertNotEqual(first['source_seconds'], second['source_seconds'])
            with self.assertRaisesRegex(ValueError, 'Identical evidence hash reused within one visit'):
                validate_manifest(manifest, directory)

    def test_standalone_rejects_copied_proof_before_loading_art(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); paths = [directory/'first.png', directory/'copy.png']
            for path in paths:
                path.write_bytes(b'identical content under distinct names')
            output = directory/'result.json'; errors = io.StringIO()
            with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
                main(['--art', str(directory/'missing-art'), '--frames', *map(str, paths), '--output', str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('Identical image content', errors.getvalue())
            self.assertFalse(output.exists())


@unittest.skipIf(cv2 is None, 'OpenCV optional research dependency unavailable')
class MaskedMatchingTests(unittest.TestCase):
    def test_transparency_and_background_do_not_hide_an_opaque_trap(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); rng = np.random.default_rng(12)
            raw = rng.integers(10, 245, (64, 64, 4), dtype=np.uint8)
            raw[:, :, 3] = 0; raw[5:59, 8:56, 3] = 255; raw[23:41, 23:41, 3] = 0
            cv2.imwrite(str(directory/'sprite.png'), raw)
            (directory/'manifest.json').write_text(json.dumps({'assets': {'trap.test': 'sprite.png'}}))
            cfg = {**copy.deepcopy(DEFAULT_CONFIG), 'scales': [1.], 'refine_offsets': [0.], 'aspect_factors': [1.], 'coarse_threshold': .5}
            detector = TrapDetector(directory, cfg)
            frames = []
            for background in (30, 180):
                image = rng.integers(0, 40, (560, 800, 3), dtype=np.uint8)+background
                roi = image[150:214, 170:234]; opaque = raw[:, :, 3] > 180; roi[opaque] = raw[:, :, :3][opaque]
                frames.append(image)
            result = detector.predict_frames(frames)
            self.assertIs(result['sides'][0]['present'], True)
            self.assertIsNone(result['sides'][1]['present'])
            self.assertGreater(result['sides'][0]['score'], .98)
            self.assertFalse(detector.read(np.zeros((224, 320, 3), np.uint8))['usable'])
            self.assertFalse(detector.read(np.zeros((560, 800, 3), np.uint8))['usable'])
            score, _ = detector._match(np.zeros((80, 80), np.uint8), np.zeros((20, 20), np.uint8), np.ones((20,20), np.uint8)*255)
            self.assertLess(score, 0)


if __name__ == '__main__':
    unittest.main()
