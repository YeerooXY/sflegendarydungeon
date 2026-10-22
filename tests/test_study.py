"""Offline tests of financial bounds, crash behavior, protocol and export privacy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sfld_study.controller import Controller, StudyLock, arm_pilot, builtin_hash, choose
from sfld_study.protocol import CLIENT_VERSION, capture, command, decode, fields
from sfld_study.pilot import ReviewedPilotDriver
from sfld_study.report import export, report, transitions
from sfld_study.mechanics import coverage, records
from sfld_study.schema import StudyError, action, digest, observation
from sfld_study.store import Store

NOW = "2026-10-07T12:00:00+00:00"
END = "2026-10-14T12:00:00+00:00"


def state(**kwargs):
    return {"game_version": CLIENT_VERSION, "server_version": "31001", "room": 10, "stage": 1, "status": 10, "level": 50,
            "event_open": True, "ultimate": False, "theme": 8, "hp_current": 70000, "hp_max": 100000,
            "mushrooms": 100, "keys": 4, "encounter_type": 1,
            "legal_actions": [{"kind": "interact", "code": 20}], **kwargs}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "study.sqlite", create=True)
        self.bindings = self.store.initialize([
            {"study_id": "P1-private", "world": "private-world", "email": "private@example.invalid"},
            {"study_id": "P2-private", "world": "private-world"}], evidence="synthetic",
            policy_hashes={"random": builtin_hash(), "conservative": builtin_hash()})
        self.store.configure(NOW, END, CLIENT_VERSION, 50, 50, server_version="31001")
        self.paid = self.store.db.execute("SELECT id FROM subjects WHERE arm='budgeted'").fetchone()[0]
        self.free = self.store.db.execute("SELECT id FROM subjects WHERE arm='f2p'").fetchone()[0]
        for sid in (self.paid, self.free):
            self.store.resume(sid)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def begin(self, sid=None, before=None, chosen=None):
        chosen = chosen or {"kind": "interact", "code": 20}
        before = before or state(legal_actions=[chosen])
        return self.store.begin(sid or self.paid, before, chosen, at=NOW)

    def test_f2p_cannot_spend(self):
        heal = {"kind": "heal_partial", "theme": 8, "mushroom_quote": 8, "quote_source": "synthetic"}
        with self.assertRaises(StudyError):
            self.begin(self.free, chosen=heal)
        self.assertEqual(self.store.status()["decisions"], 0)

    def test_unquoted_heal_and_reroll_blocked(self):
        for chosen in ({"kind": "heal_full", "theme": 8}, {"kind": "interact", "code": 80}):
            with self.assertRaises(StudyError):
                self.begin(chosen=chosen)

    def test_reserve_before_network_and_recover_after_restart(self):
        heal = {"kind": "heal_partial", "theme": 8, "mushroom_quote": 8, "quote_source": "synthetic"}
        did = self.begin(chosen=heal)
        self.assertEqual(self.store.spent(), {"charged": 0, "reserved": 8})
        self.store.close()
        self.store = Store(self.root / "study.sqlite")
        with self.assertRaises(StudyError):
            self.begin(chosen=heal)
        with self.assertRaises(StudyError):
            self.store.resume(self.paid)
        self.store.unknown(did)
        self.assertEqual(self.store.finish(did, state(mushrooms=92), at=NOW, resolution="applied"), "confirmed")
        self.assertEqual(self.store.spent(), {"charged": 8, "reserved": 0})
        with self.assertRaises(StudyError):
            self.store.finish(did, state(mushrooms=84), at=NOW)
        self.store.resume(self.paid)

    def test_oversized_unexpected_cost_reserves_observed_loss_and_pauses_all(self):
        reroll = {"kind": "interact", "code": 80, "mushroom_quote": 1, "quote_source": "synthetic"}
        did = self.begin(chosen=reroll)
        self.assertEqual(self.store.finish(did, state(mushrooms=93), at=NOW), "unknown")
        self.assertEqual(self.store.spent()["reserved"], 7)
        self.assertEqual(self.store.subject(self.free)["state"], "paused")
        self.store.finish(did, state(mushrooms=93), at=NOW, resolution="applied", actual_charge=7,
                          evidence_sha256="a" * 64)
        self.assertEqual(self.store.spent(), {"charged": 7, "reserved": 0})
        record = self.store.db.execute("SELECT quote,charged FROM decisions").fetchone()
        self.assertEqual(tuple(record), (1, 7))

    def test_missing_balance_is_unknown_not_zero_cost(self):
        did = self.begin()
        self.assertEqual(self.store.finish(did, state(mushrooms=None), at=NOW), "unknown")
        with self.assertRaises(StudyError):
            self.store.finish(did, state(), at=NOW)

    def test_negative_balance_delta_is_recorded(self):
        did = self.begin()
        self.store.finish(did, state(mushrooms=101), at=NOW)
        row = self.store.db.execute("SELECT charged,net_delta FROM decisions").fetchone()
        self.assertEqual(tuple(row), (0, -1))

    def test_global_budget_includes_pending_actions(self):
        # Make both subjects budgeted in this isolated finance fixture.
        self.store.db.execute("UPDATE subjects SET budget=50,arm='budgeted'")
        heal = {"kind": "heal_full", "theme": 8, "mushroom_quote": 40, "quote_source": "synthetic"}
        self.begin(self.paid, chosen=heal)
        with self.assertRaises(StudyError):
            self.begin(self.free, chosen=heal)

    def test_unknown_action_is_not_automatically_retried(self):
        class Driver:
            calls = 0
            def observe(self, sid):
                return state(), None
            def execute(self, sid, chosen):
                self.calls += 1
                raise TimeoutError("token=do-not-print")
        driver = Driver()
        ctl = Controller(self.store, driver)
        with self.assertRaisesRegex(StudyError, "uncertain"):
            ctl.step(self.paid, at=NOW)
        with self.assertRaises(StudyError):
            ctl.step(self.paid, at=NOW)
        self.assertEqual(driver.calls, 1)
        self.assertEqual(len(self.store.unresolved()), 1)

    def test_unknown_screen_is_saved_before_pause(self):
        class Driver:
            def observe(self, sid):
                return state(legal_actions=None, encounter_type=328), {"iadungeonsave": "123"}
        with self.assertRaises(StudyError):
            Controller(self.store, Driver()).step(self.paid, at=NOW)
        self.assertEqual(self.store.latest(self.paid)["data"]["encounter_type"], 328)
        self.assertEqual(self.store.status()["decisions"], 0)

    def test_stale_controller_cannot_commit(self):
        did = self.begin()
        self.store.finish(did, state(), at=NOW)
        with self.assertRaises(StudyError):
            self.store.begin(self.paid, state(), {"kind": "interact", "code": 20}, at=NOW, expected_sequence=0)

    def test_deadline_closed_mode_and_version_checked(self):
        for update in ({"event_open": False}, {"ultimate": True}, {"game_version": "31.500"}, {"event_seconds_left": 0}):
            with self.assertRaises(StudyError):
                self.begin(before=state(**update))
        with self.assertRaises(StudyError):
            self.store.begin(self.paid, state(), {"kind": "interact", "code": 20}, at=END)
        with self.assertRaises(StudyError):
            self.store.close_event(self.paid, at=NOW)

    def test_entry_minimum_health_and_level(self):
        start = {"kind": "start", "theme": 8}
        for hp, level in ((19999, 50), (20000, 49)):
            with self.assertRaises(StudyError):
                self.begin(before=state(hp_current=hp, level=level, legal_actions=[start]), chosen=start)
        self.begin(before=state(hp_current=20000, legal_actions=[start]), chosen=start)

    def test_legal_menu_exact_membership(self):
        with self.assertRaises(StudyError):
            self.begin(before=state(), chosen={"kind": "interact", "code": 21})

    def test_wait_is_persistent_and_does_not_call_transport(self):
        wait = {"kind": "wait", "seconds": 3600}
        class Driver:
            def observe(self, sid):
                return state(legal_actions=[wait]), None
            def execute(self, sid, chosen):
                raise AssertionError("A wait must not touch the network")
        Controller(self.store, Driver()).step(self.paid, at=NOW)
        self.assertIsNotNone(self.store.subject(self.paid)["wait_until"])
        with self.assertRaises(StudyError):
            self.begin()

    def test_dead_monster_does_not_end_run_and_completion_not_double_counted(self):
        did = self.begin()
        self.store.finish(did, state(status=101, hp_current=0), at=NOW)
        self.assertEqual(self.store.status()["completed_runs"], 0)
        self.assertEqual(self.store.db.execute("SELECT state FROM runs").fetchone()[0], "open")
        did = self.begin()
        self.store.finish(did, state(status=102, room=100), at=NOW)
        self.store.observe(self.paid, state(status=102, room=100), at=NOW)
        self.assertEqual(self.store.status()["completed_runs"], 1)

    def test_start_and_partial_run_are_reported_separately(self):
        start = {"kind": "start", "theme": 8}
        did = self.begin(before=state(room=0, status=0, legal_actions=[start]), chosen=start)
        self.store.finish(did, state(room=1, status=1), at=NOW)
        self.store.close_event(self.paid, at=END)
        summary = report(self.store)
        group = next(g for g in summary["groups"] if g["arm"] == "budgeted")
        self.assertEqual(group["full_event_characters"], 1)
        self.assertEqual(group["mean_completions_per_full_event"], 0)
        self.assertEqual(group["restricted_first_completion_seconds"], 7 * 86400)

    def test_export_strips_nested_secrets_and_hidden_seed(self):
        secret = "PASSWORD_SENTINEL_abcXYZ"
        data = state(password=secret, seed=secret, account_id=secret,
                     blessings=[{"slot": 0, "id": 2, "remaining": 8, "token": secret}])
        did = self.store.begin(self.paid, data, {"kind": "interact", "code": 20, "token": secret},
                               at=NOW, private={"seed": secret, "authorization": secret})
        self.store.finish(did, state(hp_current=0, status=101), at=NOW)
        export(self.store, self.root / "public")
        all_text = "".join(p.read_text() for p in (self.root / "public").iterdir())
        for item in (secret, "private-world", "P1-private", "private@example.invalid", "2026-10-07T12:00:00"):
            self.assertNotIn(item, all_text)
        entry = json.loads((self.root / "public" / "transitions.jsonl").read_text().strip())
        self.assertTrue(entry["damage_censored_at_death"])
        self.assertEqual(entry["before"]["blessings"][0]["remaining"], 8)
        self.assertIsNone(entry["before"]["curses"])

    def test_backup_contains_inflight_journal(self):
        did = self.begin()
        self.store.backup(self.root / "copy.sqlite")
        copy = Store(self.root / "copy.sqlite")
        try:
            self.assertEqual(copy.unresolved()[0]["id"], did)
            self.assertEqual(copy.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            copy.close()

    def test_reviewed_mechanics_preserve_journal_costs_and_privacy(self):
        did = self.begin()
        self.store.finish(did, state(hp_current=85000), at=NOW)
        original = self.store.db.execute("SELECT data FROM observations ORDER BY id").fetchall()
        details = {"before": {"run_number_event": 1, "run_number_lifetime": 3,
                              "fountain_variant": "cleansing", "password": "PRIVATE_SENTINEL"},
                   "after": {"result": {"events_complete": True, "events": [
                       {"kind": "heal", "source": "fountain", "amount": 15000, "unit": "hp_absolute"},
                       {"kind": "effect_removed", "effect_id": 102, "removal_reason": "fountain",
                        "token": "PRIVATE_SENTINEL"}]}}}
        self.assertTrue(self.store.annotate(did, details, "a" * 64)["added"])
        self.assertFalse(self.store.annotate(did, details, "a" * 64)["added"])
        self.assertEqual(self.store.spent(), {"charged": 0, "reserved": 0})
        self.assertEqual(original, self.store.db.execute("SELECT data FROM observations ORDER BY id").fetchall())
        row = next(transitions(self.store))
        self.assertEqual(row["before"]["run_number_event"], 1)
        self.assertEqual(row["before"]["run_number_lifetime"], 3)
        self.assertEqual(row["annotation_count"], 1)
        export(self.store, self.root / "reviewed-export")
        text = "".join(p.read_text() for p in (self.root / "reviewed-export").iterdir())
        self.assertNotIn("PRIVATE_SENTINEL", text)
        self.assertNotIn("a" * 64, text)
        self.assertTrue((self.root / "reviewed-export" / "mechanics.jsonl").is_file())

    def test_reviewed_details_cannot_replace_known_values_or_resolve_actions(self):
        did = self.begin()
        with self.assertRaises(StudyError):
            self.store.annotate(did, {"after": {"outcome": "fight"}}, "b" * 64)
        self.store.unknown(did)
        self.store.annotate(did, {"before": {"screen_kind": "encounter"}}, "b" * 64)
        self.assertEqual(self.store.unresolved()[0]["state"], "unknown")
        with self.assertRaises(StudyError):
            self.store.annotate(did, {"before": {"hp_current": 123}}, "b" * 64)
        with self.assertRaises(StudyError):
            self.store.annotate(did, {"before": {"screen_kind": "shop"}}, "b" * 64)
        with self.assertRaises(StudyError):
            self.store.annotate(did, {"before": {"container_kind": "private-name"}}, "b" * 64)

    def test_old_study_can_receive_append_only_review_without_losing_data(self):
        self.store.db.execute("DROP TABLE annotations")
        did = self.begin()
        self.store.finish(did, state(), at=NOW)
        self.assertEqual(next(transitions(self.store))["annotation_count"], 0)
        self.store.annotate(did, {"before": {"run_number_event": 2}}, "c" * 64)
        self.assertEqual(next(transitions(self.store))["before"]["run_number_event"], 2)

    def test_door_pairs_link_to_container_action_without_inventing_contents(self):
        choose_door = {"kind": "interact", "code": 2}
        pair = [{"index": 0, "kind": "monster", "trap_kind": "none"},
                {"index": 1, "kind": "mystery", "trap_kind": "none"}]
        did = self.begin(before=state(screen_kind="doors", doors=pair, legal_actions=[choose_door]), chosen=choose_door)
        self.store.finish(did, state(screen_kind="encounter", container_kind="warrior_skeleton",
            result={"monster_on_entry": False}), at=NOW)
        open_corpse = {"kind": "interact", "code": 40}
        did2 = self.begin(before=state(screen_kind="encounter", container_kind="warrior_skeleton",
                         legal_actions=[open_corpse]), chosen=open_corpse)
        self.store.finish(did2, state(result={"events_complete": True,
            "events": [{"kind": "monster_awakened", "source": "container"}]}), at=NOW)
        rows = list(records(self.store))
        self.assertEqual(rows[0]["chosen_door"]["kind"], "mystery")
        self.assertFalse(rows[0]["result"]["monster_on_entry"])
        self.assertEqual(rows[1]["entry"]["decision"], did)
        self.assertEqual(rows[1]["entry"]["doors"][0]["kind"], "monster")
        self.assertIsNone(rows[1]["result"]["monster_on_entry"])
        self.assertEqual(coverage(self.store)["counts"]["door_choices"], 1)

    def test_shop_draws_count_identical_rerolls_once_and_all_offers(self):
        offers = [{"slot": 0, "effect_id": 1, "duration": 4, "key_price": 0},
                  {"slot": 1, "effect_id": 2, "duration": 8, "key_price": 0}]
        choose_door = {"kind": "interact", "code": 1}
        did = self.begin(before=state(room=5, legal_actions=[choose_door]), chosen=choose_door)
        shop = state(room=5, screen_kind="shop", encounter_type=315, offers=offers)
        self.store.finish(did, shop, at=NOW)
        reroll = {"kind": "interact", "code": 80, "mushroom_quote": 1, "quote_source": "synthetic"}
        did2 = self.begin(before={**shop, "legal_actions": [reroll]}, chosen=reroll)
        self.store.finish(did2, {**shop, "mushrooms": 99}, at=NOW)
        buy = {"kind": "buy_blessing", "effect_id": 1, "key_price": 0}
        did3 = self.begin(before={**shop, "mushrooms": 99, "legal_actions": [buy]}, chosen=buy)
        self.store.finish(did3, state(room=6, mushrooms=99), at=NOW)
        draws = [r for r in records(self.store) if r["record_type"] == "shop_draw"]
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[1]["origin"], "reroll")
        self.assertEqual(coverage(self.store)["counts"]["shop_offers_observed"], 4)

    def test_unknown_outcomes_are_not_empty_rewards_or_no_curse(self):
        open_box = {"kind": "interact", "code": 40}
        did = self.begin(before=state(container_kind="wooden_box", legal_actions=[open_box]), chosen=open_box)
        self.store.finish(did, state(), at=NOW)
        counts = coverage(self.store)["counts"]
        self.assertEqual(counts["container_opens"], 1)
        self.assertEqual(counts["container_opens_with_complete_subevents"], 0)
        self.assertIsNone(next(records(self.store))["result"])
        for invalid in ({"result": {"events_complete": True}}, {"run_number_event": 0},
                        {"doors": [{"index": 0}, {"index": 0}]}):
            with self.assertRaises(StudyError):
                observation(invalid)

    def test_annotation_cli_and_mechanics_cli_work_offline(self):
        did = self.begin()
        self.store.finish(did, state(), at=NOW)
        details = self.root / "review.json"
        details.write_text(json.dumps({"before": {"run_number_event": 2}}))
        proof = self.root / "synthetic-evidence.txt"
        proof.write_text("Synthetic CLI fixture; no live observation.")
        cli = Path(__file__).resolve().parents[1] / "tools" / "study.py"
        result = subprocess.run([sys.executable, str(cli), "annotate", "--directory", str(self.root),
            "--decision", did, "--file", str(details), "--evidence-file", str(proof)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["added"])
        result = subprocess.run([sys.executable, str(cli), "mechanics", "--directory", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["actions_with_event_run_number"], 1)

    def test_shop_unattributed_changes_do_not_invent_rerolls(self):
        skip = {"kind": "interact", "code": 51}
        offers = [{"slot": 0, "effect_id": 1}, {"slot": 1, "effect_id": 2}]
        shop = state(screen_kind="shop", encounter_type=315, offers=offers, legal_actions=[skip])
        did = self.begin(before=shop, chosen=skip)
        self.store.finish(did, {**shop, "offers": [{"slot": 0, "effect_id": 3}, offers[1]]}, at=NOW)
        counts = coverage(self.store)["counts"]
        self.assertEqual(counts["shop_draws_observed"], 1)
        self.assertEqual(counts["shop_offer_changes_without_verified_draw"], 1)

    def test_missing_reroll_offers_use_next_same_shop_snapshot(self):
        offers = [{"slot": 0, "effect_id": 1}, {"slot": 1, "effect_id": 2}]
        reroll = {"kind": "interact", "code": 80, "mushroom_quote": 1, "quote_source": "synthetic"}
        shop = state(screen_kind="shop", encounter_type=315, offers=offers)
        did = self.begin(before={**shop, "legal_actions": [reroll]}, chosen=reroll)
        self.store.finish(did, {**shop, "offers": None, "mushrooms": 99}, at=NOW)
        buy = {"kind": "buy_blessing", "effect_id": 1, "key_price": 0}
        did2 = self.begin(before={**shop, "legal_actions": [buy], "mushrooms": 99}, chosen=buy)
        self.store.finish(did2, state(room=11, mushrooms=99), at=NOW)
        draws = [r for r in records(self.store) if r["record_type"] == "shop_draw"]
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[1]["origin"], "reroll_followup")

    def test_policy_and_budget_frozen_after_first_action(self):
        self.begin()
        with self.assertRaises(StudyError):
            self.store.configure(NOW, END, CLIENT_VERSION, 500, 500)
        with self.assertRaises(StudyError):
            self.store.set_policy(self.paid, "human", digest({"human": 1}))

    def test_live_gate_and_pilot_limit(self):
        with self.assertRaises(StudyError):
            arm_pilot(self.store, [self.paid], 1)
        self.store.set_meta("evidence", "live")
        class Driver:
            def observe(self, sid):
                return state(), None
            def execute(self, sid, chosen):
                return state(), None
        ctl = Controller(self.store, Driver(), live=True)
        with self.assertRaises(StudyError):
            ctl.step(self.paid, at=NOW)
        arm_pilot(self.store, [self.paid], 1)
        ctl.step(self.paid, at=NOW)
        with self.assertRaises(StudyError):
            ctl.step(self.paid, at=NOW)
        with self.assertRaises(StudyError):
            ctl.step(self.free, at=NOW)

    def test_numeric_validation_and_privacy_enum(self):
        for bad in (float("nan"), float("inf"), True, "password", -1):
            with self.assertRaises(StudyError):
                observation(state(hp_current=bad))
        with self.assertRaises(StudyError):
            observation(state(outcome="my-private-character"))

    def test_lock_released_on_close(self):
        with StudyLock(self.root / "lock"):
            with self.assertRaises(StudyError):
                with StudyLock(self.root / "lock"):
                    pass
        with StudyLock(self.root / "lock"):
            pass

    def test_import_cli_is_idempotent(self):
        record = {"evidence": "synthetic", "subject": self.paid, "at_utc": NOW,
                  "before": state(), "action": {"kind": "interact", "code": 20},
                  "after": state(hp_current=60000), "after_utc": NOW}
        path = self.root / "import.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        cli = Path(__file__).resolve().parents[1] / "tools" / "study.py"
        args = [sys.executable, str(cli), "import", "--directory", str(self.root), "--file", str(path)]
        first = subprocess.run(args, check=True, text=True, capture_output=True)
        second = subprocess.run(args, check=True, text=True, capture_output=True)
        self.assertEqual(json.loads(first.stdout)["imported"], 1)
        self.assertEqual(json.loads(second.stdout)["already_imported"], 1)
        self.assertEqual(self.store.status()["decisions"], 1)

    def test_partial_import_stops_without_resending(self):
        record = {"evidence": "synthetic", "subject": self.paid, "at_utc": NOW,
                  "before": state(), "action": {"kind": "interact", "code": 20}, "after": None}
        path = self.root / "import.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        cli = Path(__file__).resolve().parents[1] / "tools" / "study.py"
        args = [sys.executable, str(cli), "import", "--directory", str(self.root), "--file", str(path)]
        subprocess.run(args, check=True, text=True, capture_output=True)
        second = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(len(self.store.unresolved()), 1)

    def test_out_of_order_timing_is_rejected(self):
        did = self.begin()
        with self.assertRaises(StudyError):
            self.store.finish(did, state(), at="2026-10-07T11:59:59Z")
        self.store.finish(did, state(), at="2026-10-07T12:00:01Z")
        with self.assertRaises(StudyError):
            self.begin()

    def test_92_assignments_balanced_with_anonymous_world_blocks(self):
        other = Store(self.root / "roster.sqlite", create=True)
        try:
            other.initialize([{"study_id": str(i), "world": "secret-world-" + str(i // 2)} for i in range(92)])
            counts = [r[0] for r in other.db.execute("SELECT COUNT(*) FROM subjects GROUP BY arm,policy")]
            self.assertEqual(counts, [23] * 4)
            self.assertEqual(other.db.execute("SELECT COUNT(DISTINCT block) FROM subjects").fetchone()[0], 46)
            self.assertTrue(all(len(r[0]) == 32 for r in other.db.execute("SELECT block FROM subjects")))
        finally:
            other.close()

    @patch("sfld_study.store.utc_now", return_value=NOW)
    @patch("sfld_study.pilot.utc_now", return_value=NOW)
    @patch("sfld_study.pilot.datetime", wraps=datetime)
    def test_pilot_review_bound_to_state_sequence_and_time(self, pilot_datetime, _pilot_now, _store_now):
        packet = [0] * 50
        packet[2:5] = [70000, 70000, 100000]
        packet[15:20] = [10, 1, 10, 100, 1]
        # Use one clock for observations, review expiry and journal intents.
        # Mixing today's snapshots with fixed NOW stopped working after noon.
        now = datetime.fromisoformat(NOW)
        pilot_datetime.now.return_value = now
        epoch = int(now.timestamp())
        values = {"iadungeonsave": "/".join(map(str, packet)), "resources": "0/100/50",
                  "iadungeontime": f"8/{epoch-60}/{epoch+86400}/{epoch+86500}",
                  "timestamp": str(epoch), "serverversion": "31001"}
        class Session:
            calls = 0
            def execute(self, chosen):
                self.calls += 1
                return values
        session = Session()
        driver = ReviewedPilotDriver(self.store, self.root, lambda sid: (session, values), lambda: None)
        snapshot, raw = driver.snapshot(self.paid)
        review = {"subject": self.paid, "sequence": snapshot["sequence"], "fingerprint": snapshot["fingerprint"],
                  "at_utc": now.isoformat(), "ui_evidence_sha256": "a" * 64, "visible": {},
                  "legal_actions": [{"kind": "interact", "code": 20}]}
        path = self.root / "pilot" / (self.paid + ".review.json")
        def write_review():
            path.write_text(json.dumps(review), encoding="utf-8")
        write_review()
        visible, _ = driver.observe(self.paid)
        self.assertEqual(visible["legal_actions"][0]["code"], 20)
        self.store.set_meta("live_enabled", True)
        with self.assertRaises(StudyError):
            driver.execute(self.paid, visible["legal_actions"][0])
        did = self.store.begin(self.paid, visible, visible["legal_actions"][0], at=NOW)
        after, _ = driver.execute(self.paid, visible["legal_actions"][0])
        self.store.finish(did, after, at=NOW)
        with self.assertRaises(StudyError):
            driver.execute(self.paid, visible["legal_actions"][0])
        snapshot, _ = driver.snapshot(self.paid)
        review["sequence"] = snapshot["sequence"]
        review["fingerprint"] = snapshot["fingerprint"]
        review["visible"] = {"hp_current": 99999}
        write_review()
        with self.assertRaises(StudyError):
            driver.observe(self.paid)
        review["visible"] = {}
        review["at_utc"] = (now - timedelta(seconds=61)).isoformat()
        write_review()
        with self.assertRaises(StudyError):
            driver.observe(self.paid)
        review["at_utc"] = now.isoformat()
        review["sequence"] += 1
        write_review()
        with self.assertRaises(StudyError):
            driver.observe(self.paid)
        self.assertEqual(session.calls, 1)

    def test_server_version_change_after_send_holds_reservation(self):
        self.store.set_meta("evidence", "live")
        did = self.begin()
        self.assertEqual(self.store.finish(did, state(server_version="31500"), at=NOW), "unknown")
        self.assertEqual(self.store.subject(self.paid)["state"], "paused")


class ProtocolTests(unittest.TestCase):
    def test_server_time_controls_event_window_and_stale_offers_hidden(self):
        packet = [0] * 50
        packet[2:5] = [100, 100, 100]
        packet[15], packet[19] = 10, 1
        stones = [0] * 36
        stones[0], stones[18] = 3, 22
        values = {"iadungeonsave": "/".join(map(str, packet)), "iadungeontime": "8/1790000000/1790000100/1790000200",
                  "timestamp": "1790000050", "iadungeonsoulstones": "/".join(map(str, stones)),
                  "iamerchant": "4/80001/8/2/60070/6"}
        obs, _, _ = decode(values, CLIENT_VERSION, now_epoch=0)
        self.assertTrue(obs["event_open"])
        self.assertEqual(obs["event_seconds_left"], 50)
        self.assertEqual(obs["gems"], [3])
        self.assertIsNone(obs["gem_offers"])
        self.assertIsNone(obs["offers"])
        packet[19] = 904
        values["iadungeonsave"] = "/".join(map(str, packet))
        obs, _, _ = decode(values, CLIENT_VERSION)
        self.assertEqual(obs["gem_offers"], [22])

    def test_full_save_fields_and_no_hidden_future(self):
        data = [0] * 50
        data[0] = 987654321  # hidden seed
        data[2:5] = [70000, 71000, 100000]
        data[5], data[11], data[42] = 2, 80070, 8
        data[8:11], data[14], data[40:42], data[45:48] = [101, 102, 104], 50010, [60020, 70030], [5, 6, 7]
        data[15:19] = [11, 3, 51, 100]
        data[19:25] = [301, 317, 9999999, 1234567, 7654321, 112233]
        data[39] = 3
        values = {"iadungeonsave": "/".join(map(str, data)), "resources": "0/99/12345",
                  "sessionid": "TOKEN_SENTINEL", "ownplayername": "NAME_SENTINEL"}
        visible, raw, reason = decode(values, CLIENT_VERSION)
        self.assertEqual(visible["hp_current"], 70000)
        self.assertEqual(visible["encounter_type"], 317)
        self.assertEqual([e["power"] for e in visible["curses"]], [10, 20, 30])
        self.assertEqual(visible["blessings"][0]["remaining"], 8)
        self.assertIsNone(visible["legal_actions"])
        for sentinel in ("987654321", "9999999", "1234567", "7654321", "112233", "SENTINEL"):
            self.assertNotIn(sentinel, json.dumps(visible))
        self.assertNotIn("sessionid", raw)
        self.assertNotIn("ownplayername", raw)
        self.assertIn("987654321", raw["iadungeonsave"])

    def test_delta_and_version_changes_pause(self):
        obs, raw, why = decode({"iadungeonsave(8)": "123/456"}, CLIENT_VERSION)
        self.assertEqual(why, "delta_requires_review")
        self.assertIsNone(obs["hp_current"])
        self.assertIn("iadungeonsave(8)", raw)
        with self.assertRaises(StudyError):
            decode({}, "31.500")

    def test_commands_include_quoted_refill_price(self):
        self.assertEqual(command({"kind": "heal_partial", "theme": 8, "mushroom_quote": 7, "quote_source": "server"}),
                         ("IADungeonGetLife", "8/1/7"))
        self.assertEqual(command({"kind": "heal_full", "theme": 8, "mushroom_quote": 31, "quote_source": "ui"}),
                         ("IADungeonGetLife", "8/2/31"))
        self.assertEqual(command({"kind": "buy_blessing", "effect_id": 4, "key_price": 8}),
                         ("IADungeonMerchantBuy", "4/8"))
        self.assertEqual(command({"kind": "interact", "code": 80, "mushroom_quote": 1, "quote_source": "ui"}),
                         ("IADungeonInteract", "80"))
        self.assertEqual(command({"kind": "choose_gem", "gem_id": 6}), ("IADungeonSelectSoulStone", "6"))

    def test_bad_responses_do_not_echo_secrets(self):
        for body in (b"<html>secret</html>", b'error:secret', b'{"token":"secret"}', b"a:1&a:2"):
            with self.assertRaises(StudyError) as context:
                fields(body)
            self.assertNotIn("secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
