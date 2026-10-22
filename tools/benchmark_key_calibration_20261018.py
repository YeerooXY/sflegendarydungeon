"""Paired weekly evaluation of new key estimates and fight-only damage stones."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import shutil
from statistics import NormalDist
import time

import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes, write_once
from train_continuation_study import audit_seeds
from sfld_rl.env import VectorEnv
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    'previous': {},
    'keys85': {'key_chance': '.85'},
    'updated': {'key_chance': '.85', 'fight_only_damage_stones': 'true'},
    'spying_observed': {'key_chance': '.85', 'fight_only_damage_stones': 'true',
                         'spying_key_penalty': '0.26666666666666666'},
}
METRICS = ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths',
           'legendaries','elapsed_hours','actions','invalid_actions','first_completion_hours')
MODEL = SAVED = None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def interval(values, z=1.959963984540054):
    values = np.asarray(values, dtype=float)
    if not len(values): return None
    mean = float(values.mean())
    se = float(values.std(ddof=1)/len(values)**.5) if len(values)>1 else 0
    return {'mean':mean,'se':se,'ci':[mean-z*se,mean+z*se],'n':len(values)}


def initialize(out):
    global MODEL,SAVED
    torch.set_num_threads(1)
    plan=read(Path(out)/'plan.json')
    SAVED,MODEL=train_rl.load_checkpoint(Path(out)/'checkpoint.pt',torch.device(plan['device']))
    MODEL.eval()


def evaluate(out, case, budget, wake):
    out=Path(out);plan=read(out/'plan.json');setup=plan['cohort'];count=setup['events']
    size=min(512,count);profile=out/(case+'.profile')
    label=f'{case}-{budget}-'+('continuous' if wake==0 else 'sleep_00_08')
    rows={};milestones={};started=time.monotonic();decisions=0
    with VectorEnv(out/'sfld_rl.dll',[profile],size,budgets=[budget],seed=setup['seed'],
                   seed_namespace=setup['namespace'],auto_reset=False,
                   event_start_hour=17,sleep_until_hour=wake) as env:
        transfer_changes(SAVED['metadata']['environment_signature'],env.metadata,True)
        rule=InteractionPolicy(env,profile_values(profile),'base');rule.count_before_episode=count
        obs,masks=env.reset();elapsed=np.zeros(size)
        with torch.inference_mode():
            while len(rows)<count:
                valid=env.episode_indices<count
                if wake and np.any(((17+elapsed[valid])%24)<wake-1e-8):
                    raise RuntimeError('Decision during sleep')
                room=np.rint(rule.v(obs,'room_div100')*100)
                first=rule.flag(obs,'first_event_run').copy()
                shop=valid&first&(room>5)&rule.flag(obs,'phase.shop')
                for slot in np.flatnonzero(shop):
                    mark=milestones.setdefault(int(env.episode_indices[slot]),{})
                    mark.setdefault('first_paid_shop_keys',float(rule.v(obs,'keys_div20')[slot]*20))
                proposed=train_rl.policy_actions(MODEL,obs,masks,plan['device'],greedy=True)[0].cpu().numpy()
                actions,targets,_=rule.apply(obs,masks,proposed)
                if np.any(targets>=0) or not masks[np.arange(size),actions].all():
                    raise RuntimeError('Unexpected wait or illegal action')
                decisions+=int(valid.sum())
                obs,masks,_,done,trunc,info=env.step(actions)
                elapsed=info['elapsed_hours'].copy()
                crossed=valid&first&(room==25)&(info['room']==26)
                for slot in np.flatnonzero(crossed):
                    mark=milestones.setdefault(int(info['episode_index'][slot]),{})
                    mark['first_boss25_keys']=float(rule.v(obs,'keys_div20')[slot]*20)
                ended=np.flatnonzero(done|trunc)
                if len(ended):
                    totals=env.reward_totals()
                    for slot in ended:
                        index=int(info['episode_index'][slot])
                        if index>=count:continue
                        row={k:float(info[k][slot]) for k in METRICS}
                        row.update({k:float(v[slot]) for k,v in totals.items()})
                        if (index in rows or trunc[slot] or not done[slot] or row['invalid_actions']
                            or row['mushrooms']>budget or row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms']
                            or not 167.99<=row['elapsed_hours']<=168.000001):
                            raise RuntimeError('Invalid weekly event: '+str(row))
                        rows[index]={'event':index,**row,**milestones.pop(index,{})}
                    obs,masks=env.reset_done(ended);elapsed[ended]=0
    ordered=[rows[i] for i in range(count)]
    summary={k:interval([r[k] for r in ordered]) for k in ordered[0]
             if k not in ('event','first_completion_hours','first_boss25_keys','first_paid_shop_keys')}
    for key in ('first_boss25_keys','first_paid_shop_keys'):
        summary[key]=interval([r[key] for r in ordered if key in r])
    first=[r['first_completion_hours'] for r in ordered if r['first_completion_hours']>=0]
    summary['first_completion_hours_among_completed']=interval(first)
    summary['first_paid_shop_at_least_four_keys']=interval([r['first_paid_shop_keys']>=3.99999 for r in ordered if 'first_paid_shop_keys' in r])
    result={'case':case,'budget':budget,'sleep_until_hour':wake,'label':label,
            'summary':summary,'environment':env.metadata,'corrections':rule.counts,
            'first_clear_fraction':len(first)/count,'decisions':decisions,'seconds':time.monotonic()-started}
    with gzip.open(out/(label+'-rows.json.gz'),'wt',encoding='utf-8') as f:json.dump(ordered,f,allow_nan=False)
    write_once(out/(label+'.json'),result)
    return result


def summarize(out, plan, cells):
    lookup={(c['case'],c['budget'],c['sleep_until_hour']):c for c in cells}
    contrasts=[]
    # Three predeclared comparisons against control for each budget/schedule,
    # plus isolation of the damage change and Spying sensitivity.
    pairs=[('previous','keys85'),('previous','updated'),('previous','spying_observed'),
           ('keys85','updated'),('updated','spying_observed')]
    family=len(pairs)*len(plan['budgets'])*len(plan['schedules'])
    z=NormalDist().inv_cdf(1-.05/(2*family))
    for wake in plan['schedules']:
        for budget in plan['budgets']:
            cached={}
            for case in CASES:
                label=lookup[case,budget,wake]['label']
                with gzip.open(out/(label+'-rows.json.gz'),'rt',encoding='utf-8') as f:cached[case]=json.load(f)
            for old,new in pairs:
                a,b=cached[old],cached[new]
                assert [r['event'] for r in a]==[r['event'] for r in b]
                delta=[y['completions']-x['completions'] for x,y in zip(a,b)]
                contrasts.append({'old':old,'new':new,'budget':budget,'sleep_until_hour':wake,
                                  'paired_95':interval(delta),'simultaneous_95':interval(delta,z)})
    for filename,expected in plan['sources'].items():
        if train_rl.sha(ROOT/filename)!=expected:raise RuntimeError('Frozen source changed: '+filename)
    for filename,expected in plan['artifacts'].items():
        if train_rl.sha(out/filename)!=expected:raise RuntimeError('Frozen artifact changed: '+filename)
    result={'schema':1,'cells':sorted(cells,key=lambda x:(x['sleep_until_hour'],x['budget'],x['case'])),
            'contrasts':contrasts,'family_comparisons':family,'events_per_cell':plan['cohort']['events'],
            'total_simulated_weeks':len(cells)*plan['cohort']['events'],
            'trained_policy_changed':False,'deployed_profile_changed':False,
            'limits':plan['limits']}
    write_once(out/'summary.json',result)
    write_once(out/'verification.json',{'passed':True,'cells':len(cells),
               'weeks':result['total_simulated_weeks'],'valid_actions_and_budget_accounting':True,
               'sleep_window_enforced':True,'paired_event_ids':True,'frozen_sources_and_artifacts_unchanged':True,
               'checkpoint_sha256':CHECKPOINT_SHA})
    return result


def main(args):
    out=args.output.resolve()
    if out.exists():raise FileExistsError(out)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Checkpoint identity changed')
    seed=2026101891 if args.smoke else 2026101821
    seed_audit=audit_seeds([seed]);out.mkdir(parents=True)
    shutil.copy2(CHECKPOINT,out/'checkpoint.pt')
    shutil.copy2(ROOT/'build-calibration-20261018/Release/sfld_rl.dll',out/'sfld_rl.dll')
    original=(ROOT/'profiles/synthetic.profile').read_text(encoding='utf-8')
    for case,settings in CASES.items():
        text=original
        if settings:
            lines=text.splitlines();changes={'id':'key-calibration-2026-10-18-'+case,**settings}
            for key,value in changes.items():
                positions=[i for i,line in enumerate(lines) if line.startswith(key+'=')]
                if positions:lines[positions[0]]=key+'='+value
                else:lines.append(key+'='+value)
            text='# Experimental calibration scenario; see plan.json. Not a verified game formula.\n'+'\n'.join(lines)+'\n'
        (out/(case+'.profile')).write_text(text,encoding='utf-8')
    sources=[Path(__file__),ROOT/'CMakeLists.txt',ROOT/'profiles/synthetic.profile']
    sources += [ROOT/'tools'/n for n in ('train_rl.py','benchmark_player_ideas.py','train_continuation_study.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted(p for folder in ('src','include','tests') for p in (ROOT/folder).rglob('*') if p.suffix in ('.cpp','.hpp','.h'))
    for source in sources:
        target=out/'source'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    plan={'schema':1,'created_utc':datetime.now(timezone.utc).isoformat(),
          'cohort':{'seed':seed,'namespace':235 if not args.smoke else 234,'events':4 if args.smoke else args.events},
          'budgets':[0,1000] if args.smoke else [0,20,50,100,500,1000], 'schedules':[0,8],
          'event_start_hour':17,'hours':168,'cases':CASES,'workers':args.workers,
          'device':'cuda' if torch.cuda.is_available() else 'cpu','checkpoint_sha256':CHECKPOINT_SHA,
          'policy':'Frozen deployed neural actor plus InteractionPolicy(base); no final-stone rollout planner.',
          'sources':{p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
          'artifacts':{p.name:train_rl.sha(p) for p in out.iterdir() if p.is_file()},
          'trial_reward_weights_5':[19,20,6,3],
          'spying_sensitivity':'0.85 minus0.2666667 gives58.3333% without Lodestone; with Lodestone it gives88.3333%. This extrapolated joint-stone behavior is an assumption, not measured.',
          'limits':'Frozen policy transfer, not retraining or an optimality claim. Random eligible stones; ordinary Legendary Dungeon; free and paid budgets separate. Simultaneous paired95% intervals cover Monte Carlo error only, not uncertain game mechanics. First-clear times condition on completion.'}
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',seed_audit)
    jobs=[(str(out),case,budget,wake) for budget in plan['budgets'] for wake in plan['schedules'] for case in CASES]
    cells=[]
    with ProcessPoolExecutor(max_workers=args.workers,initializer=initialize,initargs=(str(out),)) as pool:
        futures=[pool.submit(evaluate,*job) for job in jobs]
        for future in as_completed(futures):
            cell=future.result();cells.append(cell)
            print(json.dumps({'finished':len(cells),'total':len(jobs),'cell':cell['label'],
                              'clears':cell['summary']['completions']['mean'],'seconds':cell['seconds']}),flush=True)
    result=summarize(out,plan,cells)
    print(json.dumps({'complete':True,'weeks':result['total_simulated_weeks']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--events',type=int,default=2048)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    if args.events<2 or not 1<=args.workers<=8:parser.error('Require events>=2 and workers1–8')
    main(args)
