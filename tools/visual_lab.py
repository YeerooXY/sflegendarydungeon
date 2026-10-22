"""Local browser interface to the C++ Legendary Dungeon engine (standard library only)."""
from __future__ import annotations
import argparse
import atexit
import hashlib
import json
import math
from pathlib import Path
import queue
import secrets
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
STRATEGIES = ("baseline", "strategic", "key-stockpile")
MAX_BODY = 2 * 1024 * 1024
POLICY_BOOLEANS = ("survival_aware", "adaptive_gems", "practical_tactics")
POLICY_INTEGERS = (("early_key_target", 100), ("shop_allowance", 1000),
                   ("shop_key_reserve", 100), ("shop_mushroom_reserve", 1000000))


def default_engine() -> Path:
    names = ('build-ui-decisions/Release/sfld.exe','build-ui-feedback/Release/sfld.exe','build-adaptive-stones/Release/sfld.exe','build-v0612/Release/sfld.exe','build/Release/sfld.exe')
    if __import__('os').name != 'nt': names = ('build-ui-decisions/sfld','build-ui-feedback/sfld','build-adaptive-stones/sfld','build/sfld')
    return next((REPO/name for name in names if (REPO/name).is_file()),REPO/names[-1])


def settings(raw: dict) -> dict:
    result = {}
    result['neural_adviser']=raw.get('neural_adviser',True)
    if type(result['neural_adviser']) is not bool:raise ValueError('Invalid neural_adviser')
    result['final_stone_adviser']=raw.get('final_stone_adviser',True)
    if type(result['final_stone_adviser']) is not bool:raise ValueError('Invalid final_stone_adviser')
    result['stone_advice_samples']=raw.get('stone_advice_samples',2048)
    if type(result['stone_advice_samples']) is not int or result['stone_advice_samples'] not in (512,2048,4096):
        raise ValueError('stone_advice_samples must be 512, 2048 or 4096')
    scenario = raw.get("scenario","default")
    if scenario not in ("default","mushroom-school"): raise ValueError("Unknown damage scenario")
    result["scenario"] = scenario
    target = raw.get("revive_hp", .2)
    if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not .2 <= target <= 1:
        raise ValueError("Invalid revive_hp")
    result["revive_hp"] = float(target)
    patient = raw.get("patient_boss_hp", 0)
    if isinstance(patient, bool) or not isinstance(patient, (int, float)) or not math.isfinite(patient) or (patient != 0 and not .2 <= patient <= 1):
        raise ValueError("Invalid patient_boss_hp")
    result["patient_boss_hp"] = float(patient)
    for key in ("sleep_until_hour", "event_start_hour"):
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 24:
            raise ValueError(f"Invalid {key}")
        result[key] = float(value)
    for key, default, low, high in [("seed",42,0,2**53-1),("budget",0,0,1000000),("run_number",1,1,1000000),("deadline_hours",168,1,8760),
                                   *((key, 0, 0, high) for key, high in POLICY_INTEGERS)]:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or int(value) != value:
            raise ValueError(f"Invalid {key}")
        result[key] = int(value)
    wallet = raw.get('wallet_mushrooms')
    if wallet is not None and (type(wallet) is not int or not result['budget'] <= wallet <= 1000000000):
        raise ValueError('Starting mushroom balance must be at least the event budget')
    result['wallet_mushrooms'] = wallet
    for key in POLICY_BOOLEANS:
        value = raw.get(key, False)
        if type(value) is not bool:
            raise ValueError(f"Invalid {key}")
        result[key] = value
    for key, maximum in [("initial_state",65536),("learned_policy",250000)]:
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
            raise ValueError(f"Invalid {key}")
        result[key] = value
    return result


