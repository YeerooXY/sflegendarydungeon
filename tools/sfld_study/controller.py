"""Policies use visible menus; the controller owns budgets and the action journal."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import random
import time

from .schema import StudyError, action, digest, observation, timestamp, utc_now


def builtin_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def choose(policy, visible, seed, sequence):
    menu = visible["legal_actions"] or []
    if not menu:
        raise StudyError("No verified action menu; this screen needs review.")
    affordable = [a for a in menu if a["mushroom_quote"] is not None
                  and a["mushroom_quote"] <= visible["budget_remaining"]
                  and (not a["mushroom_quote"] or (visible["mushrooms"] is not None
                       and a["mushroom_quote"] <= visible["mushrooms"]))]
    if not affordable:
        raise StudyError("No affordable verified action; recovery observation needed.")
    rng = random.Random(f"{seed}:{sequence}")
    if policy == "random":
        return rng.choice(affordable)
    if policy != "conservative":
        raise StudyError("This subject needs its assigned external policy.")
    # This is a declared baseline, not a claim that these choices are optimal.
    rank = {70: 1, 60: 1, 21: 2, 51: 3, 42: 3, 1: 4, 2: 4, 20: 6, 80: 12}
    def score(a):
        if a["kind"] == "wait":
            return 10
        if a["mushroom_quote"]:
            return 20 + a["mushroom_quote"]
        return rank.get(a.get("code"), 5)
    best = min(score(a) for a in affordable)
    return rng.choice([a for a in affordable if score(a) == best])


class ExternalPolicy:
    """Fresh Python module per subject event; persist optional JSON memory.

