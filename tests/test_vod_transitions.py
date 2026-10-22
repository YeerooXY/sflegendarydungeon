"""Temporal evidence regression: fades, rapid combat, and repeated room art."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sfld_vod.transitions import unique_items


class CandidateIdentityTests(unittest.TestCase):
    def test_same_art_different_visit_is_retained_but_duplicate_id_is_collapsed(self):
        a = {"observation_id": "a", "source_end": 10, "image": "same.jpg"}
        b = {"observation_id": "b", "source_end": 12, "image": "same.jpg"}
        items, duplicate = unique_items([a, a.copy(), b])
        self.assertEqual(items, [a, b])
        self.assertEqual(duplicate, ["a"])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            unique_items([a, {**a, "source_end": 11}])


NATIVE = all(importlib.util.find_spec(x) for x in ("numpy", "cv2", "imageio_ffmpeg"))


@unittest.skipUnless(NATIVE, "Optional native VOD environment required.")
class TransitionTests(unittest.TestCase):
    def sample(self, stamp, offer=False, brightness=60, content=0):
        import numpy as np
        return {"source_seconds": stamp, "features": {
            "brightness": brightness, "contrast": brightness / 2,
            "offer_vector": np.array([1., 0.]) if offer else np.array([0., 1.]),
            "interior_vector": np.eye(3)[content]}}

    def test_dark_frames_skipped_and_readable_pair_never_claims_a_side(self):
        from sfld_vod.transitions import select_transition
        samples = [self.sample(0, True), self.sample(.125, brightness=5),
                   self.sample(.25, brightness=20), self.sample(.375), self.sample(.5)]
        result = select_transition(samples, 0)
        self.assertEqual(result["selected_index"], 3)
        self.assertEqual(result["confirmation_index"], 4)
        self.assertEqual(result["early_index"], 2)
        self.assertIsNone(result["chosen_side"])
        self.assertIsNone(result["initial_content"])
        self.assertFalse(result["resource_values_settled"])

    def test_next_room_cannot_supply_a_missing_initial_frame(self):
        from sfld_vod.transitions import select_transition
        samples = [self.sample(0, True), self.sample(.125, brightness=5),
                   self.sample(.25, True), self.sample(.375), self.sample(.5)]
        result = select_transition(samples, 0)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["selected_index"])
        self.assertEqual(result["return_seconds"], .25)

    def test_rapid_monster_disappearance_requires_temporal_review(self):
        from sfld_vod.transitions import select_transition
        samples = [self.sample(0, True), self.sample(.125, brightness=30, content=1),
                   self.sample(.25, brightness=40, content=1),
                   self.sample(.375, content=0), self.sample(.5, content=0)]
        result = select_transition(samples, 0)
        self.assertEqual(result["selected_index"], 3)
        self.assertEqual(result["status"], "unreviewed_temporal_change")
        self.assertTrue(result["earlier_content_may_differ"])

    def test_missing_preimage_and_sparse_neighbors_remain_unknown(self):
        from sfld_vod.transitions import select_transition
        self.assertEqual(select_transition([self.sample(1)], 0)["reason"], "missing_pre_entry_offer")
        result = select_transition([self.sample(0, True), self.sample(1), self.sample(2)], 0)
        self.assertEqual(result["status"], "unknown")
        with self.assertRaises(ValueError):
            select_transition([], 0, {"offer_similarity": 2})

    def test_native_queue_rejects_changed_evidence(self):
        import cv2
        import numpy as np
        from sfld_vod.media import sha256, write_json
        from sfld_vod.transitions import analyze_queue
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frame = np.random.default_rng(41).integers(0, 255, (480, 684, 3), dtype=np.uint8)
            cv2.imwrite(str(root / "frame.png"), frame)
            evidence = {"image": "frame.png", "sha256": sha256(root / "frame.png"), "source_seconds": 1}
            write_json(root / "queue.json", {"items": [{"observation_id": "visit", "source_id": "source",
                "source_end": 1, "evidence": [evidence]}]})
            report = analyze_queue(root, root / "queue.json", root / "report")
            self.assertEqual(report["summary"], {"unknown": 1})
            self.assertTrue((root / "report/review.html").is_file())
            (root / "frame.png").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                analyze_queue(root, root / "queue.json", root / "report")

    def test_bounded_native_extraction_preserves_resolution_and_can_resume(self):
        import cv2
        import numpy as np
        from sfld_vod.media import sha256, write_json
        from sfld_vod.transitions import extract_queue
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clips = root / "source/720p"
            clips.mkdir(parents=True)
            (root / "layouts").mkdir()
            video = clips / "sample.avi"
            rng = np.random.default_rng(4)
            offer = rng.integers(30, 230, (480, 640, 3), dtype=np.uint8)
            inside = rng.integers(30, 230, (480, 640, 3), dtype=np.uint8)
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"), 24, (640, 480))
            self.assertTrue(writer.isOpened())
            for index in range(72):
                writer.write(offer if index < 24 else inside)
            writer.release()
            write_json(clips / "sample.json", {"schema": 1, "file": video.name,
                "source_start": 0, "source_end": 3, "sha256": sha256(video)})
            write_json(root / "layouts/source.json", {"crop": [0, 0, 1, 1]})
            write_json(root / "manifest.json", {"items": [{"observation_id": "visit", "source_id": "source", "source_end": 1}]})
            result = extract_queue(root, root / "manifest.json", root / "output", after=1)
            self.assertEqual(len(result["items"]), 1)
            evidence = result["items"][0]["evidence"]
            self.assertTrue(all(e["crop_size"] == [640, 480] for e in evidence))
            frame = cv2.imread(str(root / evidence[0]["image"]))
            self.assertEqual(frame.shape[:2], (480, 640))
            self.assertEqual(extract_queue(root, root / "manifest.json", root / "output", after=1), result)
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                extract_queue(root, root / "manifest.json", root / "output", after=2)


if __name__ == "__main__":
    unittest.main()
