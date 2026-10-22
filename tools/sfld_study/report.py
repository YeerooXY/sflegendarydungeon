"""Anonymous exports and descriptive reports; never export the private database."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics

from .schema import StudyError, action, collector_files, observation, timestamp


def elapsed(row, at):
    return None if not row["start_utc"] else (timestamp(at) - timestamp(row["start_utc"])).total_seconds()


def transitions(store):
    for record in store.db.execute("""SELECT d.*, b.data AS before_data, a.data AS after_data,
        a.at_utc AS after_utc FROM decisions d JOIN observations b ON b.id=d.before_id
        LEFT JOIN observations a ON a.id=d.after_id ORDER BY d.subject,d.sequence"""):
        subject = store.subject(record["subject"])
        before = observation(json.loads(record["before_data"]))
        after = None if record["after_data"] is None else observation(json.loads(record["after_data"]))
        before, after, annotation_count = store.reviewed(record["id"], before, after)
        yield {"schema_version": 2, "evidence": store.meta("evidence"), "subject": record["subject"],
               "decision": record["id"], "annotation_count": annotation_count,
               "run": record["run"], "block": subject["block"], "sequence": record["sequence"], "arm": subject["arm"],
               "policy": subject["policy"], "policy_sha256": subject["policy_hash"],
               "at_seconds": elapsed(subject, record["at_utc"]),
               "after_seconds": None if record["after_utc"] is None else elapsed(subject, record["after_utc"]),
               "before": before, "action": action(json.loads(record["chosen"])), "after": after,
               "outcome_status": record["state"], "mushroom_quote": record["quote"],
               "mushrooms_deducted": record["charged"], "mushroom_balance_delta": record["net_delta"],
               "reserved_mushrooms": record["reserved"], "spending_category": record["category"],
               "damage_censored_at_death": bool(after is not None and after["hp_current"] == 0
                   and before["hp_current"] is not None and before["hp_current"] > 0)}


def report(store):
    groups = defaultdict(list)
    subjects = []
    for row in store.db.execute("SELECT * FROM subjects ORDER BY id"):
        sid = row["id"]
        runs = [dict(r) for r in store.db.execute("SELECT * FROM runs WHERE subject=? ORDER BY ordinal", (sid,))]
        last = store.latest(sid)
        completed = [r for r in runs if r["state"] == "complete"]
        observed_full_first = next((r for r in runs if r["ordinal"] == 1 and not r["left_truncated"]
                                    and r["state"] == "complete"), None)
        first_seconds = None if observed_full_first is None else elapsed(row, observed_full_first["end_utc"])
        categories = {r[0]: r[1] for r in store.db.execute("SELECT category,SUM(charged) FROM decisions WHERE subject=? GROUP BY category", (sid,))}
        item = {"subject": sid, "block": row["block"], "arm": row["arm"], "policy": row["policy"], "policy_sha256": row["policy_hash"],
                "prior_completions": row["prior_completions"], "budget": row["budget"],
                "event_state": row["state"], "has_observations": last is not None,
                "completed_runs": len(completed), "unfinished_runs": sum(r["state"] != "complete" for r in runs),
                "left_truncated_runs": sum(r["left_truncated"] for r in runs),
                "first_completion_seconds": first_seconds, "mushrooms_spent": store.spent(sid)["charged"],
                "mushrooms_reserved": store.spent(sid)["reserved"],
                "recovery_mushrooms": categories.get("recovery") or 0,
                "reroll_mushrooms": categories.get("reroll") or 0,
                "last_observed_room": None if last is None else last["data"]["room"],
                "last_observed_seconds": None if last is None else elapsed(row, last["at_utc"]),
                "deadline_seconds": None if not row["start_utc"] else elapsed(row, row["end_utc"]),
                "closed_at_deadline": row["state"] == "closed" and row["reason"] == "deadline",
                "unresolved_actions": len(store.unresolved(sid))}
        subjects.append(item)
        groups[(row["arm"], row["policy"], row["policy_hash"])].append(item)
    summaries = []
    for (arm, policy, sha), members in groups.items():
        observed = [m for m in members if m["has_observations"]]
        full_events = [m for m in observed if m["closed_at_deadline"] and not m["left_truncated_runs"]
                       and not m["unresolved_actions"]]
        counts = [m["completed_runs"] for m in full_events]
        spend = sum(m["mushrooms_spent"] for m in full_events)
        complete = sum(counts)
        first = [m["first_completion_seconds"] for m in full_events if m["first_completion_seconds"] is not None]
        summaries.append({"arm": arm, "policy": policy, "policy_sha256": sha, "assigned_characters": len(members),
                          "observed_characters": len(observed), "full_event_characters": len(full_events),
                          "completed_runs_so_far": sum(m["completed_runs"] for m in observed),
                          "mean_completions_per_full_event": statistics.mean(counts) if counts else None,
                          "mushrooms_per_completion_full_events": spend / complete if complete else None,
                          "conditional_first_completion_seconds": statistics.mean(first) if first else None,
                          "restricted_first_completion_seconds": statistics.mean([
                              m["first_completion_seconds"] if m["first_completion_seconds"] is not None
                              else m["deadline_seconds"] for m in full_events]) if full_events else None})
    return {"schema_version": 1, "evidence": store.meta("evidence"), "groups": summaries, "subjects": subjects,
            "interpretation": "Descriptive only. Open, interrupted and left-truncated events are excluded from full-event means. "
                              "Completions are observed completed dungeons, not legendary item drops. "
                              "Unknown charges remain reserved. Event/world clustering needs analysis before confidence claims."}


def export(store, destination):
    target = Path(destination).resolve()
    if target.exists():
        raise StudyError("Export destination must be a new directory.")
    target.mkdir(parents=True)
    # Use one read transaction for a consistent snapshot while another process
    # may be writing. No private_fields, absolute times, identities, seeds or
    # agent memory enter these files.
    store.db.execute("BEGIN")
    try:
        summary = report(store)
        with (target / "transitions.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in transitions(store):
                stream.write(json.dumps(row, allow_nan=False) + "\n")
        with (target / "observations.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in store.db.execute("SELECT subject,at_utc,data FROM observations ORDER BY id"):
                subject = store.subject(row["subject"])
                stream.write(json.dumps({"subject": row["subject"], "at_seconds": elapsed(subject, row["at_utc"]),
                    "evidence": store.meta("evidence"), "observation": observation(json.loads(row["data"]))}) + "\n")
        with (target / "transitions.csv").open("w", encoding="utf-8", newline="") as stream:
            columns = ["subject", "block", "run", "sequence", "arm", "policy", "at_seconds", "after_seconds", "outcome_status",
                       "mushroom_quote", "mushrooms_deducted", "mushroom_balance_delta", "spending_category",
                       "damage_censored_at_death", "before", "action", "after"]
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in transitions(store):
                flat = {k: row[k] for k in columns}
                for key in ("before", "action", "after"):
                    flat[key] = json.dumps(flat[key], separators=(",", ":"))
                writer.writerow(flat)
        (target / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        with (target / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["subject", "run", "ordinal", "start_seconds", "end_seconds",
                                                        "state", "initial_room", "last_room", "left_truncated"])
            writer.writeheader()
            for row in store.db.execute("SELECT * FROM runs ORDER BY subject,ordinal"):
                subject = store.subject(row["subject"])
                writer.writerow({"subject": row["subject"], "run": row["id"], "ordinal": row["ordinal"],
                    "start_seconds": elapsed(subject, row["start_utc"]),
                    "end_seconds": None if row["end_utc"] is None else elapsed(subject, row["end_utc"]),
                    "state": row["state"], "initial_room": row["initial_room"], "last_room": row["last_room"],
                    "left_truncated": bool(row["left_truncated"])})
        with (target / "availability.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in store.db.execute("SELECT subject,at_utc,kind FROM audit WHERE kind IN ('paused','resumed','closed') ORDER BY id"):
                if row["subject"] is None:
                    continue
                subject = store.subject(row["subject"])
                stream.write(json.dumps({"subject": row["subject"], "at_seconds": elapsed(subject, row["at_utc"]),
                                         "control": row["kind"]}) + "\n")
        with (target / "subjects.csv").open("w", encoding="utf-8", newline="") as stream:
            if summary["subjects"]:
                writer = csv.DictWriter(stream, fieldnames=list(summary["subjects"][0]))
                writer.writeheader()
                writer.writerows(summary["subjects"])
        from .mechanics import coverage, records
        with (target / "mechanics.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in records(store):
                stream.write(json.dumps(row, allow_nan=False) + "\n")
        (target / "mechanics-coverage.json").write_text(json.dumps(coverage(store), indent=2) + "\n", encoding="utf-8")
        manifest = {"schema_version": 2, "evidence": store.meta("evidence"), "study_id": store.meta("study_id"),
                    "game_version": store.meta("game_version"), "server_version": store.meta("server_version"),
                    "exporter_files": collector_files(),
                    "collector_files": store.meta("collector_files"), "driver_sha256": store.meta("driver_sha256"), "files": {
                        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.iterdir()) if p.is_file()}}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    finally:
        store.db.execute("ROLLBACK")
    return {"directory": str(target), "files": len(manifest["files"]) + 1}
