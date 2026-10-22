"""Measurement records, including denominators and unknown outcomes.

This is an observational export, not a fitted game model. Neither HP changes
nor disappearing effect slots establish the cause of an outcome by themselves.
"""
from __future__ import annotations

from collections import Counter

from .schema import digest


DOOR_ACTIONS = {1: 0, 2: 1, 5: 0, 6: 1, 9: 0, 10: 1, 13: 0, 14: 1}


def context(visible):
    return {key: visible[key] for key in (
        "game_version", "server_version", "theme", "ultimate", "room", "stage", "room_visit",
        "run_number_event", "run_number_lifetime", "level", "keys", "resources", "gems", "blessings", "curses",
        "encounter_type", "encounter_variant", "visual_variant", "container_kind", "fountain_variant")}


def delta(before, after, key):
    if after is None or before[key] is None or after[key] is None:
        return None
    return after[key] - before[key]


def records(store):
    from .report import transitions
    entries = {}
    shops = {}
    pending_rerolls = {}
    run_ordinals = {r["id"]: r["ordinal"] for r in store.db.execute("SELECT id,ordinal FROM runs")}

    def shop_record(row, visible, origin, force=False):
        if visible is None or visible["offers"] is None or not (
                visible["screen_kind"] == "shop" or visible["encounter_type"] in (315, 323)):
            return None
        shop = visible["shop"] or {}
        key = (row["subject"], row["run"], visible["room"], visible["encounter_type"], shop.get("visit"))
        signature = digest([{k: v for k, v in offer.items() if k != "strength"} for offer in visible["offers"]])
        old = shops.get(key)
        expected = pending_rerolls.pop(row["subject"], None)
        if expected == (row["run"], visible["room"], visible["encounter_type"]):
            force, origin = True, "reroll_followup"
        explicit_new_draw = old and shop.get("draw") is not None and old[1] is not None and shop["draw"] != old[1]
        if old and not force and not explicit_new_draw and old[0] == signature:
            return None
        record_type = "shop_draw" if old is None or force or explicit_new_draw else "shop_observation"
        shops[key] = (signature, shop.get("draw"))
        return {**base(row), "record_type": record_type, "context": context(visible),
                "origin": origin, "shop": visible["shop"], "offers": visible["offers"],
                "keys": visible["keys"], "mushrooms": visible["mushrooms"]}

    def base(row):
        return {"schema_version": 1, "evidence": row["evidence"], "subject": row["subject"],
                "block": row["block"], "run": row["run"], "observed_run_ordinal": run_ordinals.get(row["run"]),
                "decision": row["decision"], "sequence": row["sequence"], "arm": row["arm"],
                "at_seconds": row["at_seconds"], "after_seconds": row["after_seconds"],
                "policy": row["policy"], "action_status": row["outcome_status"],
                "annotation_count": row["annotation_count"]}

    for row in transitions(store):
        before, after, chosen = row["before"], row["after"], row["action"]
        code = chosen.get("code") if chosen["kind"] == "interact" else None
        sid = row["subject"]
        entry = entries.get(sid)
        if entry and (entry["run"] != row["run"] or before["room"] not in entry["rooms"]):
            entries.pop(sid, None)
            entry = None
        initial_draw = shop_record(row, before, "before_action")
        if initial_draw:
            yield initial_draw
        result = None if after is None else after["result"]
        details = {**base(row), "context": context(before), "action": chosen,
                   "before_hp": before["hp_current"], "after_hp": None if after is None else after["hp_current"],
                   "hp_max": before["hp_max"], "net_hp_change": delta(before, after, "hp_current"),
                   "net_key_change": delta(before, after, "keys"), "result": result,
                   "outcome": None if after is None else after["outcome"],
                   "damage_censored_at_death": row["damage_censored_at_death"],
                   "after_blessings": None if after is None else after["blessings"],
                   "after_curses": None if after is None else after["curses"],
                   "after_encounter_type": None if after is None else after["encounter_type"],
                   "after_container_kind": None if after is None else after["container_kind"]}
        if code in DOOR_ACTIONS:
            side = DOOR_ACTIONS[code]
            offered = before["doors"]
            picked = next((d for d in offered or [] if d["index"] == side), None)
            entry = {"decision": row["decision"], "run": row["run"], "doors": offered,
                     "chosen_index": side, "chosen_door": picked,
                     "rooms": [r for r in (before["room"], None if after is None else after["room"]) if r is not None]}
            details.update(record_type="door_choice", doors=offered, chosen_index=side, chosen_door=picked)
            if row["outcome_status"] == "confirmed":
                entries[sid] = entry
        else:
            details.update(record_type="interaction", entry=None if entry is None else {
                k: v for k, v in entry.items() if k not in {"run", "rooms"}})
        yield details
        if after is not None:
            # A reroll can return an identical pair: it is still a new draw.
            # A failed/uncertain reroll does not establish another random trial.
            if code != 80 or row["outcome_status"] == "confirmed":
                draw = shop_record(row, after, "reroll" if code == 80 else "after_action", force=code == 80)
                if draw:
                    yield draw
                elif code == 80:
                    pending_rerolls[sid] = (row["run"], before["room"], before["encounter_type"])
        if row["outcome_status"] != "confirmed" or (after and after["status"] == 102):
            entries.pop(sid, None)
            pending_rerolls.pop(sid, None)


def coverage(store):
    counts = Counter()
    for row in records(store):
        counts[row["record_type"] + "_records"] += 1
        if row["action_status"] != "confirmed":
            counts["records_from_unresolved_or_unapplied_actions"] += 1
            continue
        if row["record_type"] in {"shop_draw", "shop_observation"}:
            if row["record_type"] == "shop_observation":
                counts["shop_offer_changes_without_verified_draw"] += 1
                continue
            counts["shop_draws_observed"] += 1
            counts["shop_offers_observed"] += len(row["offers"])
            counts["shop_offers_with_reviewed_strength"] += sum(o["strength"] is not None for o in row["offers"])
            continue
        counts["confirmed_actions"] += 1
        ctx = row["context"]
        counts["actions_with_event_run_number"] += ctx["run_number_event"] is not None
        counts["actions_with_lifetime_run_number"] += ctx["run_number_lifetime"] is not None
        if row["record_type"] == "door_choice":
            counts["door_choices"] += 1
            counts["door_choices_with_complete_pair"] += (row["doors"] is not None
                and len(row["doors"]) == 2 and {d["index"] for d in row["doors"]} == {0, 1}
                and all(d["kind"] is not None or d["type"] is not None for d in row["doors"]))
            counts["door_choices_with_entry_monster_result"] += (row["result"] is not None
                and row["result"]["monster_on_entry"] is not None)
        outcome = row["result"] or {}
        counts["actions_with_complete_subevents"] += outcome.get("events_complete") is True
        counts["actions_with_reviewed_rps_outcome"] += outcome.get("rps_outcome") is not None
        if ctx["container_kind"] is not None and row["action"].get("code") in (40, 41):
            counts["container_opens"] += 1
            counts["container_opens_with_complete_subevents"] += outcome.get("events_complete") is True
    return {"schema_version": 1, "evidence": store.meta("evidence"), "counts": dict(sorted(counts.items())),
            "interpretation": "Coverage, not fitted odds. Unknown is not failure or an empty room. "
                "Door records count entry decisions, not every poll; unchosen contents are unobserved. "
                "Shop draws deduplicate repeated observations, but include identical confirmed rerolls. "
                "Record shop visit/draw identifiers when the room counter is ambiguous. "
                "Net HP loss is not necessarily damage from a single source. "
                "Observed run ordinal is not event or lifetime run history."}