class Session:
    def __init__(self, exe: Path, profile: Path, config: dict):
        self.exe, self.profile, self.config = exe, profile, settings(config)
        self.lock = threading.RLock()
        self.temp = tempfile.TemporaryDirectory(prefix="sfld-visual-")
        self.directory = Path(self.temp.name)
        self.history: list[dict] = []
        self.journal: list[dict] = []
        self.touched = time.monotonic()
        self.process = None
        self.stone_advice_cache = {}
        self.archived_stone_advice_records = {}
        try:
            self._start()
        except Exception:
            self.close(); raise

    def arguments(self, command: str, *, policy_options: bool = True) -> list[str]:
        c = self.config
        args = [str(self.exe),command,"--allow-assumptions","--profile",str(self.profile),
                "--budget",str(c["budget"]),"--deadline-hours",str(c["deadline_hours"]),
                "--seed",str(c["seed"]),"--run-number",str(c["run_number"]),"--max-actions","100000"]
        if c["sleep_until_hour"] or c["event_start_hour"]:
            args += ["--sleep-until-hour",str(c["sleep_until_hour"]),"--event-start-hour",str(c["event_start_hour"])]
        if policy_options:
            args += ["--revive-hp", str(c["revive_hp"])]
            args += ["--patient-boss-hp", str(c["patient_boss_hp"])]
            for key in POLICY_BOOLEANS:
                args += ["--" + key.replace("_", "-"), str(c[key]).lower()]
            for key, _ in POLICY_INTEGERS:
                args += ["--" + key.replace("_", "-"), str(c[key])]
        if c["initial_state"]:
            path = self.directory / "start.state"
            path.write_text(c["initial_state"], encoding="utf-8")
            args += ["--state",str(path)]
        if c["learned_policy"]:
            path = self.directory / "learned.policy"
            path.write_text(c["learned_policy"], encoding="utf-8")
            if command == "agent": args += ["--advisor-policy",str(path)]
        return args

    def _start(self) -> None:
        # Presentation inventory is derived only from native reward outcomes.
        # Replaying/undoing rebuilds it, including ordinary items (not part of
        # the engine's completion objective). No equipment stats are invented.
        self.kept_items: list[dict] = []
        self.loot_counts = {"ordinary":0,"epic":0,"legendary":0}
        self.lines: queue.Queue = queue.Queue()
        self.errors = (self.directory / "engine.log").open("w+", encoding="utf-8")
        self.process = subprocess.Popen(self.arguments("agent") + ["--ui-hints","true"],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.errors,text=True,encoding="utf-8",bufsize=1,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        process, lines = self.process, self.lines
        def reader():
            try:
                for line in process.stdout: lines.put(line)
            finally: lines.put(None)
        threading.Thread(target=reader,daemon=True).start()
        self.observation = self._read()

    def _read_wire(self) -> dict:
        try: line = self.lines.get(timeout=15)
        except queue.Empty:
            self._stop(); raise ValueError("The engine did not respond; start a new run.")
        if line is None:
            self.errors.flush()
            details = (self.directory / "engine.log").read_text(encoding="utf-8")[-1600:]
            raise ValueError("Engine stopped. " + details.strip())
        return json.loads(line)

    def _read(self) -> dict:
        result=self._read_wire()
        # Retain the unannotated native observation for exact planning parity.
        self.raw_observation=json.loads(json.dumps(result))
        if self.config['neural_adviser'] and 'suggestions' in result and not result['terminal']:
            try:
                from sfld_rl.visual_policy import advise
                advise(result,self.config,self.profile)
            except ImportError:
                result['adviser_unavailable']='Neural adviser needs the RL Python environment; showing the rule adviser.'
        if 'suggestions' in result and 'combat_advice' not in result:
            from sfld_rl.visual_advice import annotate, correct_action
            annotate(result)
            for suggestion in result['suggestions']:
                suggestion['action'], suggestion['reason'], suggestion['adjustment'] = correct_action(result, suggestion['action'])
        return result

    def _stop(self) -> None:
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
            self.process.stdin.close(); self.process.stdout.close()
            self.process = None
        if hasattr(self,"errors") and not self.errors.closed: self.errors.close()

    def close(self) -> None:
        with self.lock:
            self._stop(); self.temp.cleanup()

    def payload(self) -> dict:
        return {"observation":self.observation,"journal":self.journal[-60:],"steps":len(self.history),
                "loot":{"mode":"keep_all","counts":dict(self.loot_counts),"recent":self.kept_items[-12:]},
                "completed_runs":[{"run":j["run"],"hours":j["run_hours"],"mushrooms":j["run_mushrooms"]}
                                  for j in self.journal if any(o["kind"]=="completed" for o in j.get("outcomes",[]))],
                "settings":{k:v for k,v in self.config.items() if k not in ("initial_state","learned_policy")},
                "has_learned_policy":bool(self.config["learned_policy"]),
                "stone_advice":self.current_stone_advice()}

    def stone_state_key(self):
        raw={k:v for k,v in self.raw_observation.items() if k not in ('outcomes','suggestions')}
        return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def current_stone_advice(self):
        o=self.observation
        if not self.config['final_stone_adviser'] or o['terminal'] or o['phase']!='gems' or o['room']!=76 or len(o['gems'])!=2:
            return None
        key=self.stone_state_key()
        return self.stone_advice_cache.get(key,dict(status='pending',state_key=key,
            samples_per_offer=self.config['stone_advice_samples']))

    def final_stone_advice(self, steps, state_key=None, retry=False):
        with self.lock:
            self.touched=time.monotonic()
            if type(retry) is not bool:raise ValueError('Invalid stone comparison retry')
            if type(steps) is not int or steps!=len(self.history):
                raise ValueError('Stone choice changed; refresh the current position')
            current=self.current_stone_advice()
            if current is None:raise ValueError('No final stone comparison is available here')
            if state_key is not None and state_key!=current['state_key']:
                raise ValueError('Stone choice changed; refresh the current position')
            if current['status']=='ready' or (current['status']=='unavailable' and not retry):return self.payload()
            key=current['state_key']
            try:
                self.process.stdin.write('planning_snapshot\n');self.process.stdin.flush()
                reply=self._read_wire()
                if 'error' in reply:
                    # Older agents return an error followed by their unchanged
                    # observation. Drain both so manual play remains usable.
                    self._read_wire()
                    raise RuntimeError('Restart with the updated simulator to enable stone comparisons')
                from sfld_rl.runtime_stone_advice import compare
                library=self.exe.with_name('sfld_rl.dll' if __import__('os').name=='nt' else 'libsfld_rl.so')
                result=compare(reply['planning_state'],self.raw_observation,library,self.profile,self.config)
            except (ImportError,OSError,RuntimeError,ValueError,KeyError) as error:
                result=dict(status='unavailable',message=str(error))
            self.stone_advice_cache[key]=dict(**result,state_key=key)
            return self.payload()

    def step(self, raw: dict) -> dict:
        with self.lock:
            self.touched = time.monotonic()
            action = {"kind":raw.get("kind"),"index":raw.get("index",0),"value":raw.get("value",0)}
            if self.observation["terminal"]: raise ValueError("This event has ended. Start a new event or undo a step.")
            if len(self.history) >= 10000: raise ValueError("Visual session reached its 10,000-action limit.")
            if action not in self.observation["legal_actions"] and not any(action==s["action"] for s in self.observation["suggestions"]):
                raise ValueError("That action is not available in the current room.")
            before = self.observation
            stone_advice=self.current_stone_advice() if action['kind']=='choose_gem' else None
            self.process.stdin.write(f"{action['kind']} {action['index']} {action['value']:.17g}\n"); self.process.stdin.flush()
            after = self._read()
            if "error" in after:
                self.observation = self._read(); raise ValueError(after["error"])
            self.history.append(action)
            self.journal.append({"step":len(self.history),"action":action,"room":before["room"],"run":before["run_number"],
                "phase":before["phase"],"encounter":before.get("encounter"),
                "stones_before":before["gems"],
                "active_blessings_before":[e for e in before["blessings"] if e["starts_at"]<=before["turn"]],
                "active_curses_before":[e for e in before["curses"] if e["starts_at"]<=before["turn"]],
                "choice":before.get("doors",[])[action["index"]].get("kind") if action["kind"]=="choose_door" else None,
                "offer":before.get("offers",[])[action["index"]] if action["kind"]=="buy" else None,
                "gem":before.get("gem_offers",[])[action["index"]] if action["kind"]=="choose_gem" else None,
                "price":before.get("heal_step_price") if action["kind"]=="heal_step" else before.get("heal_full_price") if action["kind"]=="heal_full" else None,
                "outcomes":after.get("outcomes",[]),"next_room":after["room"],"next_phase":after["phase"],
                "revealed":after.get("encounter") if after["phase"]=="encounter" and action["kind"]=="choose_door" else None,
                "run_hours":after["run_elapsed_hours"],"run_mushrooms":after["run_mushrooms_spent"],
                "hp_before":before["hp"],"hp_after":after["hp"],"keys_delta":after["keys"]-before["keys"],
                "mushrooms_delta":after["mushrooms_spent"]-before["mushrooms_spent"],
                "hours":after["elapsed_hours"],"death":after["deaths"]>before["deaths"],
                "blessings":[e["kind"] for e in after["blessings"]],"curses":[e["kind"] for e in after["curses"]]})
            if stone_advice and stone_advice['status']=='ready':
                self.journal[-1]['stone_advice']=stone_advice
            self.observation = after
            for outcome in after.get("outcomes",[]):
                rarity = {"item":"ordinary","epics":"epic","legendaries":"legendary"}.get(outcome["kind"])
                if rarity is None: continue
                # Native ordinary-item outcomes have no amount: one item.
                count = 1 if outcome["kind"]=="item" else int(outcome["amount"])
                if count <= 0: continue
                self.loot_counts[rarity] += count
                self.kept_items.append({"step":len(self.history),"run":before["run_number"],
                                        "room":before["room"],"rarity":rarity,"count":count})
            return self.payload()

    def undo(self) -> dict:
        with self.lock:
            replay = self.history[:-1]
            self._stop(); self.history = []; self.journal = []; self._start()
            for action in replay: self.step(action)
            return self.payload()

    def export(self) -> dict:
        with self.lock:
            return {"format":"sfld-visual-v1","engine":self.observation["engine"],
                    "profile_fingerprint":self.observation["scenario"]["fingerprint"],"settings":self.config,
                    "loot":{"mode":"keep_all","counts":dict(self.loot_counts),"items":list(self.kept_items)},
                    "actions":list(self.history),"journal":list(self.journal),"last_observation":self.observation,
                    "stone_advice_records":{**self.archived_stone_advice_records,**self.stone_advice_cache}}


class Lab:
    def __init__(self, exe: Path, profile: Path):
        self.exe, self.profile = exe.resolve(strict=True), profile.resolve(strict=True)
        self.sessions: dict[str,Session] = {}
        self.lock = threading.RLock()
        self.compare_lock = threading.Lock()
        self.version = subprocess.check_output([str(self.exe),"--version"],text=True).strip()

    def scenario_profile(self, config: dict) -> Path:
        name = settings(config)["scenario"]
        return self.profile if name=="default" else REPO/"profiles/mushroom-school-2024.profile"

    def create(self, config: dict, replay: dict | None = None) -> dict:
        with self.lock:
            for key,s in list(self.sessions.items()):
                if time.monotonic()-s.touched > 7200:
                    s.close(); del self.sessions[key]
            if len(self.sessions)>=12: raise ValueError("Too many open sessions. Close an old tab's session first.")
            session = Session(self.exe,self.scenario_profile(config),config)
            try:
                if replay is not None:
                    if replay.get("format")!="sfld-visual-v1" or replay.get("engine")!=session.observation["engine"] or replay.get("profile_fingerprint")!=session.observation["scenario"]["fingerprint"]:
                        raise ValueError("Replay needs the same engine version and scenario as its recording.")
                    actions = replay.get("actions")
                    if not isinstance(actions,list) or len(actions)>10000: raise ValueError("Invalid replay action list")
                    for action in actions: session.step(action)
                    expected = replay.get("last_observation")
                    if not isinstance(expected,dict) or {k:v for k,v in expected.items() if k!="suggestions"} != {k:v for k,v in session.observation.items() if k!="suggestions"}:
                        raise ValueError("Replay did not reproduce the recorded final state.")
                    # Advice is research metadata, not trusted replay input.
                    # Replayed game state is verified above; recompute its
                    # current recommendation using the independent planner.
                    archived=replay.get('stone_advice_records',{})
                    if not isinstance(archived,dict):raise ValueError('Invalid recorded stone advice')
                    session.archived_stone_advice_records=archived
                    old_journal=replay.get('journal',[])
                    if isinstance(old_journal,list) and len(old_journal)==len(session.journal):
                        for old,new in zip(old_journal,session.journal):
                            if isinstance(old,dict) and {k:v for k,v in old.items() if k!='stone_advice'}==new and isinstance(old.get('stone_advice'),dict):
                                new['stone_advice']=old['stone_advice']
            except Exception:
                session.close(); raise
            key=secrets.token_urlsafe(24); self.sessions[key]=session
            return {"session":key,**session.payload()}

    def session(self, key: str) -> Session:
        with self.lock:
            if key not in self.sessions: raise ValueError("Session expired. Start a new event or import a saved run.")
            return self.sessions[key]

    def close(self) -> None:
        with self.lock:
            for session in self.sessions.values(): session.close()
            self.sessions.clear()

    def compare(self, config: dict, runs: int) -> dict:
        if isinstance(runs,bool) or not isinstance(runs,int) or not 1<=runs<=10000: raise ValueError("Compare 1 to 10,000 events")
        if not self.compare_lock.acquire(blocking=False): raise ValueError("A comparison is already running.")
        session = None
        try:
            session=Session(self.exe,self.scenario_profile(config),config)
            results=[]
            for strategy in (*STRATEGIES, *(('learned',) if session.config['learned_policy'] else ())):
                args=session.arguments("event", policy_options=strategy!="learned")+["--runs",str(runs),"--threads","3"]
                args += ["--policy",str(session.directory/"learned.policy")] if strategy=="learned" else ["--strategy",strategy]
                p=subprocess.run(args,text=True,encoding="utf-8",capture_output=True,timeout=120,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                if p.returncode: raise ValueError(p.stderr.strip()[-1600:])
                results.append({"strategy":strategy,"report":json.loads(p.stdout)})
            return {"results":results,"scope":"same starting setup; seed set shared across strategies"}
        finally:
            if session: session.close()
            self.compare_lock.release()


def handler_for(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        def local_host(self):
            return urlsplit("http://"+self.headers.get("Host","")).hostname in ("localhost","127.0.0.1","::1")

        def reply(self, code: int, value, content_type="application/json; charset=utf-8"):
            data = value if isinstance(value,bytes) else json.dumps(value).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if not self.local_host(): self.reply(400,{"error":"Use the loopback lab address"}); return
            path=urlsplit(self.path).path
            if path=="/api/config":
                manifest=WEB/"local-assets/manifest.json"
                assets=json.loads(manifest.read_text(encoding="utf-8"))["assets"] if manifest.exists() else {}
                self.reply(200,{"engine":lab.version,"assets":assets,"strategies":STRATEGIES}); return
            known={"/":"index.html","/index.html":"index.html","/app.js":"app.js","/style.css":"style.css",
                   "/game.css":"game.css","/play-shell.css":"play-shell.css","/presentation.js":"presentation.js",
                   "/rule-guide.html":"rule-guide.html","/rule-guide.js":"rule-guide.js",
                   "/rule-guide.css":"rule-guide.css","/rule-guide-data.json":"rule-guide-data.json"}
            if path in known: target=WEB/known[path]
            elif path.startswith("/local-assets/"):
                base=(WEB/"local-assets").resolve(); target=(WEB/path.lstrip("/")).resolve()
                if not target.is_relative_to(base) or target.suffix.lower() not in (".png",".webp",".jpg",".ttf",".otf",".woff",".woff2"):
                    self.reply(404,{"error":"Not found"}); return
            else: self.reply(404,{"error":"Not found"}); return
            if not target.is_file(): self.reply(404,{"error":"Not found"}); return
            types={".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".json":"application/json; charset=utf-8",".png":"image/png",".jpg":"image/jpeg",".webp":"image/webp",".ttf":"font/ttf",".otf":"font/otf",".woff":"font/woff",".woff2":"font/woff2"}
            self.reply(200,target.read_bytes(),types[target.suffix.lower()])

        def do_POST(self):
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=MAX_BODY: raise ValueError("Request is empty or too large")
                body=self.rfile.read(length) # Drain bounded requests before rejecting, avoiding a TCP reset on Windows.
                if not self.local_host(): raise ValueError("Use the loopback lab address")
                # Browser requests must originate from this loopback page.
                origin=self.headers.get("Origin")
                if origin and origin!=f"http://{self.headers.get('Host')}": raise ValueError("Cross-origin request rejected")
                if self.headers.get("X-SFLD-Lab")!="1": raise ValueError("Missing local lab request header")
                data=json.loads(body)
                if not isinstance(data,dict): raise ValueError("Expected an object")
                route=urlsplit(self.path).path
                if route=="/api/new": result=lab.create(data.get("settings",{}))
                elif route=="/api/import": result=lab.create(data.get("recording",{}).get("settings",{}),data.get("recording",{}))
                elif route=="/api/compare": result=lab.compare(data.get("settings",{}),data.get("runs",1000))
                else:
                    session=lab.session(data.get("session",""))
                    if route=="/api/action": result=session.step(data.get("action",{}))
                    elif route=="/api/stone-advice": result=session.final_stone_advice(data.get("steps"),data.get("state_key"),data.get("retry",False))
                    elif route=="/api/undo": result=session.undo()
                    elif route=="/api/export": result=session.export()
                    elif route=="/api/close":
                        session.close()
                        with lab.lock: lab.sessions.pop(data["session"],None)
                        result={"closed":True}
                    else: self.reply(404,{"error":"Not found"}); return
                self.reply(200,result)
            except (ValueError,KeyError,TypeError,AttributeError,OSError,subprocess.SubprocessError) as error:
                self.reply(400,{"error":str(error)})

        def log_message(self, *args): pass
    return Handler


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",type=Path,default=default_engine())
    parser.add_argument("--profile",type=Path,default=REPO/"profiles/synthetic.profile")
    parser.add_argument("--port",type=int,default=8787)
    args=parser.parse_args()
    # Load numerical DLLs before the request/pipe threads start on Windows.
    try:
        from sfld_rl.visual_policy import warmup
        warmup()
    except ImportError:
        pass
    lab=Lab(args.exe,args.profile); atexit.register(lab.close)
    server=ThreadingHTTPServer(("127.0.0.1",args.port),handler_for(lab))
    print(f"Legendary Dungeon lab: http://127.0.0.1:{server.server_port}",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); lab.close()


if __name__=="__main__": main()
