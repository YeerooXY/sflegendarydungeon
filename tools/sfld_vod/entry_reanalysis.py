"""Recount every ordinary offer, retaining unknown entry routes and contents.

This is an evidence join and conditional inventory, not a room classifier.
Reviewed non-monster floor objects can exclude a visible-monster alternative;
empty scenes, pop-up loot, hover and unreviewed predictions cannot.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .door_contexts import content_known, choice_known, family
from .review import PHYSICAL
from .temporal import normalize_generation, observation_counts, selection_basis

FLOOR_OBJECTS = {"chest", "wooden_box", "corpse", "barrel"}
CONTEXT_FIELDS = ("version", "stage", "run_class", "theme", "mode", "phase")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def validated_reviews(reviews, observations):
    """Require explicit native floor-object checks; reject automatic side guesses."""
    result = {}
    for review in reviews:
        ident = review["observation_id"]
        if ident in result:
            raise ValueError(f"Repeated entry review: {ident}")
        if ident not in observations:
            raise ValueError(f"Unknown entry review: {ident}")
        row = observations[ident]
        if review.get("status") not in ("verified_floor_object", "verified_resource_cost", "verified_content_correction", "verified_offer_correction", "content_conflict", "route_unresolved"):
            continue
        original_kinds = [d["kind"] for d in row["doors"]]
        if (review.get("expected_offered_kinds") != original_kinds
                or review.get("expected_initial_content") != row.get("outcome", {}).get("initial_content")):
            raise ValueError(f"Entry review no longer matches its original labels: {ident}")
        kinds = review.get("corrected_offered_kinds") or original_kinds
        allowed_kinds = set(original_kinds + ["mystery"])
        if review.get("status") == "verified_offer_correction" or review.get("offer_correction_review"):
            allowed_kinds |= set(PHYSICAL) | {"monster", "golden"}
        if len(kinds) != 2 or any(k not in allowed_kinds for k in kinds):
            raise ValueError(f"Unexpected offer correction: {ident}")
        checks = review.get("checks", {})
        if (not review.get("evidence") or any(not e.get("sha256") or e.get("source_seconds") is None
                or e.get("native_height", 0) < 720 for e in review["evidence"])):
            raise ValueError(f"Missing native proof: {ident}")
        if review.get("status") == "verified_offer_correction":
            if (not review.get("corrected_offered_kinds") or not review.get("reason")
                    or not all(checks.get(k) is True for k in ("same_offer", "native_sequence_reviewed", "door_artwork_visible"))
                    or review.get("initial_content") != (row.get("outcome", {}).get("initial_content") if content_known(row) else None)
                    or review.get("selected_side") is not None):
                raise ValueError(f"Incomplete native offer-only correction: {ident}")
            result[ident] = review
            continue
        if review.get("status") == "route_unresolved":
            if (checks.get("native_sequence_reviewed") is not True or not review.get("reason")
                    or review.get("initial_content") != row.get("outcome", {}).get("initial_content")
                    or review.get("selected_side") is not None):
                raise ValueError(f"Unresolved route review claims unsupported new information: {ident}")
            result[ident] = review
            continue
        if review.get("status") == "content_conflict":
            if (review.get("initial_content") is not None or not review.get("reason")
                    or not review.get("evidence") or checks.get("native_sequence_reviewed") is not True):
                raise ValueError(f"Incomplete rejected-content review: {ident}")
            result[ident] = review
            continue
        if review.get("status") == "verified_content_correction":
            if review.get("initial_content") not in FLOOR_OBJECTS or not all(checks.get(k) is True for k in (
                    "native_sequence_reviewed", "actual_floor_object", "initial_object_before_interaction")):
                raise ValueError(f"Incomplete native content correction: {ident}")
            result[ident] = review
            continue
        if review.get("status") == "verified_resource_cost":
            if (kinds.count("locked") != 1 or review.get("selected_side") != kinds.index("locked")
                    or not content_known(row) or review.get("initial_content") not in FLOOR_OBJECTS
                    or type(review.get("keys_before")) is not int or type(review.get("keys_after")) is not int
                    or review["keys_before"]-review["keys_after"] != 1 or review["keys_after"] < 0
                    or not all(checks.get(k) is True for k in (
                        "same_offer", "actual_floor_object", "initial_object_before_interaction", "not_reward_popup",
                        "native_sequence_reviewed", "key_cost_visible_before_interaction"))):
                raise ValueError(f"Incomplete or conflicting key-cost evidence: {ident}")
            result[ident] = review
            continue
        if (kinds.count("monster") != 1 or "wall" in kinds or not content_known(row)
                or row["outcome"]["initial_content"] not in FLOOR_OBJECTS
                or review.get("initial_content") not in FLOOR_OBJECTS
                or not all(checks.get(key) is True for key in (
                    "same_offer", "actual_floor_object", "initial_object_before_interaction",
                    "not_reward_popup", "native_sequence_reviewed"))
                or not review.get("evidence")):
            raise ValueError(f"Incomplete floor-object exclusion evidence: {ident}")
        side = 1 - kinds.index("monster")
        if review.get("selected_side") != side:
            raise ValueError(f"Floor-object selection contradicts visible monster: {ident}")
        if any(not e.get("sha256") or e.get("source_seconds") is None
               or e.get("native_height", 0) < 720 for e in review["evidence"]):
            raise ValueError(f"Missing native proof: {ident}")
        result[ident] = review
    return result


def inventory_row(row, review=None):
    kinds = [d["kind"] for d in row["doors"]]
    initial = row["outcome"]["initial_content"] if content_known(row) else None
    if review:
        kinds = review.get("corrected_offered_kinds") or kinds
        initial = review["initial_content"]
    side = kind = basis = None
    if choice_known(row):
        side = row["chosen_side"]
        kind = kinds[side]
        basis = selection_basis(row)
    elif kinds[0] == kinds[1] and initial is not None:
        kind = kinds[0]
        basis = "both_sides_same_kind"
    elif kinds.count("wall") == 1 and initial is not None:
        side = 1 - kinds.index("wall")
        kind = kinds[side]
        basis = "only_traversable_alternative"
    elif review is not None and review["status"] in ("verified_floor_object", "verified_resource_cost"):
        side = review["selected_side"]
        kind = kinds[side]
        basis = ("verified_key_cost_before_object_interaction" if review["status"] == "verified_resource_cost"
                 else "reviewed_floor_object_excludes_visible_monster")
    status = ("attributed" if kind and initial else "route_known_content_unknown" if kind
              else "route_unknown_content_known" if initial else "route_and_content_unknown")
    return {"observation_id": row["id"], "source_id": row["source_id"],
            "source_seconds": row.get("source_seconds"), "context": row["context"],
            "generation": row["generation"], "offered_kinds": kinds,
            "pair_family": "/".join(sorted(family(k) for k in kinds)),
            "initial_content": initial, "initial_content_known": initial is not None,
            "recorded_following_scene": row.get("outcome", {}).get("following_scene_observation"),
            "selected_side": side, "selected_kind": kind, "selection_basis": basis,
            "other_choice": kinds[1-side] if side is not None else kinds[0] if kind else None,
            "entry_link_status": status,
            "original_choice_status": row.get("chosen_side_status"),
            "entry_evidence": row.get("outcome", {}).get("evidence", []) if initial else [],
            "new_review": review}


def route_stats(rows, kind):
    offered = [r for r in rows if kind in r["offered_kinds"]]
    selected = [r for r in offered if r["selected_kind"] == kind]
    counted = [r for r in selected if r["initial_content_known"]]
    contents = Counter(r["initial_content"] for r in counted)
    unresolved = [r for r in offered if r["selected_kind"] is None]
    return {"offered_pairs": len(offered), "selected_entries": len(selected),
            "selected_with_known_initial_content": len(counted),
            "selected_content_unknown": len(selected)-len(counted),
            "selected_other_kind": sum(r["selected_kind"] not in (None, kind) for r in offered),
            "selection_unknown": len(unresolved), "initial_contents": dict(sorted(contents.items())),
            "content_fractions_of_observed_selected_entries": {
                key: value/len(counted) for key, value in sorted(contents.items())},
            "selection_bases": dict(sorted(Counter(r["selection_basis"] for r in counted).items())),
            "unlinked_following_contents": dict(sorted(Counter(r["initial_content"] for r in unresolved
                                                               if r["initial_content_known"]).items()))}


def summary(rows):
    alternatives = defaultdict(list)
    for row in rows:
        if "mystery" in row["offered_kinds"]:
            kinds = row["offered_kinds"]
            other = kinds[1-kinds.index("mystery")]
            alternatives[other].append(row)
    return {"eligible_offers": len(rows),
            "entry_link_status": dict(sorted(Counter(r["entry_link_status"] for r in rows).items())),
            "known_initial_contents": dict(sorted(Counter(r["initial_content"] for r in rows
                                                           if r["initial_content_known"]).items())),
            "selection_bases": dict(sorted(Counter(r["selection_basis"] for r in rows
                                                  if r["selection_basis"]).items())),
            "mystery_by_exact_alternative": {other: route_stats(members, "mystery")
                                            for other, members in sorted(alternatives.items())},
            "physical_door_by_exact_style": {kind: route_stats(rows, kind) for kind in sorted(PHYSICAL)}}


def analyze(observations, reviews=()):
    original_counts = observation_counts(observations)
    by_id = {row["id"]: row for row in observations}
    accepted = validated_reviews(reviews, by_id)
    corrected = []
    for row in observations:
        review = accepted.get(row["id"])
        if review and review.get("corrected_offered_kinds"):
            row = deepcopy(row)
            for door, kind in zip(row["doors"], review["corrected_offered_kinds"]):
                door["kind"] = kind
            row["verified_fields"] = sorted(set(row.get("verified_fields", [])) | {"doors"})
        corrected.append(row)
    counts = observation_counts(corrected)
    rows = [inventory_row(normalize_generation(row), accepted.get(row["id"]))
            for row in corrected if observation_counts([row])["coverage"].get("eligible_decisions")]
    sources = defaultdict(list)
    contexts = defaultdict(list)
    for row in rows:
        sources[row["source_id"]].append(row)
        context = {"source_id": row["source_id"], **{key: row["context"].get(key) for key in CONTEXT_FIELDS},
                   "gems": sorted(set(row["context"]["gems"]))}
        contexts[json.dumps(context, sort_keys=True)].append(row)
    report = {"schema": 1, "coverage": counts["coverage"], "original_coverage": original_counts["coverage"],
              "newly_eligible_from_verified_door_resolution": counts["coverage"].get("eligible_decisions", 0)-original_counts["coverage"].get("eligible_decisions", 0),
              "new_native_reviews": len(accepted),
              "new_native_route_links": sum(r["status"] in ("verified_floor_object", "verified_resource_cost") for r in accepted.values()),
              "new_native_key_cost_links": sum(r["status"] == "verified_resource_cost" for r in accepted.values()),
              "new_native_routes_still_unresolved": sum(r["status"] in ("route_unresolved", "verified_content_correction") for r in accepted.values()),
              "native_content_conflicts": sum(r["status"] == "content_conflict" for r in accepted.values()),
              "native_content_corrections": sum(r["status"] != "content_conflict" and
                  r["initial_content"] != r["expected_initial_content"] for r in accepted.values()),
              "native_offer_corrections": sum(bool(r.get("corrected_offered_kinds")) for r in accepted.values()),
              "all_offers_descriptive": summary(rows),
              "by_source": {source: summary(members) for source, members in sorted(sources.items())},
              "baseline_no_stones": summary([r for r in rows if not r["context"]["gems"]]),
              "stone_specific_exposures": {
                  stone: summary([r for r in rows if stone in r["context"]["gems"]])
                  for stone in ("spying", "lodestone", "devil")},
              "fully_conditioned_strata": [{"context": json.loads(context), **summary(members)}
                                            for context, members in sorted(contexts.items())],
              "calibrated": False,
              "limitations": ["This inventories every eligible ledger offer, not every frame of every VOD; missed offers can remain.",
                  "Following interiors without a verified route are retained separately, never assigned by hover or expected odds.",
                  "A reviewed persistent floor object can exclude the visible-monster alternative; empty rooms and reward popups cannot.",
                  "Both-identical choices establish route kind only; wall exclusion requires a verified entered room.",
                  "Recorded contents and any new native reviews have different provenance; no classifier suggestions are counted.",
                  "The new floor-object review cohort deliberately targets non-monster objects beside visible monsters; its content mix cannot establish a living-monster rate.",
                  "The key-cost cohort reviews all 260 locked/mystery offers with previously verified floor objects. Only a visible one-key loss before object interaction establishes a new locked-door route; unchanged counters, uncertain timing and unresolved two-key modifiers stay unknown.",
                  "Physical-door content counts are strongly biased toward non-monster entries because both new linkage cohorts were selected for known floor objects. Zero counted monsters is not evidence of zero monster probability. This bias remains in no-stone and other context splits; none of these physical-door fractions is a calibrated encounter rate.",
                  "Selected-room proportions reflect player selection and review coverage, not unconditional generated room probabilities.",
                  "Stone-specific exposures include coexisting stones and can overlap; full source/version/stage/stone contexts remain separate."]}
    return rows, report


def render(report):
    total = report["all_offers_descriptive"]
    lines = ["# Full VOD entry linkage audit — 2026-10-08", "",
             f"All **{total['eligible_offers']:,} eligible ordinary offers** from all eight source ledgers are included. "
             f"This pass checks **{report['new_native_reviews']} native offer/entry reviews**, adds **{report['new_native_route_links']} route links**, "
             f"and rejects **{report['native_content_conflicts']} stale content label(s)**. Unknown routes and contents remain explicit.", "",
             "## Coverage", "", "| Source | Offers | Linked entry + content | Known content, route unknown | Known route, content unknown | Both unknown |", "|---|---:|---:|---:|---:|---:|"]
    for source, group in report["by_source"].items():
        statuses = group["entry_link_status"]
        lines.append(f"| {source} | {group['eligible_offers']} | {statuses.get('attributed', 0)} | {statuses.get('route_unknown_content_known', 0)} | {statuses.get('route_known_content_unknown', 0)} | {statuses.get('route_and_content_unknown', 0)} |")
    lines += ["", "## Mystery entries by exact other choice", "",
              "Counts below are observed selected entries across contexts, not fitted population odds. See JSON for no-stone and exact source/stage/stone strata.", "",
              "| Other choice | Offered | Linked contents | Route unknown | Observed initial contents |", "|---|---:|---:|---:|---|"]
    for other, stats in total["mystery_by_exact_alternative"].items():
        content = ", ".join(f"{key}: {value}" for key, value in stats["initial_contents"].items()) or "—"
        lines.append(f"| {other} | {stats['offered_pairs']} | {stats['selected_with_known_initial_content']} | {stats['selection_unknown']} | {content} |")
    lines += ["", "## Physical doors by exact style", "",
              "**These counts cannot estimate physical-door monster odds.** Both new linkage cohorts deliberately select known floor objects. Monster entries with unresolved action timing remain unlinked; a zero in the table is not a zero-probability finding. This limitation also applies to no-stone and fully conditioned JSON subsets.", "",
              "Spying Gem unlocked doors and Lodestone double-locked doors have separate exposure cohorts in the JSON. They are never blended into no-stone rates.", "",
              "| Style | Offered | Linked contents | Route unknown | Observed initial contents |", "|---|---:|---:|---:|---|"]
    for kind, stats in total["physical_door_by_exact_style"].items():
        content = ", ".join(f"{key}: {value}" for key, value in stats["initial_contents"].items()) or "—"
        lines.append(f"| {kind} | {stats['offered_pairs']} | {stats['selected_with_known_initial_content']} | {stats['selection_unknown']} | {content} |")
    lines += ["", "## Interpretation", ""] + [f"- {line}" for line in report["limitations"]]
    return "\n".join(lines)+"\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, default=Path("data/vods/observations-2026-10-08.jsonl"))
    parser.add_argument("--reviews", type=Path, default=Path("data/vods/full-entry-native-reviews-2026-10-08.jsonl"))
    parser.add_argument("--inventory", type=Path, default=Path("data/vods/full-entry-linkage-2026-10-08.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("docs/vod-full-entry-audit-2026-10-08.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/vod-full-entry-audit-2026-10-08.md"))
    args = parser.parse_args(argv)
    rows, report = analyze(read_rows(args.observations), read_rows(args.reviews) if args.reviews else [])
    report["inputs"] = {"observations_sha256": digest(args.observations),
                        "native_reviews_sha256": digest(args.reviews) if args.reviews else None}
    for path in (args.inventory, args.report, args.markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.inventory.write_text("".join(json.dumps(row, separators=(",", ":"))+"\n" for row in rows), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    args.markdown.write_text(render(report), encoding="utf-8")
    print(json.dumps({"offers": len(rows), "new_native_reviews": report["new_native_reviews"],
                      "entry_link_status": report["all_offers_descriptive"]["entry_link_status"]}))
