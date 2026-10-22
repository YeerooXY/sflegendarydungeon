"""SQLite write-ahead action journal. A pending action is never safe to resend."""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import timedelta
import json
from pathlib import Path
import random
import sqlite3
import uuid

from .schema import (StudyError, action, canonical, collector_files, digest, integer, observation,
                     spending_category, supplement, timestamp, utc_now, version)


ANNOTATION_DDL = """CREATE TABLE IF NOT EXISTS annotations (
 id INTEGER PRIMARY KEY, decision TEXT NOT NULL REFERENCES decisions(id),
 at_utc TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, data TEXT NOT NULL,
 UNIQUE(decision,evidence_sha256,data))"""


DDL = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subjects (
 id TEXT PRIMARY KEY, block TEXT NOT NULL, arm TEXT NOT NULL, policy TEXT NOT NULL, policy_hash TEXT NOT NULL,
 policy_seed INTEGER NOT NULL, prior_completions INTEGER NOT NULL, budget INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'prepared', reason TEXT, start_utc TEXT, end_utc TEXT,
 wait_until TEXT, current_run TEXT, sequence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, subject TEXT NOT NULL REFERENCES subjects(id),
 at_utc TEXT NOT NULL, data TEXT NOT NULL, fingerprint TEXT NOT NULL, private_fields TEXT);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES subjects(id), ordinal INTEGER NOT NULL,
 start_utc TEXT NOT NULL, end_utc TEXT, state TEXT NOT NULL, initial_room INTEGER,
 left_truncated INTEGER NOT NULL, last_room INTEGER, UNIQUE(subject, ordinal));
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES subjects(id), sequence INTEGER NOT NULL,
 run TEXT REFERENCES runs(id), at_utc TEXT NOT NULL, before_id INTEGER NOT NULL REFERENCES observations(id),
 chosen TEXT NOT NULL, quote INTEGER NOT NULL, reserved INTEGER NOT NULL,
 state TEXT NOT NULL, after_id INTEGER REFERENCES observations(id), charged INTEGER,
 net_delta INTEGER, category TEXT NOT NULL, error TEXT, UNIQUE(subject, sequence));
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, subject TEXT, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS observations_subject ON observations(subject, id);
CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_per_subject ON decisions(subject)
 WHERE state IN ('pending', 'unknown');