Only load code you trust. A Python module is not a security sandbox. The callback
receives no transport, credentials, raw packets, study bindings or hidden seed.
Return {action: <one exact menu item>, memory: <JSON>} from choose(visible,memory).
"""
    def __init__(self, path, expected_hash):
        self.path = Path(path).resolve()
        self.expected_hash = expected_hash
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != expected_hash:
            raise StudyError("External policy hash does not match the assignment.")
        spec = importlib.util.spec_from_file_location("study_participant_" + expected_hash, self.path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def choose(self, visible, memory):
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.expected_hash:
            raise StudyError("Policy changed after the event began.")
        result = self.module.choose(json.loads(json.dumps(visible)), memory)
        if not isinstance(result, dict) or "action" not in result:
            raise StudyError("Policy did not return an action and optional memory.")
        if len(json.dumps(result.get("memory"))) > 65536:
            raise StudyError("Policy memory exceeds the limit.")
        return result


class Controller:
    def __init__(self, store, driver, *, live=False):
        self.store, self.driver, self.live = store, driver, live

    def recommend(self, subject, *, external=None, at=None):
        """Observe and suggest without reserving currency or advancing memory."""
        row=self.store.subject(subject)
        if row['state']!='ready' or self.store.unresolved(subject):
            raise StudyError('Character is paused or has an unresolved action.')
        if self.live != (self.store.meta('evidence')=='live'):
            raise StudyError('Controller and study evidence modes differ.')
        before,raw=self.driver.observe(subject);before=observation(before)
        if before['prior_completions'] is None:
            completed=self.store.db.execute("SELECT COUNT(*) FROM runs WHERE subject=? AND state='complete'",(subject,)).fetchone()[0]
            before['prior_completions']=row['prior_completions']+completed
        self.store.observe(subject,before,at=at,private=raw)
        spend,all_spend=self.store.spent(subject),self.store.spent()
        visible={**before,'budget_remaining':min(row['budget']-spend['charged']-spend['reserved'],
            self.store.meta('global_budget')-all_spend['charged']-all_spend['reserved']), 'decision_number':row['sequence']+1}
        if external is not None:
            if row['policy'] not in {'external','learned'} or external.expected_hash!=row['policy_hash']:
                raise StudyError('External policy does not match the frozen assignment.')
            chosen=external.choose(visible,self.store.meta('memory:'+subject))['action']
        else:
            if row['policy_hash']!=builtin_hash():raise StudyError('Builtin policy changed since assignment.')
            chosen=choose(row['policy'],visible,row['policy_seed'],row['sequence']+1)
        chosen=action(chosen)
        if chosen not in (visible['legal_actions'] or []):raise StudyError('Recommendation is not in the observed legal menu.')
        if chosen['mushroom_quote'] is None or chosen['mushroom_quote']>visible['budget_remaining']:
            raise StudyError('Recommendation has an unknown or unaffordable price.')
        if chosen['mushroom_quote'] and (visible['mushrooms'] is None or chosen['mushroom_quote']>visible['mushrooms']):
            raise StudyError('Recommendation needs a sufficient observed mushroom balance.')
        return {'subject':subject,'sequence':row['sequence'],'policy_hash':row['policy_hash'],
                'fingerprint':recommendation_fingerprint(visible),'action':chosen,'visible':visible}

    def step(self, subject, *, selected=None, external=None, at=None, expected=None):
        row = self.store.subject(subject)
        if self.store.unresolved(subject):
            raise StudyError("An earlier action has an unknown outcome; never resend it.")
        if self.live:
            if self.store.meta("evidence") != "live" or not self.store.meta("live_enabled"):
                raise StudyError("Live execution is disabled.")
            if subject not in self.store.meta("pilot_subjects"):
                raise StudyError("Subject is outside the armed pilot.")
            baseline = self.store.meta("pilot_baselines") or {}
            if row["sequence"] - baseline.get(subject, 0) >= self.store.meta("pilot_action_limit"):
                raise StudyError("The pilot action limit has been reached.")
        elif self.store.meta("evidence") == "live":
            raise StudyError("A live study cannot accept simulated controller results.")
        if row["state"] != "ready":
            raise StudyError("Subject is not ready.")
        before, raw_before = self.driver.observe(subject)
        before = observation(before)
        if before["prior_completions"] is None:
            # Explicit roster history plus observed completions, never a guessed
            # first/later-run stone pool.
            completed = self.store.db.execute("SELECT COUNT(*) FROM runs WHERE subject=? AND state='complete'", (subject,)).fetchone()[0]
            before["prior_completions"] = row["prior_completions"] + completed
        self.store.observe(subject, before, at=at, private=raw_before)
        spend, all_spend = self.store.spent(subject), self.store.spent()
        visible = {**before, "budget_remaining": min(row["budget"] - spend["charged"] - spend["reserved"],
                   self.store.meta("global_budget") - all_spend["charged"] - all_spend["reserved"]),
                   "decision_number": row["sequence"] + 1}
        if expected is not None and (expected['subject']!=subject or expected['sequence']!=row['sequence'] or
                expected['policy_hash']!=row['policy_hash'] or expected['fingerprint']!=recommendation_fingerprint(visible)):
            raise StudyError('The character state changed; request a fresh recommendation.')
        memory = None
        if selected is None:
            if external is not None:
                if row["policy"] not in {"external", "learned"} or external.expected_hash != row["policy_hash"]:
                    raise StudyError("External policy does not match the frozen assignment.")
                result = external.choose(visible, self.store.meta("memory:" + subject))
                selected, memory = result["action"], result.get("memory")
            else:
                if row["policy_hash"] != builtin_hash():
                    raise StudyError("Builtin controller changed; freeze a new study or restore its code.")
                selected = choose(row["policy"], visible, row["policy_seed"], row["sequence"] + 1)
        elif row["policy"] != "human":
            raise StudyError("Explicit manual choices require a human policy assignment.")
        selected = action(selected)
        if expected is not None and selected!=expected['action']:
            raise StudyError('The recommendation changed; review the new action before sending.')
        # Journal commit happens BEFORE the first mutating network request.
        did = self.store.begin(subject, before, selected, at=at, private=raw_before,
                               expected_sequence=row["sequence"])
        if memory is not None:
            with self.store.transaction():
                self.store.set_meta("memory:" + subject, memory)
        try:
            if selected["kind"] == "wait":
                after, raw_after = before, None
            else:
                after, raw_after = self.driver.execute(subject, selected)
            state = self.store.finish(did, after, at=at, private=raw_after)
        except BaseException:
            # Includes Ctrl+C after the request left the process. Do not replace
            # the journaled outcome with a retry or print raw exception text.
            self.store.unknown(did)
            raise StudyError("Action outcome is uncertain. Subject paused; inspect and reconcile without retrying.") from None
        return {"decision": did, "subject": subject, "state": state}


def recommendation_fingerprint(visible):
    stable=observation(visible)
    # Wall-clock counters advance between observation and a click. The fresh
    # policy still rechecks them, and execution requires the same recommendation.
    stable['elapsed_seconds']=None;stable['event_seconds_left']=None
    return digest({'visible':stable,'budget_remaining':visible['budget_remaining'],
                   'decision_number':visible['decision_number']})


class StudyLock:
    """OS-held lock; released on crashes. The file itself is not a stale lock."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        try:
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise StudyError("Another controller holds this study's lock.") from None
        return self

    def __exit__(self, *unused):
        self.stream.close()


