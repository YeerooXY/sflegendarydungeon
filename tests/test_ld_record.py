import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from sfld_record.spool import Spool, extract  # noqa: E402
from sfld_record.measure import ingest, measure  # noqa: E402
from sfld_study.protocol import GameSession  # noqa: E402


def save(status=1, stage=0, room=10, doors=(1001, 1), traps=(0, 0), keys=2, hp=100, steps=None):
    words = [0] * 50
    words[2], words[4], words[15], words[16], words[17] = hp, 100, status, stage, room
    words[19], words[20] = doors
    words[25], words[26] = traps
    words[39] = keys
    for i, (typ, variant) in enumerate(steps or []):
        words[19 + i], words[22 + i] = typ, variant
    return '/'.join(map(str, words))


class ExtractParity(unittest.TestCase):
    CASES = [
        ('IADungeonInteract', '40', f'iadungeonsave:{save()}&sessionid:123&newfield:1/2&name:text&resources:1/99'),
        ('Poll', '', f'iadungeontime:8/1/2/3&timestamp:1790000000&ownplayersavecharacter:1/2&resources:1/2'),
        ('Poll', '', 'ownplayersavecharacter:1/2&timestamp:5'),
        ('IADungeonStart', '8/0', 'iadungeonsave(3):5/6&cryptoid:9'),
    ]

    @unittest.skipUnless(shutil.which('node'), 'node is required for the desktop parity check')
    def test_python_and_desktop_extract_identically(self):
        script = ("const {fromNetwork}=require(process.argv[1]);const cases=JSON.parse(process.argv[2]);"
                  "console.log(JSON.stringify(cases.map(([req,params,body])=>{const r=fromNetwork({url:"
                  "'https://s1.sfgame.net/cmd.php?req='+req+'&params='+Buffer.from(params).toString('base64'),"
                  "headers:{'PG-Player':'5'},body,client:'c'});return r&&{fields:r.fields,params:r.params,player:r.player}})))")
        out = subprocess.run(['node', '-e', script, str(ROOT / 'desktop' / 'ld-recorder.cjs'), json.dumps(self.CASES)],
                             capture_output=True, text=True, check=True).stdout
        desktop = json.loads(out)
        for (req, params, body), js in zip(self.CASES, desktop):
            values = dict(f.split(':', 1) for f in body.split('&'))
            py = extract(req, params, values, source='controller', host='s1.sfgame.net', player=5, client='c')
            self.assertEqual(js, py and {'fields': py['fields'], 'params': py['params'], 'player': py['player']}, req)


class FakeResponse:
    status = 200
    def __init__(self, body): self.body = body
    def read(self, _): return self.body
    def __enter__(self): return self
    def __exit__(self, *_): return False


class ControllerHook(unittest.TestCase):
    def test_game_session_records_numeric_dungeon_fields_only(self):
        with tempfile.TemporaryDirectory() as folder:
            session = GameSession('s1.sfgame.net', recorder=Spool(folder))
            body = f'sessionid:abcdef&iadungeonsave:{save()}&ownplayersavecharacter:1/77/3/50'.encode()
            session.opener = type('O', (), {'open': lambda self, request, timeout: FakeResponse(body)})()
            session.session_id = 'abcdef'
            session._send('IADungeonInteract', '1')
            lines = [json.loads(l) for p in Path(folder).glob('*.jsonl') for l in p.read_text().splitlines()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]['params'], '1')
            self.assertNotIn('abcdef', json.dumps(lines[0]))
            self.assertEqual(session.player_id, 77)


class EndToEnd(unittest.TestCase):
    def test_transitions_rates_and_unknown_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            spool = Spool(folder)
            def send(req, params, s, **extra):
                spool.packet(req, params, {'iadungeonsave': s, **extra}, host='s1.sfgame.net', player=1)
            # Door offer (mystery + monster), enter the mystery, find a barrel, open it for a key.
            send('Poll', '', save(status=1, doors=(1001, 1), traps=(1, 0)))
            send('IADungeonInteract', '1', save(status=10, steps=[(100, 400)], hp=90))
            send('IADungeonInteract', '40', save(status=1, room=11, doors=(1002, 1001), keys=3, hp=90))
            # Enter a monster door and fight it, losing 20% HP and gaining a key.
            send('IADungeonInteract', '2', save(status=10, room=11, steps=[(1, 0)], keys=3, hp=90))
            send('IADungeonInteract', '20', save(status=1, room=12, doors=(1006, 1001), keys=4, hp=70))
            # A golden door reveals a room code the decoder has never seen.
            send('IADungeonInteract', '1', save(status=10, room=12, steps=[(777, 3)], keys=4, hp=70))
            database = Path(folder) / 'records.sqlite'
            self.assertEqual(ingest(folder, database)['added'], 6)
            self.assertEqual(ingest(folder, database)['added'], 0)  # re-ingest adds nothing
            result = measure(database)
            q = {x['quantity']: x for x in result['quantities']}
            self.assertEqual(q['contents mystery']['outcomes']['barrel']['count'], 1)
            self.assertEqual(q['container barrel']['outcomes']['key']['count'], 1)
            self.assertEqual(q['key_after_fight stage 1']['outcomes']['key']['count'], 1)
            self.assertAlmostEqual(q['fight_damage stage 1']['mean'], .2)
            self.assertAlmostEqual(q['trap_damage stage 1']['mean'], .1)
            self.assertEqual(q['traps stage 1']['outcomes']['trapped']['count'], 1)
            self.assertIn('encounter 777', result['unknown_codes'])
            self.assertEqual(result['characters'], 1)


if __name__ == '__main__':
    unittest.main()
