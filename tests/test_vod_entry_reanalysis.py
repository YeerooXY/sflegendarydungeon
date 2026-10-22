"""Conditional entry linkage must not convert unreviewed interiors into actions."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sfld_vod.entry_reanalysis import analyze, inventory_row


def observation(ident="one", kinds=("monster", "mystery"), content="chest", gems=()):
    value = {"id": ident, "source_id": "source", "status": "decision", "generation": "ordinary",
             "context": {"run_id": ident, "room": 10, "stage": 1, "phase": "ordinary",
                         "run_class": None, "gems": list(gems), "stone_status": "verified"},
             "doors": [{"kind": kind, "trap": None} for kind in kinds],
             "chosen_side": None, "verified_fields": ["identity", "doors", "stones"]}
    if content:
        value["outcome"] = {"initial_content": content, "evidence": [{"sha256": "prior"}]}
        value["verified_fields"] += ["initial_content", "entered_room"]
    return value


def review(row):
    kinds = [d["kind"] for d in row["doors"]]
    return {"observation_id": row["id"], "status": "verified_floor_object",
            "expected_offered_kinds": kinds, "expected_initial_content": row["outcome"]["initial_content"],
            "initial_content": row["outcome"]["initial_content"], "selected_side": 1-kinds.index("monster"),
            "checks": {key: True for key in ("same_offer", "actual_floor_object", "initial_object_before_interaction",
                                              "not_reward_popup", "native_sequence_reviewed")},
            "evidence": [{"sha256": "native", "source_seconds": 100, "native_height": 720}]}


def key_review(row):
    value = review(observation(content=row["outcome"]["initial_content"]))
    value.update(observation_id=row["id"], status="verified_resource_cost",
                 expected_offered_kinds=[d["kind"] for d in row["doors"]],
                 keys_before=4, keys_after=3, selected_side=1)
    value["checks"]["key_cost_visible_before_interaction"] = True
    return value


class EntryReanalysisTests(unittest.TestCase):
    def test_offer_only_correction_changes_denominators_without_inventing_entry(self):
        row = observation(kinds=("mystery", "hungry"), content=None)
        evidence = {"observation_id":row["id"], "status":"verified_offer_correction",
                    "expected_offered_kinds":["mystery", "hungry"], "corrected_offered_kinds":["mystery", "cursed"],
                    "expected_initial_content":None, "initial_content":None, "reason":"Cursed artwork visible.",
                    "checks": {k: True for k in ("same_offer", "native_sequence_reviewed", "door_artwork_visible")},
                    "evidence":[{"sha256":"native", "source_seconds":100, "native_height":720}]}
        rows, report = analyze([row], [evidence])
        self.assertEqual(rows[0]["offered_kinds"], ["mystery", "cursed"])
        self.assertEqual(rows[0]["entry_link_status"], "route_and_content_unknown")
        self.assertEqual(report["native_offer_corrections"], 1)
        self.assertEqual(report["new_native_route_links"], 0)

    def test_verified_unknown_door_resolution_can_restore_eligible_offer(self):
        row = observation(kinds=(None, "hungry"), content=None)
        row["verified_fields"].remove("doors")
        evidence = {"observation_id":row["id"], "status":"verified_offer_correction",
                    "expected_offered_kinds":[None, "hungry"], "corrected_offered_kinds":["mystery", "hungry"],
                    "expected_initial_content":None, "initial_content":None, "reason":"Question-mark body visible below trap.",
                    "checks": {k: True for k in ("same_offer", "native_sequence_reviewed", "door_artwork_visible")},
                    "evidence":[{"sha256":"native", "source_seconds":100, "native_height":720}]}
        rows, report = analyze([row], [evidence])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["newly_eligible_from_verified_door_resolution"], 1)
        self.assertIsNone(rows[0]["selected_kind"])

    def test_key_loss_before_floor_object_interaction_attributes_locked_door(self):
        row = observation(kinds=("mystery", "locked"))
        rows, report = analyze([row], [key_review(row)])
        self.assertEqual(rows[0]["selected_kind"], "locked")
        self.assertEqual(rows[0]["selection_basis"], "verified_key_cost_before_object_interaction")
        self.assertEqual(report["new_native_key_cost_links"], 1)

    def test_key_timing_and_uncertain_costs_cannot_create_routes(self):
        row = observation(kinds=("mystery", "locked"))
        for after in (4, 2, -1):
            evidence = key_review(row)
            evidence["keys_after"] = after
            with self.assertRaises(ValueError):
                analyze([row], [evidence])
        evidence = key_review(row)
        evidence["checks"]["key_cost_visible_before_interaction"] = False
        with self.assertRaises(ValueError):
            analyze([row], [evidence])

    def test_native_content_correction_does_not_resolve_unknown_route(self):
        row = observation(kinds=("mystery", "locked"))
        evidence = key_review(row)
        evidence.update(status="verified_content_correction", initial_content="barrel")
        evidence.pop("selected_side")
        rows, report = analyze([row], [evidence])
        self.assertEqual(rows[0]["initial_content"], "barrel")
        self.assertIsNone(rows[0]["selected_kind"])
        self.assertEqual(report["native_content_corrections"], 1)

    def test_reviewed_unknown_route_remains_in_audit_without_counting_a_link(self):
        row = observation(kinds=("mystery", "locked"))
        evidence = key_review(row)
        evidence.update(status="route_unresolved", reason="Counter unchanged; active effects unresolved.")
        evidence.pop("selected_side")
        rows, report = analyze([row], [evidence])
        self.assertEqual(rows[0]["entry_link_status"], "route_unknown_content_known")
        self.assertEqual(report["new_native_routes_still_unresolved"], 1)
        self.assertEqual(report["new_native_route_links"], 0)

    def test_same_content_does_not_attribute_without_native_review(self):
        row = observation()
        row["chosen_side"] = 1
        row["chosen_side_status"] = "inferred_from_pointer_and_adjacent_scene"
        rows, report = analyze([row])
        self.assertEqual(rows[0]["entry_link_status"], "route_unknown_content_known")
        self.assertEqual(report["all_offers_descriptive"]["mystery_by_exact_alternative"]["monster"]["unlinked_following_contents"], {"chest": 1})

    def test_reviewed_floor_object_excludes_visible_monster_with_distinct_basis(self):
        row = observation()
        rows, report = analyze([row], [review(row)])
        self.assertEqual(rows[0]["selected_kind"], "mystery")
        self.assertEqual(rows[0]["selection_basis"], "reviewed_floor_object_excludes_visible_monster")
        self.assertEqual(report["new_native_route_links"], 1)

    def test_empty_room_cannot_exclude_fast_monster_fight(self):
        row = observation(content="empty")
        with self.assertRaises(ValueError):
            analyze([row], [review(row)])

    def test_popup_without_preinteraction_object_is_not_evidence(self):
        row = observation()
        evidence = review(row)
        evidence["checks"]["initial_object_before_interaction"] = False
        with self.assertRaises(ValueError):
            analyze([row], [evidence])

    def test_corpses_remain_corpses_even_if_awakened_later(self):
        row = observation(content="corpse")
        row["outcome"]["later_content"] = "monster"
        rows, _ = analyze([row], [review(row)])
        self.assertEqual(rows[0]["initial_content"], "corpse")

    def test_native_rejection_removes_stale_content_from_count(self):
        row = observation(kinds=("monster", "locked"))
        evidence = review(row)
        evidence.update(status="content_conflict", initial_content=None,
                        reason="No floor chest visible; only an old floating item reward.")
        rows, report = analyze([row], [evidence])
        self.assertIsNone(rows[0]["initial_content"])
        self.assertIsNone(rows[0]["selected_kind"])
        self.assertEqual(report["native_content_conflicts"], 1)

    def test_checked_offer_and_object_corrections_are_explicit(self):
        row = observation(kinds=("monster", "locked"), content="barrel")
        evidence = review(row)
        evidence.update(corrected_offered_kinds=["monster", "mystery"], initial_content="wooden_box")
        rows, report = analyze([row], [evidence])
        self.assertEqual(rows[0]["selected_kind"], "mystery")
        self.assertEqual(rows[0]["initial_content"], "wooden_box")
        self.assertEqual(report["native_offer_corrections"], 1)
        self.assertEqual(report["native_content_corrections"], 1)

    def test_stale_review_and_duplicate_ids_are_rejected(self):
        row = observation()
        evidence = review(row)
        evidence["expected_initial_content"] = "barrel"
        with self.assertRaises(ValueError):
            analyze([row], [evidence])
        with self.assertRaises(ValueError):
            analyze([row, deepcopy(row)])
        with self.assertRaises(ValueError):
            analyze([row], [review(row), review(row)])

    def test_identical_choices_and_walls_do_not_require_hover(self):
        for kinds in (("mystery", "mystery"), ("mystery", "wall")):
            rows, _ = analyze([observation(kinds=kinds, content="monster")])
            self.assertEqual(rows[0]["selected_kind"], "mystery")
        rows, _ = analyze([observation(kinds=("mystery", "wall"), content=None)])
        self.assertIsNone(rows[0]["selected_kind"])

    def test_unknown_resource_timing_does_not_become_a_route(self):
        row = observation(kinds=("mystery", "locked"), content="monster")
        row.update(chosen_side=0, chosen_side_status="timing_conditional_resource_hypothesis")
        rows, _ = analyze([row])
        self.assertIsNone(rows[0]["selected_kind"])

    def test_stone_exposures_and_exact_alternative_denominators_remain_separate(self):
        rows = [observation("a", kinds=("monster", "unlocked"), gems=("spying", "greed")),
                observation("b", kinds=("monster", "locked")),
                observation("c", kinds=("mystery", "mystery"), content="corpse")]
        _, report = analyze(rows, [review(rows[0]), review(rows[1])])
        self.assertEqual(report["baseline_no_stones"]["eligible_offers"], 2)
        self.assertEqual(report["stone_specific_exposures"]["spying"]["eligible_offers"], 1)
        self.assertEqual(len(report["fully_conditioned_strata"]), 2)
        mysteries = report["all_offers_descriptive"]["mystery_by_exact_alternative"]
        self.assertEqual(mysteries["mystery"]["initial_contents"], {"corpse": 1})


if __name__ == "__main__":
    unittest.main()
