import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.full_door_check import check, _recognizers


class DoorQualityCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'out').mkdir()
        (self.root/'art').mkdir()
        (self.root/'art/manifest.json').write_text('{}')
        (self.root/'layout.json').write_text('{}')
        proof = []
        for i in range(2):
            data = f'proof{i}'.encode()
            (self.root/f'{i}.jpg').write_bytes(data)
            proof.append({'image': f'{i}.jpg', 'sha256': hashlib.sha256(data).hexdigest()})
        self.record = {'observation_id': 'visit', 'source_id': 'source', 'source_seconds': 10,
                       'status': 'machine_suggestions_only', 'doors': [{'kind': 'locked'}, {'kind': 'mystery'}],
                       'evidence': proof}
        (self.root/'visit.json').write_text(json.dumps(self.record))
        self.job = {'record': str(self.root/'visit.json'), 'art': str(self.root/'art'),
                    'layout': str(self.root/'layout.json'), 'output': str(self.root/'out')}
        self.fake_cv = types.SimpleNamespace(setNumThreads=lambda n: None, imread=lambda p: p)
        _recognizers.clear()

    def invoke(self, frames):
        with patch.dict(sys.modules, {'cv2': self.fake_cv}), patch('sfld_vod.door_recognition.MaskedDoorRecognizer') as detector:
            detector.return_value.doors.side_effect = frames
            result = check(self.job)
            return result, detector.return_value.doors.call_count

    def test_single_frame_disagreement_needs_second_confirmation(self):
        result, calls = self.invoke([[{'kind': 'cursed'}, {'kind': 'mystery'}],
                                     [{'kind': 'locked'}, {'kind': 'mystery'}]])
        self.assertEqual(calls, 2)
        self.assertEqual(result['status'], 'unresolved_disagreement')
        self.assertEqual(result['contradictions'], [])

    def test_repeated_disagreement_is_only_a_review_candidate(self):
        result, _ = self.invoke([[{'kind': 'cursed'}, {'kind': 'mystery'}]] * 2)
        self.assertEqual(result['status'], 'review_disagreement')
        self.assertEqual(result['expected'], ['locked', 'mystery'])
        self.assertEqual(json.loads((self.root/'visit.json').read_text())['doors'][0]['kind'], 'locked')

    def test_unknown_first_frame_is_not_a_disagreement(self):
        result, calls = self.invoke([[{'kind': None}, {'kind': 'mystery'}]])
        self.assertEqual(calls, 1)
        self.assertEqual(result['status'], 'partially_recognized_no_contradiction')

    def test_changed_native_proof_is_rejected(self):
        (self.root/'0.jpg').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.invoke([])


if __name__ == '__main__': unittest.main()
