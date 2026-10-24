"""Exercise browser-session state, replay and HTTP boundaries against the real engine."""
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"tools"))
from visual_lab import Lab, Session, handler_for, settings

EXE=Path(os.environ.get("SFLD_EXE",REPO/("build/Release/sfld.exe" if os.name=="nt" else "build/sfld")))
PROFILE=REPO/"profiles/synthetic.profile"
OPENING="schema=1\nroom=5\nrun_number=1\nphase=doors\nhp_percent=70\nkeys=0\ndoors=shop,wall\n"
SHOP="schema=1\nroom=82\nrun_number=2\nphase=shop\nhp_percent=55\nkeys=8\ngems=rabbit,gambler,greed\noffers=one_hit:strong:8:4,escape:strong:10:6\nshops_seen=1,2,3,4\n"

from engine_build import MISSING  # noqa: E402

@unittest.skipUnless(EXE.is_file(), MISSING)
class VisualLabTests(unittest.TestCase):
    def setUp(self): self.lab=Lab(EXE,PROFILE)
    def tearDown(self): self.lab.close()

    def test_wallet_is_separate_from_budget_and_survives_replay(self):
        self.assertIsNone(settings({'budget':100})['wallet_mushrooms'])
        for wallet in (-1,99,True,100.5,1000000001):
            with self.subTest(wallet=wallet), self.assertRaises(ValueError):
                settings({'budget':100,'wallet_mushrooms':wallet})
        result=self.lab.create({'initial_state':SHOP,'run_number':2,'budget':100,'wallet_mushrooms':600})
        session=self.lab.session(result['session']);session.step({'kind':'reroll'})
        record=session.export();self.assertEqual(record['settings']['wallet_mushrooms'],600)
        self.assertEqual(record['settings']['budget'],100)
        replay=self.lab.create(record['settings'],record)
        self.assertEqual(replay['observation']['mushrooms_spent'],1)
        self.assertEqual(replay['observation']['mushrooms_left'],99)

    def test_opening_purchase_undo_and_exact_replay(self):
        result=self.lab.create({"initial_state":OPENING,"budget":500})
        session=self.lab.session(result["session"])
        with self.assertRaises(ValueError): session.step({"kind":"choose_door","index":1})
        session.step({"kind":"choose_door","index":0})
        shop=session.observation
        self.assertEqual([x["effect"]["remaining"] for x in shop["offers"]],[4,5])
        self.assertFalse(any(a["kind"]=="reroll" for a in shop["legal_actions"]))
        session.step({"kind":"buy","index":0})
        after=session.observation
        self.assertEqual(after["room"],6)
        self.assertEqual(after["blessings"][0]["remaining"],4)
        session.undo(); self.assertEqual(session.observation,shop)
        session.step({"kind":"buy","index":0}); self.assertEqual(session.observation,after)
        recording=session.export()
        replay=self.lab.create(recording["settings"],recording)
        self.assertEqual(replay["observation"],after)
        self.assertEqual(replay["journal"],session.journal)
        recording["last_observation"]={}
        with self.assertRaisesRegex(ValueError,"final state"): self.lab.create(recording["settings"],recording)
        with self.assertRaises(ValueError): self.lab.create({}, {})

    def test_paid_shop_rerolls_refills_and_single_purchase(self):
        result=self.lab.create({"initial_state":SHOP,"run_number":2,"budget":1})
        session=self.lab.session(result["session"])
        session.step({"kind":"reroll"})
        self.assertEqual(session.observation["mushrooms_spent"],1)
        self.assertNotEqual(*[x["effect"]["kind"] for x in session.observation["offers"]])
        self.assertEqual(session.observation["keys"],8)
        before=session.payload()
        for kind in ("heal_step","heal_full"):
            with self.assertRaises(ValueError): session.step({"kind":kind})
        self.assertEqual(session.payload(),before)
        self.assertAlmostEqual(session.observation["hp"],.55)
        self.assertEqual(session.observation["phase"],"shop")
        self.assertEqual(session.observation["mushrooms_spent"],1)
        with self.assertRaises(ValueError): session.step({"kind":"reroll"})
        session.step({"kind":"buy","index":0})
        self.assertEqual(session.observation["room"],83)
        self.assertEqual(session.observation["phase"],"doors")

    def test_advisers_are_legal_and_replay_does_not_peek_or_change_rng(self):
        result=self.lab.create({"seed":12,"budget":0})
        session=self.lab.session(result["session"])
        for _ in range(180):
            if session.observation["terminal"]: break
            advice=session.observation["suggestions"]
            self.assertTrue({"baseline","strategic","key-stockpile"}<={x["strategy"] for x in advice})
            session.step(advice[1]["action"])
        self.assertEqual(session.observation["mushrooms_spent"],0)
        record=session.export(); replay=self.lab.create(record["settings"],record)
        self.assertEqual(replay["observation"],session.observation)
        incompatible=json.loads(json.dumps(record)); incompatible["engine"]="0.0.0"
        with self.assertRaisesRegex(ValueError,"same engine"): self.lab.create(incompatible["settings"],incompatible)

    def test_comparison_uses_shared_setup_and_hard_budgets(self):
        for budget in (0,10):
            report=self.lab.compare({"seed":8,"budget":budget,"deadline_hours":24},2)
            self.assertEqual(len(report["results"]),3)
            for row in report["results"]:
                self.assertEqual(row["report"]["master_seed"],8)
                self.assertEqual(row["report"]["events"],2)
                self.assertTrue(row["report"]["valid"])
                self.assertLessEqual(row["report"]["mean_mushrooms"],budget)

    def test_shop_allowance_advice_replay_key_gate_and_comparison_budget(self):
        state="schema=1\nroom=35\nrun_number=2\nphase=shop\nhp_percent=100\nkeys=4\ngems=rabbit\noffers=raider:weak:5:1,elixir:weak:1:1\n"
        configuration={"initial_state":state,"run_number":2,"seed":8,"budget":7,"deadline_hours":24,
                       "survival_aware":True,"adaptive_gems":True,"early_key_target":8,
                       "shop_allowance":5,"shop_key_reserve":4,"shop_mushroom_reserve":3}
        result=self.lab.create(configuration); session=self.lab.session(result["session"])
        advice=next(s["action"] for s in session.observation["suggestions"] if s["strategy"]=="key-stockpile")
        self.assertEqual(advice["kind"],"reroll")
        self.assertEqual(session.observation["reroll_shop_visits"],1)
        session.step(advice)
        self.assertEqual(session.observation["mushrooms_spent"],1)
        self.assertEqual(session.observation["reroll_bank_used"],1)
        self.assertEqual(session.observation["keys"],4)
        record=session.export(); replay=self.lab.create(record["settings"],record)
        self.assertEqual(replay["observation"],session.observation)
        for key in ("survival_aware","adaptive_gems","early_key_target","shop_allowance","shop_key_reserve","shop_mushroom_reserve"):
            self.assertEqual(replay["settings"][key],configuration[key])
        poor=self.lab.create({**configuration,"initial_state":state.replace("keys=4","keys=3")})
        self.assertNotEqual(next(s["action"] for s in poor["observation"]["suggestions"] if s["strategy"]=="key-stockpile")["kind"],"reroll")
        for result in self.lab.compare(configuration,2)["results"]:
            report=result["report"]
            self.assertTrue(report["valid"])
            self.assertLessEqual(report["mean_mushrooms"],7)
            self.assertIn("shop-allowance=5",report["policy"])
            self.assertIn("shop-keys=4",report["policy"])

    def test_optional_strategy_setting_validation(self):
        default=settings({})
        self.assertEqual(default["revive_hp"], .2)
        for invalid in (True, False, 0, .199, 1.01, "0.21", None, float("nan"), float("inf")):
            with self.subTest(revive_hp=invalid), self.assertRaisesRegex(ValueError,"revive_hp"):
                settings({"revive_hp":invalid})
        self.assertFalse(default["survival_aware"])
        self.assertFalse(default["adaptive_gems"])
        for key, high in (("early_key_target",100),("shop_allowance",1000),("shop_key_reserve",100),("shop_mushroom_reserve",1000000)):
            self.assertEqual(default[key],0)
            for invalid in (True,False,-1,high+1,.5,"5"):
                with self.subTest(key=key,value=invalid), self.assertRaisesRegex(ValueError,key): settings({key:invalid})
        for key in ("survival_aware","adaptive_gems"):
            for invalid in (0,1,"true",None):
                with self.subTest(key=key,value=invalid), self.assertRaisesRegex(ValueError,key): settings({key:invalid})

    def test_recovery_target_reaches_native_adviser_and_survives_replay(self):
        state="schema=1\nroom=35\nrun_number=2\nphase=recovery\nresume_phase=doors\nhp_percent=0\nkeys=3\ngems=hero\n"
        result=self.lab.create({"initial_state":state,"run_number":2,"budget":0,"revive_hp":.21})
        session=self.lab.session(result["session"])
        advice=next(s["action"] for s in session.observation["suggestions"] if s["strategy"]=="key-stockpile")
        self.assertEqual(advice["kind"],"wait")
        self.assertAlmostEqual(advice["value"], .21 * session.observation["scenario"]["recovery_hours"])
        session.step(advice)
        self.assertAlmostEqual(session.observation["hp"], .21)
        replay=self.lab.create(session.config,session.export())
        self.assertEqual(replay["settings"]["revive_hp"], .21)
        self.assertEqual(replay["observation"],session.observation)

    def test_video_scenario_is_used_for_play_replay_and_comparison(self):
        configuration={"scenario":"mushroom-school","seed":42,"deadline_hours":24}
        result=self.lab.create(configuration); session=self.lab.session(result["session"])
        self.assertEqual(session.observation["scenario"]["id"],"vod-working-mushroom-school-2024-v6")
        session.step(session.observation["suggestions"][1]["action"])
        recording=session.export(); replay=self.lab.create(recording["settings"],recording)
        self.assertEqual(replay["observation"],session.observation)
        for row in self.lab.compare(configuration,1)["results"]:
            self.assertEqual(row["report"]["profile_fingerprint"],session.observation["scenario"]["fingerprint"])
            self.assertEqual(row["report"]["damage_distribution"],"triangular_mean")

    def test_http_rejects_external_origin_and_non_loopback_host(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(self.lab))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            def request(method,path,headers=None,data=None):
                conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=15)
                conn.request(method,path,json.dumps(data) if data is not None else None,headers or {})
                response=conn.getresponse(); status=response.status; body=response.read(); conn.close()
                return status,body
            self.assertEqual(request("GET","/api/config")[0],200)
            self.assertEqual(request("GET","/api/config",{"Host":"elsewhere.example"})[0],400)
            self.assertEqual(request("GET","/local-assets/../../README.md")[0],404)
            self.assertEqual(request("POST","/api/new",data={})[0],400)
            self.assertEqual(request("POST","/api/new",{"X-SFLD-Lab":"1","Origin":"http://elsewhere.example"},{})[0],400)
            status,body=request("POST","/api/new",{"X-SFLD-Lab":"1"},{"settings":{"seed":1}})
            self.assertEqual(status,200); self.assertIn("suggestions",json.loads(body)["observation"])
            self.assertEqual(request("POST","/api/import",{"X-SFLD-Lab":"1"},{})[0],400)
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_outcomes_keep_instant_rewards_and_historical_prices(self):
        recovery="schema=1\nroom=30\nrun_number=2\nphase=recovery\nresume_phase=doors\nhp_percent=20\nkeys=8\ngems=rabbit\n"
        result=self.lab.create({"initial_state":recovery,"budget":100,"run_number":2})
        session=self.lab.session(result["session"])
        session.step({"kind":"heal_step"})
        self.assertEqual(session.journal[0]["price"],10)
        self.assertEqual(session.observation["heal_step_price"],15)
        record=session.export(); replay=self.lab.create(record["settings"],record)
        self.assertEqual(replay["journal"],session.journal)
        state="schema=1\nroom=30\nrun_number=2\nphase=shop\nhp_percent=20\nkeys=8\ngems=rabbit\noffers=elixir:weak:1:1,one_hit:weak:4:2\n"
        result=self.lab.create({"initial_state":state,"budget":100,"run_number":2})
        session=self.lab.session(result["session"])
        session.step({"kind":"buy","index":0})
        journal=session.journal
        self.assertEqual(journal[0]["offer"]["effect"]["kind"],"elixir")
        self.assertTrue(any(e["kind"]=="blessing" and e["detail"]=="elixir" for e in journal[0]["outcomes"]))
        self.assertTrue(any(e["kind"]=="healing" for e in journal[0]["outcomes"]))
        self.assertFalse(any(e["kind"]=="elixir" for e in session.observation["blessings"]))
        record=session.export(); replay=self.lab.create(record["settings"],record)
        self.assertEqual(replay["journal"],journal)

    def test_completed_run_totals_survive_restart_and_undo(self):
        state="schema=1\nroom=100\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=boss\nhp_percent=80\nkeys=0\ngems=rabbit,gambler,greed\n"
        result=self.lab.create({"initial_state":state,"budget":100,"run_number":2})
        session=self.lab.session(result["session"])
        session.step({"kind":"heal_step"}); session.step({"kind":"reenter"}); session.step({"kind":"fight"})
        finished=session.payload(); self.assertEqual(len(finished["completed_runs"]),1)
        total=finished["completed_runs"][0]; self.assertEqual(total["mushrooms"],10)
        self.assertGreater(total["hours"],0)
        session.step({"kind":"restart"})
        self.assertEqual(session.observation["run_mushrooms_spent"],0)
        self.assertAlmostEqual(session.observation["run_elapsed_hours"],0)
        self.assertEqual(session.observation["mushrooms_spent"],10)
        self.assertEqual(session.payload()["completed_runs"],finished["completed_runs"])
        session.undo(); self.assertEqual(session.payload(),finished)

    def test_trap_death_reenters_next_choices_without_encounter_or_reward(self):
        state="schema=1\nroom=30\nrun_number=2\nphase=doors\nhp_percent=5\nkeys=1\ngems=rabbit\ndoors=monster:trap,mystery\n"
        result=self.lab.create({"initial_state":state,"budget":100,"run_number":2})
        session=self.lab.session(result["session"])
        session.step({"kind":"choose_door","index":0})
        self.assertEqual(session.observation["phase"],"recovery")
        self.assertEqual(session.observation["room"],31)
        self.assertIsNone(session.journal[-1]["revealed"])
        self.assertTrue(any(e["kind"]=="trap" for e in session.journal[-1]["outcomes"]))
        session.step({"kind":"heal_step"}); session.step({"kind":"reenter"})
        self.assertEqual(session.observation["phase"],"doors")
        self.assertEqual(session.observation["room"],31)
        self.assertEqual(session.observation["heal_step_price"],15)

    def test_daily_sleep_survives_undo_and_replay(self):
        initial="schema=1\nroom=10\nrun_number=1\nphase=recovery\nresume_phase=doors\nhp_percent=0\nkeys=0\ndoors=monster,mystery\n"
        result=self.lab.create({"initial_state":initial,"event_start_hour":17,"sleep_until_hour":8,"neural_adviser":False})
        session=self.lab.session(result['session']);before=session.observation
        session.step(next(a for a in before['legal_actions'] if a['kind']=='wait' and abs(a['value']-9.6)<1e-9))
        after=session.observation
        self.assertEqual(after['local_hour'],8)
        self.assertEqual(after['elapsed_hours'],15)
        self.assertAlmostEqual(after['hp'],.625)
        self.assertEqual(after['phase'],'recovery')
        self.assertTrue(any(e['kind']=='sleep' for e in after['outcomes']))
        recording=session.export()
        replay=self.lab.create(recording['settings'],recording)
        self.assertEqual(replay['observation'],after)
        session.undo();self.assertEqual(session.observation,before)
        for key in ('sleep_until_hour','event_start_hour'):
            for value in (-1,24,float('nan'),True,'8'):
                with self.assertRaises(ValueError):settings({key:value})

if __name__=="__main__": unittest.main()
