"""Guard against inflated coverage and unjustified legendary classifications."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sfld_vod.arena_rewards import audit_arena
from test_vod_trial_outcomes import attempt


def covered_attempt(ident="a"):
    row = attempt(ident)
    row["covered_observation_ids"] = [ident + ":I", ident + ":II"]
    row["source_url"] = "https://example.test/video"
    return row


class ArenaRewardTests(unittest.TestCase):
    def test_relabelled_sequence_cannot_reuse_the_same_menu(self):
        first, second = covered_attempt(), covered_attempt("b")
        second["covered_observation_ids"][0] = "a:I"
        with self.assertRaisesRegex(ValueError, "exactly one"):
            audit_arena([first, second])

    def test_complete_inventory_requires_every_expected_menu(self):
        with self.assertRaisesRegex(ValueError, "1 missing"):
            audit_arena([covered_attempt()], {"a:I", "a:II", "a:III"})

    def test_gold_from_item_sale_is_not_a_chest_component(self):
        row = covered_attempt()
        row["reward"]["components"][0]["origin"] = "item_sale"
        with self.assertRaisesRegex(ValueError, "second chest reward"):
            audit_arena([row])

    def test_legendary_needs_a_linked_frame_not_just_a_tier(self):
        row = covered_attempt()
        item = {"kind": "item", "quantity": 1, "quantity_verified": True, "rarity": "legendary"}
        row["reward"]["components"] = [item]
        for evidence in ({}, {"mapping": "colors.json", "frame": "absent.jpg"}):
            item["rarity_evidence"] = evidence
            with self.assertRaisesRegex(ValueError, "positive frame"):
                audit_arena([row])
        item["rarity_evidence"] = {"mapping": "colors.json", "frame": "frame.jpg"}
        self.assertEqual(len(audit_arena([row])["legendary_evidence"]), 1)

    def test_opened_unreadable_chest_stays_in_missing_outcomes(self):
        row = covered_attempt()
        row["reward"] = {"state": "unobserved", "complete": False, "components": []}
        report = audit_arena([row])
        self.assertEqual(report["missing_rewards"], ["a"])
        self.assertEqual(report["pooled_descriptive_only"]["eligible_prize_chests"], 0)


if __name__ == "__main__":
    unittest.main()
