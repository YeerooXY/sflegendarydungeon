"""A one-action live pilot bound to a freshly checked UI menu.

No automatic room menus are claimed verified. This driver captures the full
numeric protocol and asks for a reviewed visible menu for each decision. A
future versioned automatic UI projection can replace this driver without
changing the ledger, policy, budgets or export format.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .protocol import CLIENT_VERSION, capture, decode
from .schema import StudyError, action, canonical, digest, observation, supplement, timestamp, utc_now


def fingerprint(visible):
    stable = observation(visible)
    for key in ("elapsed_seconds", "event_seconds_left", "legal_actions"):
        stable[key] = None
    return digest(stable)


class ReviewedPilotDriver:
    def __init__(self, store, directory, session_factory, guard):
        self.store = store
        self.directory = Path(directory)
        self.session_factory = session_factory
        self.guard = guard
        self.active = None
        self.reviewed_menu = None
        self.pilot_dir = self.directory / "pilot"
        self.pilot_dir.mkdir(exist_ok=True)

    def close(self):
        if callable(getattr(self.session_factory, "close", None)):
            self.session_factory.close()
        self.active = None
        self.reviewed_menu = None

    def _decode(self, subject, values):
        try:
            return decode(values, self.store.meta("game_version") or CLIENT_VERSION)
        except StudyError:
            self.store.observe(subject, {"game_version": self.store.meta("game_version")}, private=capture(values))
            raise

    def snapshot(self, subject):
        self.active = None
        self.reviewed_menu = None
        self.guard()
        self.store.subject(subject)
        session, values = self.session_factory(subject)
        visible, raw, reason = self._decode(subject, values)
        self.store.observe(subject, visible, private=raw)
        snapshot = {"schema_version": 1, "subject": subject, "at_utc": utc_now(),
                    "sequence": self.store.subject(subject)["sequence"] + 1,
                    "fingerprint": fingerprint(visible), "observation": visible, "review_reason": reason}
        path = self.pilot_dir / (subject + ".snapshot.json")
        path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        self.active = (subject, session, snapshot)
        return snapshot, raw

    def observe(self, subject):
        snapshot, raw = self.snapshot(subject)
        if snapshot["review_reason"] != "ui_menu_requires_review":
            raise StudyError("A complete version-matched dungeon save is required for a live action.")
        path = self.pilot_dir / (subject + ".review.json")
        if not path.is_file() or path.stat().st_size > 128000:
            raise StudyError("Pilot snapshot saved. A current UI-reviewed action menu is required.")
        review = json.loads(path.read_text(encoding="utf-8-sig"))
        age = (datetime.now(timezone.utc) - timestamp(review.get("at_utc"))).total_seconds()
        if not 0 <= age <= 60 or review.get("sequence") != snapshot["sequence"]:
            raise StudyError("The UI review expired or belongs to another decision.")
        if review.get("fingerprint") != snapshot["fingerprint"] or review.get("subject") != subject:
            raise StudyError("The reviewed screen no longer matches the observed character state.")
        proof = review.get("ui_evidence_sha256")
        if not isinstance(proof, str) or len(proof) != 64 or any(x not in "0123456789abcdef" for x in proof):
            raise StudyError("Hash a UI screenshot/recording as the pilot evidence.")
        # Only UI-visible fields absent from the decoder can be added. A review
        # cannot overwrite known HP, keys, balance, version or event status.
        extra = observation(review.get("visible", {}))
        merged = supplement(snapshot["observation"], extra)
        merged["legal_actions"] = review.get("legal_actions")
        merged = observation(merged)
        if not merged["legal_actions"]:
            raise StudyError("The reviewed screen has no action menu.")
        self.store.audit(subject, "ui_review", {"sequence": snapshot["sequence"], "evidence_sha256": proof,
                                                "fingerprint": snapshot["fingerprint"]})
        self.reviewed_menu = merged["legal_actions"]
        return merged, raw

    def execute(self, subject, chosen):
        self.guard()
        if (self.directory / "STOP").exists() or not self.store.meta("live_enabled"):
            raise StudyError("Live controller was stopped before sending.")
        if not self.active or self.active[0] != subject:
            raise StudyError("No matching fresh character session.")
        _, session, snapshot = self.active
        if action(chosen) not in (self.reviewed_menu or []):
            raise StudyError("The chosen action lacks a valid current UI review.")
        pending = self.store.unresolved(subject)
        if (len(pending) != 1 or pending[0]["state"] != "pending"
                or pending[0]["sequence"] != snapshot["sequence"]
                or pending[0]["chosen"] != canonical(action(chosen))
                or self.store.subject(subject)["state"] != "ready"):
            raise StudyError("The live action must have a matching committed journal intent.")
        self.active = None  # consume before sending, including timeouts
        self.reviewed_menu = None
        if (datetime.now(timezone.utc) - timestamp(snapshot["at_utc"])).total_seconds() > 60:
            raise StudyError("The character session's decision snapshot expired.")
        values = session.execute(chosen)
        visible, raw, reason = self._decode(subject, values)
        if reason in {"delta_requires_review", "no_full_dungeon_save"} or any(
                visible[k] is None for k in ("mushrooms", "server_version", "hp_current", "hp_max", "room", "status")):
            # Capture the delta, then obtain a full read. Never repeat the action.
            self.store.observe(subject, visible, private=raw)
            self.guard()
            _, fresh_values = self.session_factory(subject)
            visible, refreshed, reason = self._decode(subject, fresh_values)
            raw = {"action_response": raw, "read_only_refresh": refreshed}
        return visible, raw
