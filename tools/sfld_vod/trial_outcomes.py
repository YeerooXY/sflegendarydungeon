"""Validate and summarize manually reviewed Legendary Dungeon trial attempts.

One row represents an attempt, not a gate, video frame or reward component.
Only complete, verified prize-chest contents enter chest-frequency denominators.
Deaths, uncertain entry, missing rewards and unknown item rarity remain distinct.
This module does not update door observations or simulation probabilities.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .review import wilson

REQUIRED = {"identity", "trial_entry", "depth", "termination", "reward_contents"}
KINDS = {"gold", "arcane_splinters", "metal", "wood", "stone", "souls",
         "fruit", "hourglass", "item", "other", "unknown"}
RARITIES = {None, "normal", "epic", "legendary"}
CONTEXT_FIELDS = ("event_id", "version_cohort", "client_build", "mode", "tier",
                  "stage", "character_level", "stone_status", "gems", "buffs")


def validate_attempt(row):
    """Reject impossible bookkeeping without filling any unknown observations."""
    def require(condition, message):
        if not condition:
            raise ValueError(f"{row.get('id', '<missing id>')}: {message}")

    require(row.get("schema") == 1, "expected trial outcome schema 1")
    for key in ("id", "source_id", "run_id", "initial_observation_id"):
        require(isinstance(row.get(key), str) and bool(row[key]), f"missing {key}")
    interval = row.get("source_interval", [])
    require(len(interval) == 2 and all(type(t) in (int, float) for t in interval)
            and 0 <= interval[0] < interval[1], "invalid source interval")
    require(row.get("entry_status") in {"entered", "unknown", "not_entered"}, "invalid entry status")
    require(row.get("termination") in {"completed", "cashed_out", "death", "unknown"}, "invalid termination")
    entered, completed = row.get("entered_depth"), row.get("completed_depth")
    require(entered is None or type(entered) is int and 1 <= entered <= 5, "invalid entered depth")
    require(completed is None or type(completed) is int and 0 <= completed <= 5, "invalid completed depth")
    if row["entry_status"] != "entered":
        require(entered is None and completed is None, "unproved entry cannot have verified trial depth")
        require("trial_entry" not in row.get("verified_fields", []), "unproved entry marked verified")
    else:
        if entered is not None and completed is not None:
            require(completed <= entered, "completed depth exceeds entered depth")
        if row["termination"] == "completed":
            require(entered == completed == 5, "completion requires five survived gates")
        elif row["termination"] == "cashed_out":
            require(entered == completed and completed is not None and 1 <= completed < 5,
                    "cashout requires one to four completed gates, without entering the next")
        elif row["termination"] == "death" and entered is not None and completed is not None:
            require(entered == completed + 1, "death must distinguish fatal gate from survived gates")
    require(isinstance(row.get("context"), dict), "missing context")
    require(isinstance(row.get("verified_fields"), list), "missing verified fields")
    reward = row.get("reward", {})
    require(reward.get("state") in {"observed", "unobserved", "not_awarded"}, "invalid reward state")
    require(type(reward.get("complete")) is bool, "reward completeness must be explicit")
    require(isinstance(reward.get("components"), list), "missing reward components")
    if reward["state"] != "observed":
        require(not reward["components"] and not reward["complete"], "unobserved reward must not be zero-filled")
    if reward["state"] == "not_awarded":
        require(row["termination"] == "death", "no-prize assertion requires observed death")
    if reward["complete"]:
        require(bool(reward["components"]), "complete observed chest needs its contents")
        require(all(c.get("kind") != "unknown" for c in reward["components"]), "complete chest has unknown content")
    for component in reward["components"]:
        require(component.get("kind") in KINDS, "invalid reward kind")
        quantity = component.get("quantity")
        require(quantity is None or type(quantity) is int and quantity > 0, "quantity must be positive or unknown")
        require(component.get("rarity") in RARITIES, "invalid item rarity")
        if component["kind"] != "item":
            require(component.get("rarity") is None, "resource has item rarity")
        if quantity is not None:
            require(component.get("quantity_verified") is True, "unverified quantity must remain null")
    evidence = row.get("evidence", [])
    require(bool(evidence), "missing evidence")
    for item in evidence:
        digest = item.get("sha256", "")
        require(isinstance(item.get("image"), str) and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest), "invalid evidence hash/path")
        require(type(item.get("source_seconds")) in (int, float), "evidence lacks source time")
        require(bool(item.get("role")), "evidence lacks role")
    return row


def verify_evidence(rows, root):
    """Resolve repo-relative evidence and reject stale or out-of-root files."""
    root = Path(root).resolve()
    checked = set()
    for row in rows:
        for item in row["evidence"]:
            path = (root / item["image"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Evidence path escapes the supplied root")
            key = (path, item["sha256"])
            if key in checked:
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(f"Evidence hash mismatch: {path}")
            checked.add(key)
    return len(checked)


def _frequency(successes, total):
    return wilson(successes, total) if total else {
        "successes": successes, "opportunities": 0, "estimate": None, "wilson_95": None}


def _summary(rows):
    entered = [r for r in rows if r["entry_status"] == "entered"
               and {"identity", "trial_entry"} <= set(r["verified_fields"])]
    chests = [r for r in entered if REQUIRED <= set(r["verified_fields"])
              and r["termination"] in {"completed", "cashed_out"}
              and r["reward"]["state"] == "observed" and r["reward"]["complete"]]
    kinds = Counter(c["kind"] for r in chests for c in r["reward"]["components"])
    frequencies = {}
    for kind in sorted(KINDS - {"unknown"}):
        frequencies[kind] = _frequency(sum(any(c["kind"] == kind for c in r["reward"]["components"])
                                           for r in chests), len(chests))
    for rarity in ("epic", "legendary"):
        yes = unknown = 0
        for row in chests:
            items = [c for c in row["reward"]["components"] if c["kind"] == "item"]
            if any(c.get("rarity") == rarity for c in items):
                yes += 1
            elif any(c.get("rarity") is None for c in items):
                unknown += 1
        frequencies[rarity] = _frequency(yes, len(chests) - unknown)
        frequencies[rarity]["unknown_rarity_chests_excluded"] = unknown
    return {
        "attempt_records": len(rows), "confirmed_entries": len(entered),
        "unproved_entry_records": sum(r["entry_status"] != "entered" for r in rows),
        "verified_terminations": dict(Counter(r["termination"] for r in entered
                                                if "termination" in r["verified_fields"])),
        "eligible_prize_chests": len(chests),
        "ineligible_confirmed_attempt_ids": [r["id"] for r in entered if r not in chests],
        "component_counts": dict(kinds),
        "observed_chest_fractions": frequencies,
        "prize_chest_ids": [r["id"] for r in chests],
        "quantities": [{"attempt_id": r["id"], "components": r["reward"]["components"]} for r in chests],
    }


def trial_counts(rows):
    """Count confirmed attempts; partition source/version/mode/depth/stone context."""
    rows = list(rows)
    ids, identities = set(), set()
    groups, sources, depths = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        validate_attempt(row)
        identity = (row["source_id"], row["run_id"], row["initial_observation_id"])
        if row["id"] in ids or identity in identities:
            raise ValueError("Duplicate trial attempt identity; do not count frames or retries twice")
        ids.add(row["id"]); identities.add(identity)
        context = {key: row["context"].get(key) for key in CONTEXT_FIELDS}
        if context["gems"] is not None:
            context["gems"] = sorted(context["gems"])
        context.update(source_id=row["source_id"], completed_depth=row["completed_depth"],
                       entered_depth=row["entered_depth"], termination=row["termination"])
        groups[json.dumps(context, sort_keys=True)].append(row)
        sources[row["source_id"]].append(row)
        depths[str(row["completed_depth"])].append(row)
    return {
        "schema": 1,
        "interpretation": "Descriptive counts of a bounded convenience sample, not game probability calibration. "
                          "Wilson intervals assume exchangeable independent draws; mixed accounts, modes, stones, "
                          "levels and repeated streamer sessions do not establish those assumptions. "
                          "Death is not a prize-chest draw. Unknown entry, contents and rarity are never negatives.",
        "pooled_descriptive_only": _summary(rows),
        "by_source": {key: _summary(value) for key, value in sorted(sources.items())},
        "by_completed_depth_descriptive_only": {key: _summary(value) for key, value in sorted(depths.items())},
        "strata": [{"context": json.loads(key), **_summary(value)} for key, value in sorted(groups.items())],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL trial attempt records")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, help="Also verify local evidence hashes")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = trial_counts(rows)
    if args.evidence_root:
        result["evidence_files_verified"] = verify_evidence(rows, args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
