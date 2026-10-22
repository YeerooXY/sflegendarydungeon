"""Versioned client-derived LD fields and commands, not a server specification.

Source: local 31.001.260916.1 and 31.500.260929.1 MessageOrder attributes and serializers.
Do not merge delta records as if they were complete saves. Unverified menus
remain null; a pilot must check them against the UI before live automation.
"""
from __future__ import annotations

import base64
import re
import urllib.error
import urllib.parse
import urllib.request

from .schema import StudyError, action, observation

CLIENT_VERSION = "31.001.260916.1"
AUTOMATIC_CLIENT_VERSION = "31.500.260929.1"
SUPPORTED_CLIENT_VERSIONS = {CLIENT_VERSION, AUTOMATIC_CLIENT_VERSION}
MAX_BODY = 4 * 1024 * 1024
LD_FIELDS = {"iadungeonsave", "iadungeon20cost", "iadungeontime", "iadungeonsoulstones",
             "iamerchant", "iadungeonstats", "iadungeonstatstotal", "iadungeonchances",
             "iadungeon", "iapendingitems", "ialootitem"}


def fields(body):
    try:
        text = body.decode("utf-8").strip()
    except UnicodeError:
        raise StudyError("Unrecognized game response encoding.") from None
    if not text or text.startswith(("<", "{")) or len(body) > MAX_BODY:
        raise StudyError("Unrecognized game response format.")
    result = {}
    for field in text.split("&"):
        if not field:
            continue
        key, separator, value = field.partition(":")
        if not re.fullmatch(r"[a-zA-Z0-9_.(),-]+", key) or (not separator and key != "success"):
            raise StudyError("Unrecognized game field.")
        if key in result and result[key] != value:
            raise StudyError("Conflicting game fields.")
        result[key] = value
    # Preserve errors as a categorical failure only, never echo their contents.
    if any(re.split(r"[.(]", k)[0].lower() == "error" for k in result):
        raise StudyError("The game rejected the command; refresh state before continuing.")
    return result


def numeric(value, length=None):
    if not isinstance(value, str) or len(value) > 100000 or not re.fullmatch(r"-?\d+(?:/-?\d+)*", value):
        raise StudyError("Unrecognized numeric game field.")
    result = [int(x) for x in value.split("/")]
    if length is not None and len(result) != length:
        raise StudyError("Game field length changed; parser review required.")
    return result


def capture(values):
    """Retain only numeric LD-specific fields, never full login/player responses."""
    result = {}
    for key, value in values.items():
        if re.split(r"[.(]", key)[0] in LD_FIELDS:
            # Names/suffixes are preserved. Unknown encodings are omitted, not
            # misparsed or included as potentially sensitive arbitrary text.
            if re.fullmatch(r"-?\d+(?:/-?\d+)*", value or "") and len(value) <= 100000:
                result[key] = value
    return result


