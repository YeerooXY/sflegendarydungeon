"""The public boundary: only typed, visible game data may cross it.

Raw responses, identities, notes and error messages never enter this schema.
Unknown fields are null; a known empty collection is []. Numeric game IDs are
preserved, including newly observed rooms, without inventing their mechanics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re


class StudyError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise StudyError("Expected a timestamp with an explicit UTC offset.") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def collector_files():
    package = Path(__file__).resolve().parent
    sources = sorted(package.glob("*.py")) + [package.parent / "study.py"]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}


def number(value, *, integer=False, minimum=0):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudyError("Expected a numeric game quantity.")
    if not math.isfinite(value) or value < minimum or (integer and int(value) != value):
        raise StudyError("Invalid numeric game quantity.")
    return int(value) if integer else value


def integer(value, minimum=0):
    return number(value, integer=True, minimum=minimum)


def enumeration(value, choices):
    if value is None:
        return None
    if not isinstance(value, str) or value not in choices:
        raise StudyError("Unrecognized categorical game quantity.")
    return value


def boolean(value):
    if value is not None and type(value) is not bool:
        raise StudyError("Expected a boolean game quantity.")
    return value


def version(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,5}", value)):
        raise StudyError("Expected the numeric game version.")
    return value


INTERACTIONS = {
    1: "left", 2: "right", 5: "left_key", 6: "right_key",
    9: "left_defuse", 10: "right_defuse", 13: "left_key_defuse", 14: "right_key_defuse",
    20: "fight", 21: "flee", 30: "trigger_trap", 31: "defuse_trap",
    40: "open", 41: "open_key", 42: "ignore_container", 50: "use_special",
    51: "ignore_special", 60: "collect", 61: "ignore_item", 70: "proceed",
    80: "reroll", 90: "rock", 91: "paper", 92: "scissors",
}
KINDS = {"interact", "start", "end", "choose_gem", "buy_blessing", "buy_curse",
         "heal_partial", "heal_full", "wait"}


def action(value):
    if not isinstance(value, dict):
        raise StudyError("An action must be an object.")
    kind = enumeration(value.get("kind"), KINDS)
    if kind is None:
        raise StudyError("An action needs a kind.")
    result = {"kind": kind}
    fields = {
        "interact": ("code",), "start": ("theme",), "end": (),
        "choose_gem": ("gem_id",), "buy_blessing": ("effect_id", "key_price"),
        "buy_curse": ("effect_id", "keys_received"), "heal_partial": ("theme",),
        "heal_full": ("theme",), "wait": ("seconds",),
    }[kind]
    for key in fields:
        result[key] = integer(value.get(key))
        if result[key] is None:
            raise StudyError("Action parameters are incomplete.")
    if kind == "interact" and result["code"] not in INTERACTIONS:
        raise StudyError("Unsupported interaction command.")
    if kind == "wait" and not 1 <= result["seconds"] <= 86400:
        raise StudyError("Wait must be between one second and one day.")
    result["mushroom_quote"] = integer(value.get("mushroom_quote"))
    result["quote_source"] = enumeration(value.get("quote_source"), {"server", "ui", "synthetic"})
    # Refill/reroll costs are never silently assumed to be zero.
    paid = kind in {"heal_partial", "heal_full"} or (kind == "interact" and result["code"] == 80)
    if not paid:
        if result["mushroom_quote"] not in (None, 0):
            raise StudyError("Unexpected mushroom charge for this command.")
        result["mushroom_quote"] = 0
    return result


def objects(value, fields, limit=100):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > limit:
        raise StudyError("Invalid game collection.")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise StudyError("Invalid game collection item.")
        result.append({key: convert(item.get(key)) for key, convert in fields.items()})
    return result


EFFECT = {
    "slot": integer, "id": integer, "power": number, "remaining": integer,
    "original_duration": integer,
    "counter": lambda x: enumeration(x, {"rooms", "encounters", "seconds", "uses"}),
    "source": lambda x: enumeration(x, {"monster", "barrel", "shop", "gem", "special", "door"}),
}
DOOR_KINDS = {"monster", "mystery", "locked", "double_locked", "unlocked", "epic",
              "golden", "shop", "cursed", "sacrifice", "blessing", "destiny", "hungry",
              "trial", "exit_trial", "boss", "wall", "other"}
CONTAINERS = {"wooden_box", "bronze_chest", "silver_chest", "epic_chest", "barrel",
              "warrior_skeleton", "mage_skeleton", "corpse", "mimic", "cursed_chest",
              "sacrificial_chest", "sated_chest", "prize_chest", "sarcophagus", "other"}
DOOR = {"index": integer, "type": integer, "trap": integer, "key_cost": integer,
        "resource_type": integer, "resource_cost": integer, "blocked": boolean,
        "kind": lambda x: enumeration(x, DOOR_KINDS), "visual_variant": integer,
        "trap_kind": lambda x: enumeration(x, {"none", "damage", "curse", "other"})}
OFFER = {"slot": integer, "effect_id": integer, "power": number, "duration": integer,
         "key_price": integer, "keys_received": integer,
         "strength": lambda x: enumeration(x, {"weak", "strong", "other"})}

EVENT = {
    "kind": lambda x: enumeration(x, {"damage", "heal", "effect_gained", "effect_removed",
        "item", "gold", "resource", "keys", "explosion", "monster_awakened", "no_reward"}),
    "source": lambda x: enumeration(x, {"fight", "failed_evade", "door", "trap", "poison",
        "container", "shop", "fountain", "rps", "lantern_zombie", "gem", "special", "other"}),
    "amount": number,
    "unit": lambda x: enumeration(x, {"hp_absolute", "hp_percentage_points", "gold_silver",
        "keys", "items", "resource_units"}),
    "effect_id": integer, "power": number, "duration": integer,
    "resource_type": integer,
    "reward_quality": lambda x: enumeration(x, {"normal", "epic", "legendary"}),
    "removal_reason": lambda x: enumeration(x, {"fountain", "expiry", "replacement", "death", "other"}),
    "damage_censored": boolean,
}


def record(value, fields):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StudyError("Expected structured game details.")
    return {key: convert(value.get(key)) for key, convert in fields.items()}


def supplement(original, added):
    """Fill unknown visible values; never rewrite recorded facts or whole lists."""
    if added is None:
        return original
    if original is None:
        return added
    if isinstance(original, dict) and isinstance(added, dict):
        return {key: supplement(value, added.get(key)) for key, value in original.items()}
    if isinstance(original, list) and isinstance(added, list) and len(original) == len(added):
        return [supplement(a, b) for a, b in zip(original, added)]
    if original != added:
        raise StudyError("Reviewed details conflict with a recorded observation.")
    return original


def observation(value):
    if not isinstance(value, dict):
        raise StudyError("An observation must be an object.")
    result = {key: integer(value.get(key)) for key in (
        "room", "stage", "rooms_total", "status", "hp_current", "hp_previous", "hp_max",
        "keys", "mushrooms", "level", "theme", "encounter_type", "encounter_variant",
        "prior_completions", "trial_tier", "pending_trial_reward", "latest_effect_id",
        "run_number_event", "run_number_lifetime", "room_visit", "visual_variant")}
    for key in ("run_number_event", "run_number_lifetime"):
        if result[key] == 0:
            raise StudyError("Run numbers start at one; use null for unknown history.")
    result["screen_kind"] = enumeration(value.get("screen_kind"), {
        "doors", "encounter", "shop", "gems", "recovery", "complete", "overview"})
    result["container_kind"] = enumeration(value.get("container_kind"), CONTAINERS)
    result["fountain_variant"] = enumeration(value.get("fountain_variant"), {"base", "cleansing", "other"})
    result["shop"] = record(value.get("shop"), {
        "visit": integer, "draw": integer, "reroll_quote": integer})
    result["result"] = record(value.get("result"), {
        "monster_on_entry": boolean, "container_exploded": boolean,
        "rps_opponent": lambda x: enumeration(x, {"rock", "paper", "scissors"}),
        "rps_outcome": lambda x: enumeration(x, {"win", "loss", "draw"}),
        "events_complete": boolean,
        "events": lambda x: objects(x, EVENT, 100)})
    result["game_version"] = version(value.get("game_version"))
    server_version = value.get("server_version")
    if server_version is not None and (not isinstance(server_version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,5}", server_version)):
        raise StudyError("Unrecognized server version.")
    result["server_version"] = server_version
    result["event_open"] = boolean(value.get("event_open"))
    result["ultimate"] = boolean(value.get("ultimate"))
    result["elapsed_seconds"] = number(value.get("elapsed_seconds"))
    result["event_seconds_left"] = number(value.get("event_seconds_left"))
    result["hp_display_percent"] = number(value.get("hp_display_percent"))
    for key in ("gems", "gem_offers"):
        items = value.get(key)
        if items is not None and (not isinstance(items, list) or len(items) > 30):
            raise StudyError("Invalid stone collection.")
        result[key] = None if items is None else [integer(x) for x in items]
    result["blessings"] = objects(value.get("blessings"), EFFECT, 3)
    result["curses"] = objects(value.get("curses"), EFFECT, 3)
    result["doors"] = objects(value.get("doors"), DOOR, 3)
    if result["doors"] is not None:
        indices = [d["index"] for d in result["doors"] if d["index"] is not None]
        if len(set(indices)) != len(indices) or any(i > 2 for i in indices):
            raise StudyError("Door positions must be distinct zero-based indices.")
    result["offers"] = objects(value.get("offers"), OFFER, 10)
    result["resources"] = None
    if value.get("resources") is not None:
        if not isinstance(value["resources"], dict):
            raise StudyError("Invalid external resources.")
        result["resources"] = {k: integer(value["resources"].get(k)) for k in (
            "gold_silver", "wood", "stone", "souls", "metal", "arcane", "hourglasses", "lucky_coins")}
    result["recovery"] = None
    if value.get("recovery") is not None:
        result["recovery"] = {k: number(value["recovery"].get(k)) for k in (
            "partial_cost", "full_cost", "seconds_to_entry", "seconds_to_full")}
    result["outcome"] = enumeration(value.get("outcome"), {
        "flee_success", "flee_failure", "fight", "death", "run_complete", "heal", "purchase",
        "reroll", "curse", "blessing", "cleansed", "reward", "no_change"})
    result["reward_quality"] = enumeration(value.get("reward_quality"), {"none", "normal", "epic", "legendary"})
    menu = value.get("legal_actions")
    if menu is not None and (not isinstance(menu, list) or len(menu) > 100):
        raise StudyError("Invalid action menu.")
    result["legal_actions"] = None if menu is None else [action(x) for x in menu]
    details = result["result"]
    if details and details["events_complete"] is True and (details["events"] is None
            or any(e["kind"] is None for e in details["events"])):
        raise StudyError("Complete outcomes require an explicit ordered event list.")
    if result["hp_max"] is not None and result["hp_current"] is not None and result["hp_current"] > result["hp_max"]:
        raise StudyError("Current health exceeds maximum health.")
    if result["hp_display_percent"] is not None and result["hp_display_percent"] > 100:
        raise StudyError("Invalid displayed health percentage.")
    return result


def spending_category(chosen):
    if chosen["kind"] in {"heal_partial", "heal_full"}:
        return "recovery"
    if chosen["kind"] == "interact" and chosen["code"] == 80:
        return "reroll"
    return "other"
