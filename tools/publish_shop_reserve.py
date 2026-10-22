"""Validate and archive the frozen 0.6.10 shop-reserve comparison."""
import gzip
import hashlib
import json
import shutil
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'out/shop-reserve-v3-2026-10-12'
TARGET = ROOT/'docs/shop-reserve-2026-10-12'


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def run():
    plan = read(SOURCE/'plan.json'); results = read(SOURCE/'results.json')
    selection = read(SOURCE/'frozen-selection.json')
    assert plan['schema'] == 'sfld-shop-reserve-2026-10-12-v3'
    assert plan['final']['events'] == 4096 and plan['selection']['events'] == 512
    files = {'checkpoint': SOURCE/'checkpoint.pt', 'library': SOURCE/'sfld_rl.dll',
             'profile': SOURCE/'synthetic.profile'}
    for key, name in {'runner':'tools/benchmark_shop_reserve.py','rules':'tools/sfld_rl/shop_reserve.py',
                      'practical':'tools/sfld_rl/practical.py','escape':'tools/sfld_rl/escape_loot.py',
                      'env':'tools/sfld_rl/env.py','ppo':'tools/sfld_rl/ppo.py','train':'tools/train_rl.py'}.items():
        files[key] = SOURCE/'source'/name
    assert all(sha(path) == plan['hashes'][key] for key,path in files.items())
    if TARGET.exists(): raise FileExistsError('Do not overwrite an archived study')
    cells = {}
    for cohort, cases in [('selection',plan['cases']), ('final',selection['finalists'])]:
        for case in cases:
            for budget in plan['budgets']:
                name = f'{cohort}-{case["name"]}-{budget}'
                c = read(SOURCE/(name+'.json')); rows=c['rows']
                assert c['identity'] == {'cohort':plan[cohort], 'case':case, 'budget':budget, 'hashes':plan['hashes']}
                assert [r['event'] for r in rows] == list(range(plan[cohort]['events']))
                assert all(not r['invalid_actions'] and 0 <= r['mushrooms'] <= budget
                           and r['mushrooms'] == r['recovery_mushrooms']+r['reroll_mushrooms']
                           and 167.999 <= r['elapsed_hours'] <= 168.000001 for r in rows)
                assert c['means'] == {k:statistics.mean(r[k] for r in rows) for k in rows[0] if k!='event'}
                cells[cohort,case['name'],budget] = c
    scores = {c['name']:statistics.mean(cells['selection',c['name'],b]['means']['completions'] for b in plan['budgets']) for c in plan['cases']}
    assert scores == selection['scores']
    winners = [max((c for c in plan['cases'] if c['reserve'] and c['cheap_elixir']==e), key=lambda c:scores[c['name']]) for e in (False,True)]
    assert plan['cases'][:2]+winners == selection['finalists']
    z=statistics.NormalDist().inv_cdf(1-.01/(2*21))
    for c in selection['finalists'][1:]:
        deltas=[[r['completions']-p['completions'] for r,p in zip(cells['final',c['name'],b]['rows'],cells['final','control',b]['rows'])] for b in plan['budgets']]
        for name, values in list(zip(map(str,plan['budgets']),deltas))+[('pooled',[statistics.mean(v) for v in zip(*deltas)])]:
            mean=statistics.mean(values); half=z*statistics.stdev(values)/(len(values)**.5)
            recorded=results['comparisons'][c['name']]['estimates'][name]
            assert abs(mean-recorded['gain'])<1e-12 and all(abs(x-y)<1e-12 for x,y in zip([mean-half,mean+half],recorded['family_99_ci']))
    TARGET.mkdir(parents=True); (TARGET/'events').mkdir()
    hashes={}
    for (cohort,case,budget),cell in cells.items():
        name=f'events/{cohort}-{case}-{budget}.json.gz';dest=TARGET/name
        dest.write_bytes(gzip.compress(json.dumps(cell,separators=(',',':')).encode(),mtime=0));hashes[name]=sha(dest)
    for name in ('plan.json','results.json','frozen-selection.json','seed-audit.json','transfer.json','synthetic.profile','sfld_rl.dll'):
        shutil.copyfile(SOURCE/name,TARGET/name);hashes[name]=sha(TARGET/name)
    shutil.copytree(SOURCE/'source',TARGET/'source')
    (TARGET/'archive.json').write_text(json.dumps({'schema':1,'files':hashes,'verified_events':sum(len(c['rows']) for c in cells.values()),
        'checkpoint':'docs/multiseed-2026-10-12/raw-1/checkpoint-000025165824.pt','checkpoint_sha256':plan['hashes']['checkpoint'],
        'selected_overlay':None,'reason':'No challenger passed the declared simultaneous confidence gate.'},indent=2)+'\n')
    labels={'control':'Learned control + OHW guard','control_elixir':'Prefer 1-key Traveler potion',
            'reserve3_carry1':'Reserve 3, carry 1','reserve4_carry1_elixir':'Reserve 4, carry 1 + potion rule'}
    lines=['# Shop reserves and Time Traveler potions, 2026-10-12','',
        'No tested challenger passes the predeclared promotion gate. The viewer keeps the learned control with its One Hit Wonder downgrade guard. These are engine 0.6.10 simulator results, not live-game completion estimates or proof of global optimality.','',
        '## Independent final evaluation','',
        'Each cell contains 4,096 fresh 168-hour events with random eligible stone offers. The four finalists total 98,304 event rollouts. Screening used a separate 55,296 rollouts across 18 cases; the final cohort did not select the reserve sizes.','',
        '| Weekly mushroom cap | Learned control | Traveler potion rule | Reserve 3 / carry 1 | Reserve 4 / carry 1 + potion |','|---:|---:|---:|---:|---:|']
    for b in plan['budgets']:
        lines.append('| '+str(b)+' | '+' | '.join(f'{results["final_means"][c["name"]][str(b)]["completions"]:.4f}' for c in selection['finalists'])+' |')
    lines+=['','Caps are maximum gross spending, not required purchases. At cap 1,000, the control spends 990.65 mushrooms on average. Event deaths do not reset paid-heal escalation; completion does.','',
        '## Paired differences from control','',
        'These equal-budget averages use simultaneous 99% normal intervals, Bonferroni-adjusted across 21 declared comparisons (three challengers, six budgets plus the pooled contrast). The pooled calculation clusters the six budget outcomes by event index. Starting seeds match; trajectories diverge after different actions.','',
        '| Candidate | Clears/week difference | Simultaneous 99% interval | Promotion |','|---|---:|---:|---|']
    for name,c in results['comparisons'].items():
        v=c['estimates']['pooled'];lo,hi=v['family_99_ci']
        lines.append(f'| {labels[name]} | {v["gain"]:+.4f} | {lo:+.4f} to {hi:+.4f} | No |')
    lines+=['','The reserve-three intervention loses detectably at five of six budgets; its cap-100 interval crosses zero. The reserve-four/potion combination loses at all six. The potion-only differences are inconclusive. Neither result rules out a better state-dependent conservation rule.','',
        'At cap 100, average keys on entering a paid shop rise from 3.73 to 4.11 with reserve three, but completions fall from 3.4819 to 3.4607. More affordable shops alone are not the objective. The potion-only rule eliminates all 3-key Traveler elixir purchases and increases 1-key purchases; a completion benefit is not established.','',
        '## Exact tested interventions','',
        '- Reserve 1/2/3/4, each with carry 1/2, with and without the potion rule, plus control and potion-only: 18 cases.',
        '- Before an unseen shop, ramp from one key to the target over the four rooms before the earliest public shop window, then hold. After a shop, retain one or two keys toward the next stage; spend freely after the final shop. Later-run stage one carries keys toward stage two. The free opening shop is exempt.',
        '- This is a hard reserve: replace a chosen key door with a legal zero-key alternative even when it is risky. This intentional limitation can explain losses; a survival-aware soft penalty is a different, untested candidate.',
        '- With Time Traveler and at least 20% HP missing, buy an available one-key potion. If the actor selects a three-key potion, reroll if legal, otherwise skip. Future offers are not known and every reroll is paid.',
        '- All cases retain the existing guaranteed-effective-escape soldier-search rule and the new shop OHW downgrade guard. All other actions come from the same frozen actor; no new PPO training occurred in this study.','',
        '## Model changes and uncertainty','',
        'The actor was explicitly transferred from 0.6.9 to corrected opening special doors, no later-run stage-one shop, special Pendant door styles, and a nonzero Time Traveler barrel blessing chance. The observation/action schema is unchanged. New opening weights and the relative barrel modifier are provisional. Old score tables cannot isolate the effect of any one correction.','',
        'A policy can be best among these tested candidates under this profile without being optimal in the real game. Next evidence should target wheel outcomes, opening frequencies, refill escalation and isolated stone modifiers. Then evaluate fresh cohorts across plausible parameter scenarios and finally compare prospectively recorded game runs. An exact optimality certificate would require a trustworthy transition model and a solved state-space or a valid upper bound.','',
        '## Reproducibility','',
        '`docs/shop-reserve-2026-10-12/` contains the frozen profile/library, controller sources, selection manifest, transfer record, hashes, summary and all 153,600 event rows in compressed JSON files. Every completed event was checked for elapsed time, zero invalid actions, budget compliance and spending reconciliation. The canonical checkpoint is referenced by hash rather than copied again.',
        'Runner: `tools/benchmark_shop_reserve.py`; validating publisher: `tools/publish_shop_reserve.py`. Do not overwrite the frozen archive or reuse its final seeds for selecting another candidate.']
    (ROOT/'docs/SHOP_RESERVE_2026_10_12.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({'archived_events':sum(len(c['rows']) for c in cells.values()),'promotion':False,'control':{b:v['completions'] for b,v in results['final_means']['control'].items()}}))


if __name__=='__main__':run()
