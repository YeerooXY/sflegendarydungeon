"""Trial rewards must retain the distinction between no draw and no item."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sfld_vod.trial_outcomes import KINDS, trial_counts, verify_evidence


def attempt(ident="a"):
    return {
        "schema": 1, "id": ident, "source_id": "video", "run_id": "run", "initial_observation_id": ident,
        "source_interval": [1, 10], "entry_status": "entered", "entered_depth": 5, "completed_depth": 5,
        "termination": "completed", "context": {"mode": "normal", "gems": [], "version_cohort": "2026"},
        "verified_fields": ["identity", "trial_entry", "depth", "termination", "reward_contents"],
        "reward": {"state": "observed", "complete": True, "components": [
            {"kind": "gold", "quantity": 123, "quantity_verified": True, "rarity": None}]},
        "evidence": [{"image": "frame.jpg", "sha256": "0" * 64, "source_seconds": 5, "role": "reward"}],
    }


class TrialOutcomeTests(unittest.TestCase):
    def summary(self, rows):
        return trial_counts(rows)["pooled_descriptive_only"]

    def test_death_is_not_an_empty_chest_draw(self):
        death = attempt("death")
        death.update(entered_depth=4, completed_depth=3, termination="death")
        death["reward"] = {"state": "not_awarded", "complete": False, "components": []}
        counts = self.summary([attempt(), death])
        self.assertEqual(counts["confirmed_entries"], 2)
        self.assertEqual(counts["eligible_prize_chests"], 1)
        self.assertEqual(counts["observed_chest_fractions"]["gold"]["estimate"], 1)

    def test_unknown_entry_never_becomes_trial_death(self):
        row = attempt()
        row.update(entry_status="unknown", entered_depth=None, completed_depth=None, termination="death")
        row["verified_fields"] = ["identity", "termination"]
        row["reward"] = {"state": "not_awarded", "complete": False, "components": []}
        counts = self.summary([row])
        self.assertEqual(counts["confirmed_entries"], 0)
        self.assertEqual(counts["unproved_entry_records"], 1)
        self.assertEqual(counts["verified_terminations"], {})

    def test_unobserved_or_partial_contents_are_excluded(self):
        rows = [attempt("unknown"), attempt("partial")]
        rows[0]["reward"] = {"state": "unobserved", "complete": False, "components": []}
        rows[1]["reward"]["complete"] = False
        self.assertEqual(self.summary(rows)["eligible_prize_chests"], 0)

    def test_item_without_rarity_is_not_epic_or_legendary(self):
        row = attempt()
        row["reward"]["components"] = [{"kind": "item", "quantity": 1, "quantity_verified": True, "rarity": None}]
        counts = self.summary([row])["observed_chest_fractions"]
        self.assertEqual(counts["item"]["successes"], 1)
        for rarity in ("epic", "legendary"):
            self.assertEqual(counts[rarity]["opportunities"], 0)
            self.assertEqual(counts[rarity]["unknown_rarity_chests_excluded"], 1)
            self.assertIsNone(counts[rarity]["estimate"])

    def test_unknown_quantity_stays_null_without_losing_known_kind(self):
        row = attempt()
        row["reward"]["components"][0].update(quantity=None, quantity_verified=False)
        counts = self.summary([row])
        self.assertEqual(counts["component_counts"], {"gold": 1})
        self.assertIsNone(counts["quantities"][0]["components"][0]["quantity"])

    def test_every_explicit_reward_kind_gets_a_frequency(self):
        counts = self.summary([attempt()])["observed_chest_fractions"]
        self.assertTrue(KINDS - {"unknown"} <= set(counts))
        self.assertNotIn("unknown", counts)

    def test_distinct_components_do_not_inflate_chest_denominator(self):
        row = attempt()
        row["reward"]["components"].append({"kind": "item", "quantity": 1, "quantity_verified": True, "rarity": "epic"})
        counts = self.summary([row])
        self.assertEqual(counts["eligible_prize_chests"], 1)
        self.assertEqual(counts["observed_chest_fractions"]["epic"]["estimate"], 1)
        self.assertEqual(counts["observed_chest_fractions"]["legendary"]["estimate"], 0)

    def test_duplicate_id_and_relabelled_same_attempt_both_fail(self):
        for rename in (False, True):
            first = attempt(); second = deepcopy(first)
            if rename: second["id"] = "new-id"
            with self.subTest(rename=rename), self.assertRaisesRegex(ValueError, "Duplicate"):
                trial_counts([first, second])

    def test_retry_with_new_initial_observation_is_separate(self):
        self.assertEqual(self.summary([attempt("first"), attempt("retry")])["confirmed_entries"], 2)

    def test_mode_version_source_depth_stones_have_separate_strata(self):
        rows = [attempt(str(i)) for i in range(6)]
        rows[1]["context"]["mode"] = "ultimate"
        rows[2]["context"]["version_cohort"] = "2024-before-trial-fix"
        rows[3]["source_id"] = "another-video"
        rows[4].update(entered_depth=1, completed_depth=1, termination="cashed_out")
        rows[5]["context"]["gems"] = ["rabbit"]
        self.assertEqual(len(trial_counts(rows)["strata"]), 6)

    def test_fatal_gate_cannot_be_counted_as_survived(self):
        row = attempt(); row.update(entered_depth=4, completed_depth=4, termination="death")
        with self.assertRaisesRegex(ValueError, "fatal gate"): trial_counts([row])

    def test_missing_verification_blocks_chest_denominator(self):
        row = attempt(); row["verified_fields"].remove("reward_contents")
        self.assertEqual(self.summary([row])["eligible_prize_chests"], 0)

    def test_unverified_amount_and_resource_rarity_are_rejected(self):
        for change in ({"quantity_verified": False}, {"rarity": "legendary"}):
            row = attempt(); row["reward"]["components"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): trial_counts([row])

    def test_evidence_is_hashed_and_confined_to_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "frame.jpg").write_bytes(b"frame")
            row = attempt(); row["evidence"][0]["sha256"] = hashlib.sha256(b"frame").hexdigest()
            self.assertEqual(verify_evidence([row], root), 1)
            (root / "frame.jpg").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"): verify_evidence([row], root)
            row["evidence"][0]["image"] = "../outside.jpg"
            with self.assertRaisesRegex(ValueError, "escapes"): verify_evidence([row], root)


if __name__ == "__main__":
    unittest.main()