def arm_pilot(store, subjects, limit):
    if not 1 <= len(subjects) <= 4 or len(set(subjects)) != len(subjects) or not 1 <= limit <= 20:
        raise StudyError("A pilot supports one to four subjects and one to twenty decisions each.")
    with store.transaction():
        if store.meta("evidence") != "live":
            raise StudyError("Only a live study may be armed.")
        rows = [store.subject(s) for s in subjects]
        if any(r["start_utc"] is None or r["end_utc"] is None for r in rows):
            raise StudyError("Configure the confirmed event window before arming.")
        if store.unresolved():
            raise StudyError("Resolve uncertain outcomes before arming.")
        store.set_meta("pilot_subjects", subjects)
        store.set_meta("pilot_baselines", {r["id"]: r["sequence"] for r in rows})
        store.set_meta("pilot_action_limit", limit)
        store.set_meta("live_enabled", True)
        store.audit(None, "pilot_armed", {"subjects": subjects, "limit": limit})


def run_round_robin(store, driver, subjects, *, live, max_actions, stop_file, external_path=None):
    """Bounded serial scheduling: one action per ready subject per round.

Recovery waits are stored in SQLite and serviced on a later round/invocation.
No busy polling, concurrent character sessions, scheduler installation or wake.
"""
    results, policies = [], {}
    controller = Controller(store, driver, live=live)
    while len(results) < max_actions:
        progress = False
        for sid in subjects:
            if Path(stop_file).exists():
                store.pause(reason="stop_file")
                return results
            row = store.subject(sid)
            now = timestamp(utc_now())
            if row["end_utc"] and now >= timestamp(row["end_utc"]):
                if row["state"] != "closed":
                    store.close_event(sid)
                continue
            if row["state"] != "ready" or (row["start_utc"] and now < timestamp(row["start_utc"])):
                continue
            if row["wait_until"] and now < timestamp(row["wait_until"]):
                continue
            try:
                external = None
                if row["policy"] in {"external", "learned"}:
                    if external_path is None:
                        raise StudyError("External policy file missing.")
                    if sid not in policies:
                        policies[sid] = ExternalPolicy(external_path, row["policy_hash"])
                    external = policies[sid]
                results.append(controller.step(sid, external=external))
                progress = True
            except StudyError:
                store.pause(sid, "controller_review_required")
                results.append({"subject": sid, "state": "paused"})
            if len(results) >= max_actions:
                break
            if live:
                time.sleep(2)
        if not progress:
            break
    return results
