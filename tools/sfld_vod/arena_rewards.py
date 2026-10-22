"""Audit a complete canonical arena inventory without treating missing rewards as zero."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .trial_outcomes import trial_counts, validate_attempt, verify_evidence


def audit_arena(rows, expected_ids=None):
    rows = list(rows)
    covered = set()
    for row in rows:
        validate_attempt(row)
        links = row.get("covered_observation_ids", [])
        if not links or len(set(links)) != len(links) or covered.intersection(links):
            raise ValueError("Arena menu observations must belong to exactly one sequence")
        covered.update(links)
        for component in row["reward"]["components"]:
            if component.get("origin") == "item_sale":
                raise ValueError("Selling a prize item does not create a second chest reward")
            if component.get("rarity") == "legendary":
                proof = component.get("rarity_evidence", {})
                if not proof.get("mapping") or proof.get("frame") not in {
                        e["image"] for e in row["evidence"]}:
                    raise ValueError("Legendary rarity requires a linked positive frame and mapping")
    if expected_ids is not None and covered != set(expected_ids):
        raise ValueError(f"Arena inventory mismatch: {len(set(expected_ids)-covered)} missing, "
                         f"{len(covered-set(expected_ids))} unexpected observations")
    report = trial_counts(rows)
    report["coverage"] = {
        "sequence_records": len(rows), "canonical_menu_observations": len(covered),
        "source_count": len({r["source_id"] for r in rows}),
        "all_expected_menu_rows_accounted_for": expected_ids is not None,
        "scope": "Every canonical trial/exit-trial menu row in eight sources is assigned once. "
                 "This is complete review of the recognized arena inventory, not proof that "
                 "the scene detector found every arena in all footage.",
        "menu_rows_by_source": dict(Counter(r["source_id"] for r in rows
                                           for _ in r["covered_observation_ids"])),
    }
    report["by_version_cohort"] = {
        cohort: trial_counts(r for r in rows if r["context"].get("version_cohort") == cohort)
        ["pooled_descriptive_only"]
        for cohort in sorted({r["context"].get("version_cohort") for r in rows})}
    current_five = [r for r in rows if r["context"].get("version_cohort") ==
                    "2026-09-event-after-2024-05-trial-fixes" and r["completed_depth"] == 5]
    report["current_event_five_gate_chests_descriptive_only"] = trial_counts(current_five)[
        "pooled_descriptive_only"]
    report["missing_rewards"] = [r["id"] for r in rows if r["entry_status"] == "entered"
                                 and r["termination"] in {"completed", "cashed_out"}
                                 and not r["reward"]["complete"]]
    report["legendary_evidence"] = [{"attempt_id": r["id"], "source_url": r["source_url"],
                                      "name": c.get("name"), **c["rarity_evidence"]}
                                     for r in rows for c in r["reward"]["components"]
                                     if c.get("rarity") == "legendary"]
    return report


def canonical_inventory(rows, root):
    expected, sources = set(), []
    for name in sorted({r["provenance"]["canonical"] for r in rows}):
        path = root / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
                   if line.strip()]
        ids = {r["id"] for r in records if r.get("status") == "decision"
               and any(d.get("kind") in {"trial", "exit_trial"} for d in r.get("doors", []))}
        expected.update(ids)
        sources.append({"canonical": name, "sha256": digest, "menu_rows": len(ids)})
    return expected, sources


def markdown(report):
    summary = report["pooled_descriptive_only"]
    current = report["current_event_five_gate_chests_descriptive_only"]
    legendary = current["observed_chest_fractions"]["legendary"]
    bounds = legendary["wilson_95"]
    lines = ["# Full recognized arena inventory — 2026-10-08", "",
             report["coverage"]["scope"], "",
             f"Reviewed **{report['coverage']['canonical_menu_observations']} menu observations** "
             f"across **{report['coverage']['source_count']} sources**, deduplicated into "
             f"**{summary['attempt_records']} sequences**. "
             f"{summary['confirmed_entries']} confirm arena entry; "
             f"{summary['unproved_entry_records']} do not establish entry/identity.", "",
             "| Confirmed result | Count |", "|---|---:|"]
    lines += [f"| {name.replace('_', ' ')} | {count} |" for name, count in
              summary["verified_terminations"].items()]
    lines += ["", f"**{summary['eligible_prize_chests']} complete prize contents** were readable. "
              f"{len(report['missing_rewards'])} further confirmed chest has unresolved contents. "
              "Deaths and unknown rewards are excluded from prize frequencies, not counted as empty chests.",
              "", "| Contents seen | Chests |", "|---|---:|"]
    for name in ("gold", "arcane_splinters", "metal", "item", "epic", "legendary"):
        fraction = summary["observed_chest_fractions"][name]
        lines.append(f"| {name.replace('_', ' ')} | {fraction['successes']} |")
    lines += ["", "Item categories overlap: epic and legendary are subsets of item chests. "
              "Multi-item chests count once. Gold from selling an awarded item is excluded. "
              "Six historical item rarities in two Ultimate chests remain unknown.", "",
              f"For the current event's five-gate chests, **{legendary['successes']} / "
              f"{legendary['opportunities']}** are confirmed legendary "
              f"(**{100*legendary['estimate']:.2f}%**). "
              f"The descriptive Wilson 95% interval is {100*bounds[0]:.1f}–{100*bounds[1]:.1f}%. "
              "These are streamer convenience samples, with repeated players and selected survival, "
              "not independent randomized estimates. No simulator probability was changed.", "",
              "| Legendary item | Evidence |", "|---|---|"]
    for item in report["legendary_evidence"]:
        lines.append(f"| {item.get('name') or 'Name unresolved'} | [{item['attempt_id']}]"
                     f"({item['source_url']}) |")
    lines += ["", "Legendary classification uses a readable orange **equipment name**, linked to "
              "the installed client's `legendaryEquipmentName` color. "
              "The [client evidence record](vod-item-rarity-evidence-2026-10-08.json) gives exact "
              "asset/object hashes and field offsets. Chest glow, sell price, rune quality, "
              "and the gold-colored reward heading do not establish rarity.", "",
              "| Source | Menu rows | Sequence records | Confirmed entries | Readable chests |",
              "|---|---:|---:|---:|---:|"]
    for sid, data in report["by_source"].items():
        lines.append(f"| {sid} | {report['coverage']['menu_rows_by_source'][sid]} | "
                     f"{data['attempt_records']} | {data['confirmed_entries']} | "
                     f"{data['eligible_prize_chests']} |")
    lines += ["", "The historical March 2024 Ultimate footage is separated from the September 2026 "
              "event by source and version; known complete stone sets remain in the JSON strata. "
              "Unknown mode, level, buffs and scaling remain null. Two historical chests awarded "
              "three items, including a tier-one cashout. One embedded other-player cutaway is "
              "retained but excluded because identity, full stones and payout are unresolved.", "",
              "Frames are native 720p gameplay crops sampled at 4 fps around recognized transitions, "
              "with longer follow-up through paused chest openings. Each selected proof carries "
              "its timestamp and SHA-256 hash; original offer/stone evidence is linked. "
              "Stream-copy section offsets can introduce several seconds of timestamp uncertainty. "
              "Nine fatal first-choice sequences cannot distinguish the arena door from the "
              "ordinary monster using hover arrows alone.", "",
              "The ledger extends the prior 19 reviewed attempts; those attempts are reused once. "
              "The JSON report includes source coverage, exact quantities, missing outcomes, "
              "per-tier results and source/version/mode/stone strata.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--verify-evidence", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.ledger.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]
    expected, sources = canonical_inventory(rows, args.root.resolve())
    report = audit_arena(rows, expected)
    report["canonical_sources"] = sources
    report["dataset"] = {"path": args.ledger.as_posix(),
                         "sha256": hashlib.sha256(args.ledger.read_bytes()).hexdigest()}
    if args.verify_evidence:
        report["evidence_files_verified"] = verify_evidence(rows, args.root)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"coverage": report["coverage"], "evidence_files_verified":
                      report.get("evidence_files_verified"), "missing_rewards": report["missing_rewards"]}))


if __name__ == "__main__":
    main()
