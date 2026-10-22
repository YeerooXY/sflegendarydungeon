#!/usr/bin/env python3
"""Initialize, control, inspect and export a local Legendary Dungeon study."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from sfld_study.controller import Controller, StudyLock, arm_pilot, builtin_hash, run_round_robin
from sfld_study.protocol import CLIENT_VERSION, capture, decode, fields
from sfld_study.report import export, report
from sfld_study.schema import StudyError, canonical, digest, observation, utc_now
from sfld_study.store import Store, read_roster


def load_json(path):
    if Path(path).stat().st_size > 4 * 1024 * 1024:
        raise StudyError("Input JSON exceeds four MiB.")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def create(directory, rows, evidence, seed):
    directory = Path(directory).resolve()
    if directory.exists():
        raise StudyError("Study directory must be new; existing studies are never overwritten.")
    directory.mkdir(parents=True)
    store = Store(directory / "study.sqlite", create=True)
    bindings = store.initialize(rows, evidence=evidence, seed=seed,
                                policy_hashes={p: builtin_hash() for p in ("random", "conservative")})
    (directory / "bindings.private.json").write_text(json.dumps(bindings, indent=2) + "\n", encoding="utf-8")
    (directory / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    return store


def demo(directory):
    """Exercise all room IDs and journaling with explicitly synthetic transitions.

    These are plumbing fixtures, not a model of room frequencies or damage.
    Use the C++ engine/evaluate_agent.py for strategy simulations.
    """
    store = create(directory, [{"study_id": "fixture-a", "world": "fixture"},
                               {"study_id": "fixture-b", "world": "fixture"}], "synthetic", 42)
    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=1)
    end = now + timedelta(days=7)
    store.configure(start.isoformat(), end.isoformat(), CLIENT_VERSION, 500, 500)
    room_types = [1, 2, 3, 4, 5, 50, 100, 200, 250, *range(301, 318), *range(319, 330),
                  *range(900, 910), *range(1000, 1024)]
    for row in store.db.execute("SELECT id,arm FROM subjects").fetchall():
        sid = row["id"]
        store.resume(sid)
        balance = 500
        before = {"game_version": CLIENT_VERSION, "room": 0, "status": 0, "hp_current": 100000,
                  "hp_max": 100000, "mushrooms": balance, "keys": 10, "level": 50, "theme": 8,
                  "event_open": True, "ultimate": False, "blessings": [], "curses": [], "gems": []}
        actions = [{"kind": "start", "theme": 8}] + [
            {"kind": "interact", "code": 70} for _ in room_types]
        if row["arm"] == "budgeted":
            actions += [{"kind": "interact", "code": 80, "mushroom_quote": 1, "quote_source": "synthetic"},
                        {"kind": "heal_partial", "theme": 8, "mushroom_quote": 8, "quote_source": "synthetic"},
                        {"kind": "heal_full", "theme": 8, "mushroom_quote": 40, "quote_source": "synthetic"}]
        actions += [{"kind": "interact", "code": 20}]
        for i, chosen in enumerate(actions):
            at = (now + timedelta(seconds=i)).isoformat()
            before["legal_actions"] = [chosen]
            did = store.begin(sid, before, chosen, at=at)
            balance -= chosen.get("mushroom_quote", 0)
            after = {**before, "room": min(i + 1, 100), "status": 10, "mushrooms": balance,
                     "encounter_type": room_types[min(max(i - 1, 0), len(room_types) - 1)],
                     "hp_previous": before["hp_current"], "hp_current": max(1, 100000 - i * 100)}
            if i == len(actions) - 1:
                after.update(status=102, room=100, outcome="run_complete", reward_quality="legendary")
            store.finish(did, after, at=at, private={"fixture_only": True})
            before = after
        # A second run is deliberately unfinished; censor it instead of
        # quietly excluding an unsuccessful event from the report.
        chosen = {"kind": "start", "theme": 8}
        before["legal_actions"] = [chosen]
        at = (now + timedelta(minutes=10)).isoformat()
        did = store.begin(sid, before, chosen, at=at)
        after = {**before, "room": 1, "status": 1, "hp_current": 100000}
        store.finish(did, after, at=at)
        store.close_event(sid, at=end.isoformat())
    store.backup(Path(directory) / "backups" / "fixture.sqlite")
    export(store, Path(directory) / "export")
    result = store.status()
    result["note"] = "Synthetic plumbing fixtures, not measured game outcomes or strategy estimates."
    store.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a PRIVATE study; never put its database in Git")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--roster", type=Path, required=True)
    init.add_argument("--evidence", choices=["live", "manual", "synthetic"], default="live")
    init.add_argument("--seed", type=int, default=20261007)
    demo_cmd = commands.add_parser("demo", help="Offline fixtures; creates no game sessions")
    demo_cmd.add_argument("--directory", type=Path, required=True)
    for name in ("status", "subjects", "report", "configure", "policy", "pause", "resume", "disarm",
                 "arm-pilot", "backup", "export", "observe", "reconcile", "annotate", "mechanics", "close", "run", "import"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--directory", type=Path, required=True)
        if name in {"configure", "pause", "resume", "policy", "observe", "close"}:
            cmd.add_argument("--subject", required=name not in {"configure", "pause"})
        if name == "configure":
            cmd.add_argument("--start", required=True)
            cmd.add_argument("--end", required=True)
            cmd.add_argument("--game-version", required=True)
            cmd.add_argument("--server-version")
            cmd.add_argument("--paid-budget", type=int, default=0)
            cmd.add_argument("--global-budget", type=int, default=0)
        if name == "policy":
            cmd.add_argument("--name", choices=["random", "conservative", "human", "external", "learned"], required=True)
            cmd.add_argument("--file", type=Path)
        if name in {"backup", "export"}:
            cmd.add_argument("--output", type=Path, required=True)
        if name in {"observe", "reconcile", "annotate", "import"}:
            cmd.add_argument("--file", type=Path, required=True)
        if name == "annotate":
            cmd.add_argument("--decision", required=True)
            cmd.add_argument("--evidence-file", type=Path, required=True)
        if name == "reconcile":
            cmd.add_argument("--decision", required=True)
            cmd.add_argument("--resolution", choices=["applied", "not_applied"], required=True)
            cmd.add_argument("--actual-charge", type=int)
            cmd.add_argument("--evidence-file", type=Path)
        if name == "close":
            cmd.add_argument("--reason", choices=["deadline", "abandoned", "technical_failure"], required=True)
        if name == "arm-pilot":
            cmd.add_argument("--subjects", nargs="+", required=True)
            cmd.add_argument("--max-actions", type=int, default=1)
        if name == "run":
            cmd.add_argument("--driver", type=Path, required=True)
            cmd.add_argument("--subjects", nargs="+", required=True)
            cmd.add_argument("--max-actions", type=int, default=1)
            cmd.add_argument("--external-policy", type=Path)
            cmd.add_argument("--action-file", type=Path, help="One explicit choice for a subject assigned the human policy")
            cmd.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(demo(args.directory), indent=2))
        return 0
    if args.command == "init":
        store = create(args.directory, read_roster(args.roster), args.evidence, args.seed)
        print(json.dumps(store.status(), indent=2))
        store.close()
        return 0
    store = Store(args.directory / "study.sqlite")
    try:
        result = None
        if args.command == "status":
            result = store.status()
        elif args.command == "subjects":
            result = [dict(r) for r in store.db.execute("SELECT id,arm,policy,budget,state,start_utc,end_utc,sequence,reason FROM subjects ORDER BY arm,policy,id")]
        elif args.command == "report":
            result = report(store)
        elif args.command == "mechanics":
            from sfld_study.mechanics import coverage
            result = coverage(store)
        elif args.command == "configure":
            store.configure(args.start, args.end, args.game_version, args.paid_budget, args.global_budget,
                            args.subject, args.server_version)
        elif args.command == "policy":
            if args.name in {"external", "learned"} and not args.file:
                raise StudyError("An external/learned policy needs a file to hash.")
            sha = hashlib.sha256(args.file.read_bytes()).hexdigest() if args.file else (
                digest({"human": 1}) if args.name == "human" else builtin_hash())
            store.set_policy(args.subject, args.name, sha)
        elif args.command == "pause":
            store.pause(args.subject)
        elif args.command == "resume":
            store.resume(args.subject)
        elif args.command == "disarm":
            with store.transaction():
                store.set_meta("live_enabled", False)
                store.audit(None, "disarmed", {})
            store.pause()
        elif args.command == "arm-pilot":
            arm_pilot(store, args.subjects, args.max_actions)
        elif args.command == "backup":
            result = {"backup": store.backup(args.output)}
        elif args.command == "export":
            result = export(store, args.output)
        elif args.command == "observe":
            value = load_json(args.file)
            result = {"observation": store.observe(args.subject, value.get("observation", value), at=value.get("at_utc"))}
        elif args.command == "annotate":
            result = store.annotate(args.decision, load_json(args.file),
                hashlib.sha256(args.evidence_file.read_bytes()).hexdigest())
        elif args.command == "reconcile":
            value = load_json(args.file)
            result = {"state": store.finish(args.decision, value.get("observation", value),
                      at=value.get("at_utc"), resolution=args.resolution, actual_charge=args.actual_charge,
                      evidence_sha256=hashlib.sha256(args.evidence_file.read_bytes()).hexdigest() if args.evidence_file else None)}
        elif args.command == "close":
            store.close_event(args.subject, args.reason)
        elif args.command == "import":
            if store.meta("evidence") == "live":
                raise StudyError("Import notes into a separate manual or synthetic study.")
            imported = skipped = 0
            with args.file.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    if len(line) > 1024 * 1024:
                        raise StudyError("Observation line is too large.")
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("evidence") != store.meta("evidence"):
                        raise StudyError("Imported evidence type differs from the study.")
                    did = digest(record)
                    old = store.db.execute("SELECT state FROM decisions WHERE id=?", (did,)).fetchone()
                    if old:
                        if old["state"] not in {"confirmed", "not_applied"}:
                            raise StudyError("This record has an unresolved import; reconcile it first.")
                        skipped += 1
                        continue
                    store.begin(record["subject"], record["before"], record["action"],
                                at=record["at_utc"], decision_id=did)
                    if record.get("after") is None:
                        store.unknown(did, "missing_observation")
                    else:
                        store.finish(did, record["after"], at=record["after_utc"])
                    imported += 1
            result = {"imported": imported, "already_imported": skipped}
        elif args.command == "run":
            if not 1 <= args.max_actions <= 100000:
                raise StudyError("Invalid action limit.")
            with StudyLock(args.directory / "controller.lock"):
                driver_hash = hashlib.sha256(args.driver.read_bytes()).hexdigest()
                with store.transaction():
                    if store.meta("driver_sha256") not in (None, driver_hash):
                        raise StudyError("Live driver source changed; use the frozen version or a separate study.")
                    store.set_meta("driver_sha256", driver_hash)
                spec = importlib.util.spec_from_file_location("local_study_driver", args.driver.resolve())
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                driver = module.create_driver(store, args.directory.resolve())
                try:
                    if args.action_file:
                        if len(args.subjects) != 1 or args.max_actions != 1:
                            raise StudyError("A manual action requires one subject and --max-actions 1.")
                        if (args.directory / "STOP").exists():
                            raise StudyError("The study's STOP file is present.")
                        selected = load_json(args.action_file)
                        result = Controller(store, driver, live=args.live).step(args.subjects[0], selected=selected)
                    else:
                        result = run_round_robin(store, driver, args.subjects, live=args.live,
                            max_actions=args.max_actions, stop_file=args.directory / "STOP",
                            external_path=args.external_policy)
                finally:
                    if callable(getattr(driver, "close", None)):
                        driver.close()
                    store.backup(args.directory / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".sqlite"))
        print(json.dumps(result if result is not None else store.status(), indent=2))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StudyError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        print(f"Study stopped ({type(error).__name__}); input and server data withheld.", file=sys.stderr)
        sys.exit(2)
