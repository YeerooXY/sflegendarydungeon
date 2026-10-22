"""Combine manually reviewed stone menus; validate continuity and count reoffers.

This checks the evidence ledger, not image recognition. Screens were read by
reviewers. Run from the repository root with cached footage evidence available.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence(row):
    return {**row, "path": row.get("path", row.get("image")),
            "sha256": row.get("sha256", row.get("image_sha256"))}


def normalize(directory):
    chains, inputs = [], []
    for name in ("adaptive_gems.json", "survival.json", "shop.json"):
        path = directory / name
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        inputs.append({"path": path.as_posix(), "sha256": digest(path)})
        for chain in doc["chains"]:
            run_id = chain.get("run_id", chain.get("observed_run_id"))
            source = chain["source_id"]
            rows = chain.get("offers")
            if rows is None:
                rows = [r for r in doc["records"]
                        if (r["source_id"], r["run_id"]) == (source, run_id)]
            offers = []
            for row in rows:
                room = row.get("boss_room", row.get("after_room"))
                offered = row.get("ordered_offers_left_to_right",
                                  row.get("offered_ordered_left_to_right", row.get("offers")))
                offer_image = row.get("offer_image", row.get("offer_evidence"))
                chosen_image = row.get("choice_confirmation_image",
                                       row.get("selected_hud_evidence", row.get("held_after_evidence")))
                offers.append({
                    "selection_index": row.get("selection_index", room // 25),
                    "boss_room": room, "source_seconds": row["source_seconds"],
                    "offered": offered, "chosen": row.get("chosen_stone", row.get("chosen")),
                    "held_before": row["held_before"], "held_after": row["held_after"],
                    "offer_evidence": evidence(offer_image),
                    "chosen_evidence": evidence(chosen_image),
                    "video": row.get("video", row.get("clip_path", offer_image.get("video"))),
                })
            chains.append({
                "source_id": source, "source_url": rows[0].get("source_url", chain.get("source_url")),
                "run_id": run_id, "cohort": chain.get("cohort", "current_2026"),
                "actual_event_run_number": None,
                "continuity": chain.get("run_linkage", chain.get("linkage", rows[0].get("continuity_rationale"))),
                "review_input": path.as_posix(), "offers": sorted(offers, key=lambda x: x["selection_index"]),
            })
    return chains, inputs, doc["next_run_reset_example"]


def validate(chains, check_images=True):
    identities, frames, image_hashes, verified = set(), set(), set(), set()
    totals = {}
    for chain in chains:
        identity = (chain["source_id"], chain["run_id"])
        if identity in identities:
            raise ValueError(f"Duplicate run: {identity}")
        identities.add(identity)
        rows = chain["offers"]
        if [r["boss_room"] for r in rows] != [25, 50, 75] or [r["selection_index"] for r in rows] != [1, 2, 3]:
            raise ValueError(f"Incomplete or misordered chain: {identity}")
        seen, held, rejected_first = set(), set(), set()
        repeats, first_repeats = [], []
        previous_time = -1
        for row in rows:
            offered = set(row["offered"])
            if len(row["offered"]) != 3 or len(offered) != 3 or row["chosen"] not in offered:
                raise ValueError(f"Invalid offer/choice: {identity}")
            if set(row["held_before"]) != held or set(row["held_after"]) != held | {row["chosen"]}:
                raise ValueError(f"Broken held-stone continuity: {identity}")
            if row["source_seconds"] <= previous_time:
                raise ValueError(f"Non-increasing time: {identity}")
            frame = (chain["source_id"], row["source_seconds"])
            sha = row["offer_evidence"]["sha256"]
            if frame in frames or sha in image_hashes:
                raise ValueError(f"Reused offer frame: {frame}")
            frames.add(frame)
            image_hashes.add(sha)
            for item in (row["offer_evidence"], row["chosen_evidence"]):
                if check_images and digest(item["path"]) != item["sha256"]:
                    raise ValueError(f"Image hash mismatch: {item['path']}")
                verified.add(item["path"])
            repeats.extend(sorted(offered & seen))
            first_repeats.extend(sorted(offered & rejected_first))
            if row["selection_index"] == 1:
                rejected_first = offered - {row["chosen"]}
            seen.update(offered)
            held.add(row["chosen"])
            previous_time = row["source_seconds"]
        chain["result"] = {"distinct_stones": len(seen), "cross_offer_repeats": repeats,
                           "first_rejected_reappeared": first_repeats}
        total = totals.setdefault(chain["cohort"], Counter())
        total.update(complete_chains=1, selection_screens=3, offered_slots=9,
                     chains_with_nine_distinct_stones=int(len(seen) == 9),
                     chains_with_any_repeat=int(bool(repeats)),
                     chains_with_first_rejected_reappearance=int(bool(first_repeats)))
    return {"cohorts": totals, "complete_chains": len(identities), "selection_screens": len(frames),
            "unique_evidence_images": len(verified), "image_hashes_checked": check_images}


def gallery(chains, path):
    esc = html.escape
    parts = ['<!doctype html><meta charset="utf-8"><title>Stone offer audit</title>',
             '<style>body{font:16px system-ui;margin:2em;background:#17212b;color:#eee} '
             'section{margin:2em 0} .row{display:flex;gap:1em} figure{margin:0;flex:1;min-width:0} '
             'img{width:100%} a{color:#9de} figcaption{padding:.5em 0}</style>',
             '<h1>Stone offer audit — 2026-10-10</h1><p>Each row is one observed run; '
             'columns are offers after rooms 25, 50 and 75. Click for native frames. '
             'Observed run labels do not establish the first run of the event.</p>']
    for chain in chains:
        parts.append(f'<section><h2>{esc(chain["source_id"])} / {esc(chain["run_id"])} '
                     f'({esc(chain["cohort"])})</h2><div class="row">')
        for row in chain["offers"]:
            link = esc(os.path.relpath(row["offer_evidence"]["path"], path.parent).replace("\\", "/"), quote=True)
            names = esc(" / ".join(row["offered"]))
            parts.append(f'<figure><a href="{link}"><img loading="lazy" src="{link}"></a>'
                         f'<figcaption>Room {row["boss_room"]}, {row["source_seconds"]:.3f}s: '
                         f'{names}. Chosen: {esc(row["chosen"])}.</figcaption></figure>')
        parts.append('</div></section>')
    path.write_text("\n".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("out/stone-offer-reuse-2026-10-10"))
    parser.add_argument("--output", type=Path, default=Path("docs/vod-stone-reoffers-2026-10-10.json"))
    args = parser.parse_args()
    chains, inputs, reset = normalize(args.input)
    validation = validate(chains)
    for item in reset["evidence"]:
        if digest(item["image"]) != item["image_sha256"]:
            raise ValueError(f"Reset evidence hash mismatch: {item['image']}")
    report = {"schema": "sfld-stone-reoffer-audit-v1", "review_date": "2026-10-10",
              "method": "Manual image review, cumulative chosen-stone HUD continuity, source-local run grouping; programmatic deduplication and image hash checks.",
              "timing_note": "VOD timestamps are approximate around stream-copy keyframes; exact saved image hashes identify reviewed evidence.",
              "limitations": ["Convenience sample of complete visible chains; no random population sample or independence assumption.",
                              "2024 and 2026 footage are reported separately.",
                              "No event-first-run classifications inferred from first observed passage.",
                              "Zero observed repeats supports exclusion; does not prove a universal rule or identify server implementation."],
              "inputs": inputs, "validation": validation, "chains": chains,
              "next_run_reset_example": reset,
              "engine_changed": False, "policy_retrained": False}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    gallery(chains, args.input / "index.html")
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