"""


class Store:
    def __init__(self, path, *, create=False):
        self.path = Path(path).resolve()
        if not create and not self.path.is_file():
            raise StudyError("Study database does not exist; initialize it first.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        if create:
            self.db.executescript(DDL)
            self.db.execute(ANNOTATION_DDL)
        if not create and self.meta("schema") != 1:
            raise StudyError("Unsupported study schema.")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def meta(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, canonical(value)))

    def audit(self, subject, kind, data):
        self.db.execute("INSERT INTO audit(at_utc,subject,kind,data) VALUES (?,?,?,?)",
                        (utc_now(), subject, kind, canonical(data)))

    def subject(self, subject):
        row = self.db.execute("SELECT * FROM subjects WHERE id=?", (subject,)).fetchone()
        if row is None:
            raise StudyError("Unknown anonymous subject.")
        return dict(row)

    def initialize(self, rows, *, evidence="live", seed=20261007, policy_hashes=None):
        if evidence not in {"live", "manual", "synthetic"}:
            raise StudyError("Unsupported evidence type.")
        if self.meta("schema") is not None:
            raise StudyError("Study already initialized.")
        if not rows:
            raise StudyError("The roster is empty.")
        identifiers = [r["study_id"] for r in rows]
        if len(set(identifiers)) != len(identifiers):
            raise StudyError("Duplicate roster identifiers.")
        # Randomize within a world block; alternate policies within each arm.
        rng = random.Random(seed)
        blocks = {}
        for row in rows:
            blocks.setdefault(row.get("world", "ungrouped"), []).append(row)
        block_list = list(blocks.values())
        rng.shuffle(block_list)
        counts = {"f2p": 0, "budgeted": 0}
        bindings = []
        with self.transaction():
            for key, value in {"schema": 1, "evidence": evidence, "created_utc": utc_now(),
                               "study_id": uuid.uuid4().hex, "assignment_seed": seed,
                               "live_enabled": False, "game_version": None, "server_version": None,
                               "global_budget": 0, "pilot_subjects": [], "pilot_action_limit": 0}.items():
                self.set_meta(key, value)
            for block in block_list:
                rng.shuffle(block)
                block_id = uuid.uuid4().hex
                for index, row in enumerate(block):
                    arm = ("f2p", "budgeted")[index % 2]
                    policy = ("random", "conservative")[counts[arm] % 2]
                    counts[arm] += 1
                    sid = uuid.uuid4().hex
                    prior = integer(int(row.get("prior_ld_completions") or 0))
                    policy_hash = (policy_hashes or {}).get(policy, digest({"builtin": policy, "revision": 1}))
                    self.db.execute("""INSERT INTO subjects
                        (id,block,arm,policy,policy_hash,policy_seed,prior_completions,budget)
                        VALUES (?,?,?,?,?,?,?,0)""", (sid, block_id, arm, policy, policy_hash, rng.randrange(2**53), prior))
                    bindings.append({"subject": sid, **row})
            self.audit(None, "initialized", {"subjects": len(bindings), "evidence": evidence})
        return bindings

    def configure(self, start, end, game_version, paid_budget=0, global_budget=0, subject=None, server_version=None):
        start_dt, end_dt = timestamp(start), timestamp(end)
        if end_dt <= start_dt:
            raise StudyError("Event end must follow event start.")
        paid_budget, global_budget = integer(paid_budget), integer(global_budget)
        version(game_version)
        with self.transaction():
            if self.db.execute("SELECT 1 FROM decisions LIMIT 1").fetchone():
                raise StudyError("Freeze schedule and budgets before recording decisions; use a new study for changes.")
            if subject:
                self.subject(subject)
                self.db.execute("UPDATE subjects SET start_utc=?, end_utc=? WHERE id=?",
                                (start_dt.isoformat(), end_dt.isoformat(), subject))
            else:
                self.db.execute("UPDATE subjects SET start_utc=?,end_utc=?,budget=CASE arm WHEN 'f2p' THEN 0 ELSE ? END",
                                (start_dt.isoformat(), end_dt.isoformat(), paid_budget))
                self.set_meta("game_version", game_version)
                self.set_meta("server_version", observation({"server_version": server_version})["server_version"])
                self.set_meta("collector_files", collector_files())
                self.set_meta("global_budget", global_budget)
            self.audit(subject, "configured", {"start": start, "end": end, "paid_budget": paid_budget,
                                               "global_budget": global_budget, "game_version": game_version})

    def set_policy(self, subject, name, sha256):
        if name not in {"random", "conservative", "external", "human", "learned"}:
            raise StudyError("Unknown policy type.")
        if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise StudyError("A SHA-256 of the frozen policy is required.")
        with self.transaction():
            self.subject(subject)
            if self.db.execute("SELECT 1 FROM decisions WHERE subject=?", (subject,)).fetchone():
                raise StudyError("Do not switch a policy during an event comparison.")
            self.db.execute("UPDATE subjects SET policy=?,policy_hash=? WHERE id=?", (name, sha256, subject))
            self.audit(subject, "policy_assigned", {"policy": name, "sha256": sha256})

    def unresolved(self, subject=None):
        query = "SELECT * FROM decisions WHERE state IN ('pending','unknown')"
        return [dict(r) for r in self.db.execute(query + (" AND subject=?" if subject else ""), (subject,) if subject else ())]

    def pause(self, subject=None, reason="operator"):
        with self.transaction():
            self.db.execute("UPDATE subjects SET state='paused', reason=?" + (" WHERE id=?" if subject else ""),
                            (reason, subject) if subject else (reason,))
            self.audit(subject, "paused", {"reason": reason})

    def resume(self, subject):
        with self.transaction():
            row = self.subject(subject)
            if self.unresolved(subject):
                raise StudyError("Unresolved action: reconcile it without resending before resuming.")
            if row["start_utc"] is None or row["end_utc"] is None:
                raise StudyError("Configure the event window first.")
            if row["state"] == "closed":
                raise StudyError("This character event is closed.")
            self.db.execute("UPDATE subjects SET state='ready',reason=NULL WHERE id=?", (subject,))
            self.audit(subject, "resumed", {})

    def spent(self, subject=None):
        sql = "SELECT COALESCE(SUM(COALESCE(charged,0)),0), COALESCE(SUM(reserved),0) FROM decisions"
        row = self.db.execute(sql + (" WHERE subject=?" if subject else ""), (subject,) if subject else ()).fetchone()
        return {"charged": row[0], "reserved": row[1]}

    def latest(self, subject):
        row = self.db.execute("SELECT * FROM observations WHERE subject=? ORDER BY id DESC LIMIT 1", (subject,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "data": json.loads(row["data"])}

    def _observe(self, subject, data, at, private=None):
        clean = observation(data)
        timestamp(at)
        result = self.db.execute("INSERT INTO observations(subject,at_utc,data,fingerprint,private_fields) VALUES (?,?,?,?,?)",
                                (subject, at, canonical(clean), digest(clean), None if private is None else canonical(private)))
        return result.lastrowid, clean

    def observe(self, subject, data, *, at=None, private=None):
        at = at or utc_now()
        with self.transaction():
            self.subject(subject)
            oid, clean = self._observe(subject, data, at, private)
            self._track_run(subject, clean, at, initial=True)
            return oid

    def reviewed(self, decision, before, after):
        # Old databases remain readable without an implicit schema migration.
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotations'").fetchone()
        count = 0
        if exists:
            for row in self.db.execute("SELECT data FROM annotations WHERE decision=? ORDER BY id", (decision,)):
                details = json.loads(row[0])
                before = supplement(before, details["before"])
                after = supplement(after, details["after"])
                count += 1
        return before, after, count

    def annotate(self, decision, details, evidence_sha256, *, at=None):
        """Append reviewed UI evidence; preserve raw snapshots and spending state."""
        if not isinstance(details, dict) or not any(k in details for k in ("before", "after")):
            raise StudyError("Reviewed details need a before and/or after observation.")
        if not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64 or any(
                c not in "0123456789abcdef" for c in evidence_sha256):
            raise StudyError("Reviewed details need a SHA-256 of their private UI evidence.")
        clean = {side: observation(details[side]) if details.get(side) is not None else None
                 for side in ("before", "after")}
        if not any(side and any(v is not None for v in side.values()) for side in clean.values()):
            raise StudyError("Reviewed details contain no recognized observations.")
        at = at or utc_now()
        timestamp(at)
        with self.transaction():
            row = self.db.execute("""SELECT d.subject,b.data AS before_data,a.data AS after_data
                FROM decisions d JOIN observations b ON b.id=d.before_id
                LEFT JOIN observations a ON a.id=d.after_id WHERE d.id=?""", (decision,)).fetchone()
            if row is None:
                raise StudyError("Unknown decision for reviewed details.")
            if row["after_data"] is None and clean["after"] is not None:
                raise StudyError("No recorded outcome to supplement; reconcile the action first.")
            before, after, _ = self.reviewed(decision, observation(json.loads(row["before_data"])),
                None if row["after_data"] is None else observation(json.loads(row["after_data"])))
            supplement(before, clean["before"])
            supplement(after, clean["after"])
            self.db.execute(ANNOTATION_DDL)
            added = self.db.execute("INSERT OR IGNORE INTO annotations(decision,at_utc,evidence_sha256,data) VALUES (?,?,?,?)",
                                   (decision, at, evidence_sha256, canonical(clean))).rowcount
            if added:
                self.audit(row["subject"], "observation_review", {"decision": decision, "evidence_sha256": evidence_sha256})
        return {"added": bool(added), "decision": decision}

    def _track_run(self, subject, data, at, *, initial=False, started=False):
        row = self.subject(subject)
        run = row["current_run"]
        if run is None and data["room"] and data["status"] not in (None, 0, 102):
            run = uuid.uuid4().hex
            ordinal = self.db.execute("SELECT COUNT(*) FROM runs WHERE subject=?", (subject,)).fetchone()[0] + 1
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,NULL,'open',?,?,?)",
                            (run, subject, ordinal, at, data["room"], int(not started), data["room"]))
            self.db.execute("UPDATE subjects SET current_run=? WHERE id=?", (run, subject))
        if run:
            self.db.execute("UPDATE runs SET last_room=? WHERE id=?", (data["room"], run))
            if data["status"] == 102 and not initial:
                self.db.execute("UPDATE runs SET state='complete',end_utc=? WHERE id=?", (at, run))
                self.db.execute("UPDATE subjects SET current_run=NULL WHERE id=?", (subject,))
        return run

    def begin(self, subject, before, chosen, *, at=None, private=None, expected_sequence=None, decision_id=None):
        at = at or utc_now()
        clean, selected = observation(before), action(chosen)
        with self.transaction():
            row = self.subject(subject)
            if expected_sequence is not None and row["sequence"] != expected_sequence:
                raise StudyError("Another controller already advanced this character.")
            if self.meta("collector_files") != collector_files():
                raise StudyError("Collector code changed after configuration; preserve its version or begin a new study.")
            if row["state"] != "ready" or self.unresolved(subject):
                raise StudyError("Character is paused or has an unresolved action.")
            if not row["start_utc"] or not timestamp(row["start_utc"]) <= timestamp(at) < timestamp(row["end_utc"]):
                raise StudyError("Outside the configured character event window.")
            if row["wait_until"] and timestamp(at) < timestamp(row["wait_until"]):
                raise StudyError("Character is waiting until its saved recovery time.")
            latest = self.latest(subject)
            if latest is not None and timestamp(at) < timestamp(latest["at_utc"]):
                raise StudyError("Decision timestamps must follow previously recorded observations.")
            if clean["event_open"] is not True or clean["ultimate"] is not False:
                raise StudyError("Require an observed open, normal Legendary Dungeon.")
            if clean["game_version"] != self.meta("game_version"):
                raise StudyError("Game version does not match the frozen study.")
            if self.meta("evidence") == "live" and (not self.meta("server_version")
                    or clean["server_version"] != self.meta("server_version")):
                raise StudyError("Observed server version does not match the frozen live study.")
            if selected not in (clean["legal_actions"] or []):
                raise StudyError("Selected action is not in the observed legal menu.")
            if clean["event_seconds_left"] is not None and clean["event_seconds_left"] <= 0:
                raise StudyError("Server event has ended.")
            quote = selected["mushroom_quote"]
            if quote is None or (quote and selected["quote_source"] not in {"server", "ui", "synthetic"}):
                raise StudyError("The mushroom price is unknown.")
            if self.meta("evidence") != "synthetic" and selected["quote_source"] == "synthetic":
                raise StudyError("Synthetic prices cannot authorize live actions.")
            if quote and (clean["mushrooms"] is None or clean["mushrooms"] < quote):
                raise StudyError("Insufficient or unobserved mushroom balance.")
            if selected["kind"] == "start":
                if clean["level"] is None or clean["level"] < 50:
                    raise StudyError("Level 50 is required to enter.")
                if not clean["hp_max"] or clean["hp_current"] is None or clean["hp_current"] * 5 < clean["hp_max"]:
                    raise StudyError("At least 20 percent observed health is required to enter.")
            local, global_ = self.spent(subject), self.spent()
            if local["charged"] + local["reserved"] + quote > row["budget"]:
                raise StudyError("Character mushroom budget would be exceeded.")
            if global_["charged"] + global_["reserved"] + quote > self.meta("global_budget"):
                raise StudyError("Study mushroom budget would be exceeded.")
            oid, clean = self._observe(subject, clean, at, private)
            run = self._track_run(subject, clean, at, initial=True)
            did = decision_id or uuid.uuid4().hex
            self.db.execute("""INSERT INTO decisions
                (id,subject,sequence,run,at_utc,before_id,chosen,quote,reserved,state,category)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?)""",
                (did, subject, row["sequence"] + 1, run, at, oid, canonical(selected), quote, quote, spending_category(selected)))
            self.db.execute("UPDATE subjects SET sequence=sequence+1,wait_until=NULL WHERE id=?", (subject,))
            return did

    def unknown(self, decision, reason="transport_unknown"):
        with self.transaction():
            row = self.db.execute("SELECT * FROM decisions WHERE id=?", (decision,)).fetchone()
            if row is None or row["state"] not in {"pending", "unknown"}:
                raise StudyError("Action is not pending.")
            self.db.execute("UPDATE decisions SET state='unknown',error=? WHERE id=?", (reason, decision))
            self.db.execute("UPDATE subjects SET state='paused',reason=? WHERE id=?", (reason, row["subject"]))
            self.audit(row["subject"], "outcome_unknown", {"decision": decision, "reason": reason})

    def finish(self, decision, after, *, at=None, private=None, resolution=None,
               actual_charge=None, evidence_sha256=None):
        at = at or utc_now()
        clean = observation(after)
        with self.transaction():
            row = self.db.execute("SELECT * FROM decisions WHERE id=?", (decision,)).fetchone()
            if row is None or row["state"] not in {"pending", "unknown"}:
                raise StudyError("Action has already been settled or does not exist.")
            if timestamp(at) < timestamp(row["at_utc"]):
                raise StudyError("An outcome cannot precede its action.")
            if row["state"] == "unknown" and resolution not in {"applied", "not_applied"}:
                raise StudyError("Reconciliation needs an explicit applied/not_applied determination.")
            before = json.loads(self.db.execute("SELECT data FROM observations WHERE id=?", (row["before_id"],)).fetchone()[0])
            chosen = json.loads(row["chosen"])
            oid, clean = self._observe(row["subject"], clean, at, private)
            delta = None if before["mushrooms"] is None or clean["mushrooms"] is None else before["mushrooms"] - clean["mushrooms"]
            # Net resource changes are retained independently from the displayed quote.
            expected = 0 if resolution == "not_applied" else row["quote"]
            if actual_charge is not None:
                actual_charge = integer(actual_charge)
                if resolution not in {"applied", "not_applied"} or not evidence_sha256 or len(evidence_sha256) != 64:
                    raise StudyError("A corrected charge requires explicit reconciliation and an evidence file hash.")
                if resolution == "not_applied" and actual_charge != 0:
                    raise StudyError("An unapplied action cannot have an action charge.")
                expected = actual_charge
            verified = (actual_charge is not None or (delta is not None
                        and (delta == expected or (expected == 0 and delta < 0))))
            if self.meta("evidence") == "live" and (clean["game_version"] != self.meta("game_version")
                    or clean["server_version"] != self.meta("server_version")
                    or any(clean[k] is None for k in ("hp_current", "hp_max", "room", "status"))):
                verified = False
            if not verified:
                reserve = max(row["quote"], delta or 0)
                self.db.execute("UPDATE decisions SET state='unknown',after_id=?,net_delta=?,reserved=?,error='cost_unverified' WHERE id=?",
                                (oid, delta, reserve, decision))
                self.db.execute("UPDATE subjects SET state='paused',reason='cost_unverified' WHERE id=?", (row["subject"],))
                if delta is not None and delta > row["quote"]:
                    self.db.execute("UPDATE subjects SET state='paused',reason='unexpected_charge' WHERE state='ready'")
                self.audit(row["subject"], "cost_unverified", {"decision": decision, "quote": row["quote"], "net_delta": delta})
                return "unknown"
            run = row["run"]
            if resolution != "not_applied":
                run = self._track_run(row["subject"], clean, at, started=chosen["kind"] == "start") or run
            state = "not_applied" if resolution == "not_applied" else "confirmed"
            self.db.execute("UPDATE decisions SET state=?,after_id=?,charged=?,net_delta=?,reserved=0,error=NULL,run=? WHERE id=?",
                            (state, oid, expected, delta, run, decision))
            if chosen["kind"] == "wait" and resolution != "not_applied":
                until = (timestamp(row["at_utc"]) + timedelta(seconds=chosen["seconds"])).isoformat()
                self.db.execute("UPDATE subjects SET wait_until=? WHERE id=?", (until, row["subject"]))
            self.audit(row["subject"], "reconciled" if resolution else "confirmed", {
                "decision": decision, "state": state, "evidence_sha256": evidence_sha256})
            return state

    def close_event(self, subject, reason="deadline", at=None):
        if reason not in {"deadline", "abandoned", "technical_failure"}:
            raise StudyError("Unsupported censoring reason.")
        at = at or utc_now()
        timestamp(at)
        with self.transaction():
            row = self.subject(subject)
            if reason == "deadline" and (row["end_utc"] is None or timestamp(at) < timestamp(row["end_utc"])):
                raise StudyError("An event cannot be marked observed through its deadline before that deadline.")
            if row["current_run"]:
                self.db.execute("UPDATE runs SET state=?,end_utc=? WHERE id=? AND state='open'", (reason, at, row["current_run"]))
            self.db.execute("UPDATE subjects SET state='closed',reason=? WHERE id=?", (reason, subject))
            self.audit(subject, "closed", {"reason": reason, "at_utc": at})

    def backup(self, destination):
        target = Path(destination).resolve()
        if target.exists() or target == self.path:
            raise StudyError("Backup destination must be a new file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy = sqlite3.connect(target)
        try:
            self.db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StudyError("Backup integrity check failed.")
        finally:
            copy.close()
        return str(target)

    def status(self):
        groups = [dict(r) for r in self.db.execute("SELECT arm,policy,state,COUNT(*) AS characters FROM subjects GROUP BY arm,policy,state")]
        return {"schema": 1, "evidence": self.meta("evidence"), "live_enabled": self.meta("live_enabled"),
                "groups": groups, "spending": self.spent(), "unresolved_actions": len(self.unresolved()),
                "observations": self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                "decisions": self.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
                "completed_runs": self.db.execute("SELECT COUNT(*) FROM runs WHERE state='complete'").fetchone()[0]}


def read_roster(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if "status" in row and row["status"] != "created":
            raise StudyError("Roster contains an uncreated character.")
        if "registration_verified" in row and row["registration_verified"] != "yes":
            raise StudyError("Roster contains an unverified character.")
    return rows