def effects(ids, packed, original):
    return [{"slot": i, "id": effect, "power": packed[i] % 10000,
             "remaining": packed[i] // 10000, "original_duration": original[i]}
            for i, effect in enumerate(ids) if effect != 0]


def decode(values, game_version, *, now_epoch=None):
    if game_version not in SUPPORTED_CLIENT_VERSIONS:
        raise StudyError("This client version has no reviewed protocol decoder yet.")
    raw = capture(values)
    data = {"game_version": game_version}
    if "serverversion" in values:
        data["server_version"] = values["serverversion"]
    if "resources" in values:
        resources = numeric(values["resources"])
        if len(resources) > 1:
            data["mushrooms"] = resources[1]
            data["resources"] = {"gold_silver": resources[0]}
    if "ownplayersavecharacter" in values:
        player = numeric(values["ownplayersavecharacter"])
        if len(player) > 3:
            data["level"] = player[3] & 65535
    # A delta may refer to byte offsets and flags. Refuse it until verified;
    # keeping the original numeric field lets a later importer reconstruct it.
    if any(k.startswith("iadungeonsave") and k != "iadungeonsave" for k in values):
        return observation(data), raw, "delta_requires_review"
    if "iadungeonsave" not in values:
        return observation(data), raw, "no_full_dungeon_save"
    save = numeric(values["iadungeonsave"], 50)
    # Wire long indices are byte offsets / 4, not IL2CPP object offsets.
    data.update(hp_current=save[2], hp_previous=save[3], hp_max=save[4], status=save[15],
                stage=save[16], room=save[17], rooms_total=save[18], keys=save[39],
                latest_effect_id=save[48], ultimate=save[49] == 1)
    if save[49] not in (0, 1):
        raise StudyError("Unrecognized dungeon mode.")
    data["blessings"] = effects(save[5:8], save[11:14], save[42:45])
    data["curses"] = effects(save[8:11], [save[14], save[40], save[41]], save[45:48])
    if 10 <= save[15] <= 12:
        data["encounter_type"] = save[19 + save[15] - 10]
    # No seed, unused save words, future step values or hidden outcomes enter
    # the observation. They remain in the private numeric capture only.
    if "iadungeontime" in values:
        times = numeric(values["iadungeontime"], 4)
        data["theme"] = times[0]
        if "timestamp" in values:
            server_time = numeric(values["timestamp"], 1)[0]
            if not 1000000000 <= server_time <= 4102444800:
                raise StudyError("Unrecognized server timestamp.")
            now_epoch = server_time
        if now_epoch is not None:
            data["event_open"] = times[1] <= now_epoch < times[2]
            data["event_seconds_left"] = max(0, times[2] - now_epoch)
    if "iadungeon20cost" in values:
        data["recovery"] = {"partial_cost": numeric(values["iadungeon20cost"], 1)[0]}
    if "iadungeonsoulstones" in values:
        gems = numeric(values["iadungeonsoulstones"], 36)
        data["gems"] = [gems[i] for i in (0, 6, 12) if gems[i] != 0]
        if data.get("encounter_type") == 904:
            data["gem_offers"] = [gems[i] for i in (18, 24, 30) if gems[i] != 0]
    # Merchant offers are visible only inside the merchant room. Login packets
    # can contain stale offers from a previous room, so don't expose those.
    if data.get("encounter_type") in (315, 323) and "iamerchant" in values:
        offers = numeric(values["iamerchant"])
        if len(offers) % 3:
            raise StudyError("Merchant record length changed.")
        price_key = "keys_received" if data["encounter_type"] == 323 else "key_price"
        data["offers"] = [{"slot": i // 3, "effect_id": offers[i], "power": offers[i + 1] % 10000,
                           "duration": offers[i + 1] // 10000, price_key: offers[i + 2]}
                          for i in range(0, len(offers), 3)]
    return observation(data), raw, "ui_menu_requires_review"


def command(chosen):
    selected = action(chosen)
    kind = selected["kind"]
    if kind == "interact":
        return "IADungeonInteract", str(selected["code"])
    if kind == "start":
        return "IADungeonStart", f"{selected['theme']}/0"
    if kind == "end":
        return "IADungeonEnd", ""
    if kind == "choose_gem":
        return "IADungeonSelectSoulStone", str(selected["gem_id"])
    if kind == "buy_blessing":
        return "IADungeonMerchantBuy", f"{selected['effect_id']}/{selected['key_price']}"
    if kind == "buy_curse":
        return "IADungeonDebuffMerchantBuy", f"{selected['effect_id']}/{selected['keys_received']}"
    if kind in {"heal_partial", "heal_full"}:
        if selected["mushroom_quote"] is None:
            raise StudyError("A refill needs an observed price.")
        size = 1 if kind == "heal_partial" else 2
        return "IADungeonGetLife", f"{selected['theme']}/{size}/{selected['mushroom_quote']}"
    raise StudyError("A wait is local; it must not generate a game command.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise StudyError("Unexpected redirect; no request was forwarded.")


class GameSession:
    """Bounded transport; explicit one-shot commands, no retries, no logging."""
    def __init__(self, domain, recorder=None):
        if not re.fullmatch(r"[a-z0-9-]+\.sfgame\.(?:eu|net)", domain):
            raise StudyError("Unexpected game domain.")
        self.domain = domain
        # Optional sfld_record Spool; it keeps numeric dungeon fields only.
        self.recorder = recorder
        self.session_id = None
        self.crypto_id = "0-00000000000000"
        self.player_id = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def _send(self, name, arguments, token=None):
        query = urllib.parse.urlencode({"req": name, "params": base64.b64encode(arguments.encode()).decode(), "sid": self.crypto_id})
        headers = {"Referer": f"https://{self.domain}/", "PG-Player": str(self.player_id)}
        if self.session_id:
            headers["PG-Session"] = self.session_id
        if token:
            headers.update({"Accept": "application/json", "Authorization": "Bearer " + token})
        request = urllib.request.Request(f"https://{self.domain}/cmd.php?{query}", headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(MAX_BODY + 1)
                if response.status != 200:
                    raise StudyError("Unexpected game HTTP status.")
        except (OSError, urllib.error.URLError):
            raise StudyError("Network outcome unknown; no automatic retry.") from None
        result = fields(body)
        if self.recorder is not None:
            try:
                self.recorder.packet(name, arguments, result, host=self.domain, player=self.player_id)
            except OSError:
                pass  # Recording must never change the outcome of a sent command.
        if "sessionid" in result:
            self.session_id = result["sessionid"]
        if "cryptoid" in result:
            self.crypto_id = result["cryptoid"].replace("|", "")
        if "ownplayersavecharacter" in result:
            self.player_id = numeric(result["ownplayersavecharacter"])[1]
        return result

    def login(self, account_id, character_id, token, expected_name):
        result = self._send("SFAccountCharLogin", f"{account_id}/{character_id}/unity3d_webglplayer//2700000000000", token)
        if result.get("ownplayername", "").casefold() != expected_name.casefold() or not self.session_id:
            raise StudyError("Authenticated character does not match the private binding.")
        return result

    def poll(self):
        return self._send("Poll", "")

    def execute(self, chosen):
        if not self.session_id:
            raise StudyError("No authenticated character session.")
        return self._send(*command(chosen))
