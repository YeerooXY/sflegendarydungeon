"""Reproduce the eight-active/four-offered opening shop policy challenge."""
import json
from pathlib import Path
from collections import Counter
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.visual_policy import VisualPolicy, CHECKPOINT_SHA

ROOT = Path(__file__).resolve().parents[1]


def run():
    profile = ROOT/'profiles/synthetic.profile'
    policy = VisualPolicy(); rows = []
    for budget in [0, 20, 50, 100, 500, 1000]:
        with VectorEnv(ROOT/'build-v0610/Release/sfld_rl.dll', [profile], 1, budgets=[budget], auto_reset=False) as env:
            env.reset(); lab = EffectLab(env)
            for hp in range(20, 101, 10):
                progress = (f'schema=1\nroom=5\nrun_number=1\nphase=shop\nhp_percent={hp}\n'
                            'keys=0\nblessings=one_hit:strong:8\noffers=unknown\n')
                with lab.fixture(progress, budget) as snap: lab.restore(0, snap, 79)
                actions = []
                for _ in range(4):
                    visible = lab.visible()
                    if visible['phase'] != 'shop': break
                    policy.advise(visible, {}, profile)
                    action = visible['suggestions'][-1]['action']; actions.append(action)
                    if action['kind'] == 'buy' and action['index'] == 0:
                        raise AssertionError('Policy downgraded eight-room One Hit Wonder')
                    name = 'buy_'+str(action['index']) if action['kind'] == 'buy' else action['kind']
                    env.step([env.action_names.index(name)])
                final = lab.visible()
                one_hit = next(e for e in final['blessings'] if e['kind'] == 'one_hit')
                assert final['room'] == 6 and one_hit['remaining'] == 7
                rows.append({'budget': budget, 'hp': hp, 'actions': actions, 'remaining': one_hit['remaining']})
    out = ROOT/'out/opening-one-hit-guard-2026-10-12.json'
    out.write_text(json.dumps({'checkpoint_sha256': CHECKPOINT_SHA, 'rows': rows}, indent=2))
    print({'passed_positions': len(rows), 'final_shop_choices': dict(Counter(str(r['actions'][-1]) for r in rows))})


if __name__ == '__main__': run()
