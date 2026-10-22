"""Frozen, paired weekly test of the first-five-room two-fight proposal."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from statistics import NormalDist
import time
import numpy as np
import torch
import train_rl
from train_continuation_study import audit_seeds
from train_current_study import copy
from benchmark_player_ideas import write_once, transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.opening_fights import OpeningFightPolicy, VARIANTS
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA
from visual_lab import default_engine

ROOT = Path(__file__).resolve().parents[1]


def interval(values, z=1.96):
    a = np.asarray(values, dtype=float)
    se = float(a.std(ddof=1)/np.sqrt(len(a))) if len(a) > 1 else 0.
    avg = float(a.mean())
    return dict(mean=avg, se=se, interval=[avg-z*se, avg+z*se], n=len(a))


def evaluate(out, plan, model, saved, cohort, variant, budget):
    setup = plan[cohort]
    n = setup['events']
    rows, milestones = {}, defaultdict(list)
    started = time.monotonic()
    with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], min(512,n), budgets=[budget],
                   seed=setup['seed'], seed_namespace=setup['namespace'], auto_reset=False,
                   event_start_hour=17, sleep_until_hour=0) as env:
        transfer_changes(saved['metadata']['environment_signature'], env.metadata, True)
        rule = OpeningFightPolicy(env, profile_values(out/'synthetic.profile'), variant)
        rule.count_before_episode = n
        obs, masks = env.reset()
        opening_doors = defaultdict(int)
        with torch.inference_mode():
            while len(rows) < n:
                proposed = train_rl.policy_actions(model, obs, masks, plan['device'], greedy=True)[0].cpu().numpy()
                actions, targets, _ = rule.apply(obs, masks, proposed)
                if np.any(targets >= 0): raise RuntimeError('Unexpected wait override')
                valid = env.episode_indices < n
                room = np.rint(rule.v(obs,'room_div100')*100)
                first = rule.flag(obs,'first_event_run').copy()
                before_clears = np.rint(rule.v(obs,'completed_runs_div10')*10)
                fights = rule.fights.copy()
                for side in range(2):
                    chosen = valid & (room<=5) & rule.flag(obs,'phase.doors') & (actions==rule.ai[f'door_{side}'])
                    for kind in ('monster','mystery','destiny','blessing','wood','stone','souls','metal','arcane','hourglasses','shop'):
                        for prefix,run_filter in [('first',first),('later',~first)]:
                            opening_doors[prefix+'.'+kind] += int((chosen & run_filter & rule.flag(obs,f'doors.{side}.kind.{kind}')).sum())
                obs,masks,_,done,trunc,info = env.step(actions)
                crossed = valid & (room<=5) & (info['room']==6)
                boss = valid & (room==25) & (info['room']==26)
                for slot in np.flatnonzero(crossed | boss):
                    key = ('first' if first[slot] else 'later') + ('.room6' if crossed[slot] else '.room26')
                    milestones[key].append(dict(event=int(info['episode_index'][slot]), hp=float(info['hp'][slot]),
                        keys=float(rule.v(obs,'keys_div20')[slot]*20), opening_fights=int(fights[slot]),
                        elapsed_hours=float(info['elapsed_hours'][slot])))
                first_finish = valid & (before_clears==0) & (info['completions']>0)
                for slot in np.flatnonzero(first_finish):
                    milestones['first.finish'].append(dict(event=int(info['episode_index'][slot]),hours=float(info['elapsed_hours'][slot])))
                for slot in np.flatnonzero(done|trunc):
                    index = int(info['episode_index'][slot])
                    if index >= n: continue
                    row = {k:float(info[k][slot]) for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','invalid_actions')}
                    if (index in rows or trunc[slot] or row['invalid_actions'] or row['mushrooms']>budget
                        or row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms']
                        or not 167.99 <= row['elapsed_hours'] <= 168.000001):
                        raise RuntimeError('Invalid weekly event')
                    rows[index] = dict(event=index,**row)
                ended = np.flatnonzero(done|trunc)
                if len(ended): obs,masks = env.reset_done(ended)
    ordered = [rows[i] for i in range(n)]
    details = dict(rows=ordered,milestones=milestones)
    with gzip.open(out/f'{cohort}-{variant}-{budget}-rows.json.gz','wt',encoding='utf-8') as stream:
        json.dump(details,stream)
    result = dict(cohort=cohort,variant=variant,budget=budget,
        summaries={k:interval([r[k] for r in ordered]) for k in ordered[0] if k!='event'},
        opening_choices=dict(opening_doors),corrections=rule.counts,
        milestones={key:{k:dict(mean=float(np.mean([r[k] for r in values])),n=len(values))
                         for k in values[0] if k!='event'} for key,values in milestones.items()},
        seconds=time.monotonic()-started)
    write_once(out/f'{cohort}-{variant}-{budget}.json',result)
    print(json.dumps(dict(cohort=cohort,variant=variant,budget=budget,
        clears=result['summaries']['completions']['mean'],seconds=result['seconds'])),flush=True)
    return result,ordered


def main(args):
    out=args.output.resolve()
    if out.exists(): raise FileExistsError(out)
    roots = [2026101717,2026101718] if not args.smoke else [2026101797,2026101798]
    seed_audit = audit_seeds(roots)
    out.mkdir(parents=True)
    for source,name in ((CHECKPOINT,'checkpoint.pt'),(default_engine().with_name('sfld_rl.dll'),'sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')):
        copy(source,out/name)
    if train_rl.sha(out/'checkpoint.pt')!=CHECKPOINT_SHA: raise RuntimeError('Checkpoint changed')
    sources=[Path(__file__),ROOT/'CMakeLists.txt',ROOT/'tests/test_opening_fights.py']
    sources += [ROOT/'tools'/name for name in ('train_rl.py', 'benchmark_player_ideas.py',
                'train_continuation_study.py', 'train_current_study.py', 'visual_lab.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted(p for folder in ('src','include') for p in (ROOT/folder).rglob('*')
                      if p.suffix in ('.cpp','.hpp','.h','.c'))
    for source in sources: copy(source,out/'source'/source.relative_to(ROOT))
    plan=dict(created_utc=datetime.now(timezone.utc).isoformat(),budgets=[0,100],variants=VARIANTS,
        device='cuda' if torch.cuda.is_available() else 'cpu',
        checkpoint_sha256=CHECKPOINT_SHA,library_sha256=train_rl.sha(out/'sfld_rl.dll'),profile_sha256=train_rl.sha(out/'synthetic.profile'),
        sources={s.relative_to(ROOT).as_posix():train_rl.sha(s) for s in sources},
        selection=dict(seed=roots[0],namespace=231,events=4 if args.smoke else 256),
        final=dict(seed=roots[1],namespace=232,events=4 if args.smoke else 1024),
        intervention='In rooms 1-5, prefer a clean visible monster over mystery/Destiny until two ordinary-monster fights have occurred this run; fight the diverted encounters if maximum hit plus exit ticks are survivable. Keep blessing/hungry choices and all other decisions learned.',
        schedules='168 hours, start 17:00, always available; random eligible stone menus, unrestricted spending within cap.',
        variants_description=dict(control='Current neural plus runtime guards',two_all='All runs',two_first='Only the first event run',two_later='Only later runs',two_buffed='Only while OHW or healing per room covers the mean hit'),
        selection_rule='Equal-budget mean clears; control wins ties. Freeze selection before confirmation.',
        confirmation='Always confirm primary two_all versus control. Also confirm selection winner if distinct. Two budgets and equal-budget mean; simultaneous 95% normal intervals over all tested differences. Do not deploy automatically.',
        limits='Same trained continuation in every arm. Final stones use the actor, not the optional expensive per-choice rollout adviser. Monte Carlo intervals omit provisional mechanics uncertainty. Milestones are descriptive; later-run observations are clustered within weeks, with no independent-run confidence bounds claimed.')
    write_once(out/'seed-audit.json',seed_audit);write_once(out/'plan.json',plan)
    torch.set_num_threads(2)
    saved,model=train_rl.load_checkpoint(out/'checkpoint.pt',torch.device(plan['device']));model.eval()
    pilot={(v,b):evaluate(out,plan,model,saved,'selection',v,b)[0] for v in VARIANTS for b in plan['budgets']}
    scores={v:float(np.mean([pilot[v,b]['summaries']['completions']['mean'] for b in plan['budgets']])) for v in VARIANTS}
    winner=max(scores,key=scores.get)
    write_once(out/'selection.json',dict(winner=winner,scores=scores))
    confirmed=list(dict.fromkeys(['control','two_all',winner]))
    final={(v,b):evaluate(out,plan,model,saved,'final',v,b) for v in confirmed for b in plan['budgets']}
    z=NormalDist().inv_cdf(1-.05/(2*3*(len(confirmed)-1)))
    contrasts=[]
    for v in confirmed[1:]:
        deltas=[]
        for b in plan['budgets']:
            a=final[v,b][1];c=final['control',b][1]
            assert [r['event'] for r in a]==[r['event'] for r in c]
            delta=np.array([x['completions']-y['completions'] for x,y in zip(a,c)])
            deltas.append(delta)
            contrasts.append(dict(variant=v,budget=b,**interval(delta,z)))
        contrasts.append(dict(variant=v,budget='equal-budget mean',**interval(np.mean(deltas,axis=0),z)))
    for source,sha in plan['sources'].items():
        if train_rl.sha(ROOT/source)!=sha: raise RuntimeError('Source changed during experiment: '+source)
    write_once(out/'summary.json',dict(winner=winner,pilot_scores=scores,confirmed=confirmed,contrasts=contrasts,
        family_confidence=.95,final=[cell[0] for cell in final.values()],deployed=False,
        limits=plan['limits']))
    print(json.dumps(dict(done=True,winner=winner,contrasts=contrasts)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--smoke',action='store_true')
    main(parser.parse_args())
