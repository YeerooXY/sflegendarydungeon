"""Controlled final-stone comparisons across held pairs, keys and health.

Every branch forces one of a legal three-stone menu at room 76, then uses the
frozen deployed policy until room 100 or the horizon. Screening covers every
eligible held-pair/candidate; fresh confirmation tests preselected reversals.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
from pathlib import Path
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes,write_once
from train_continuation_study import audit_seeds,status
from train_current_study import copy
from sfld_rl.effect_lab import EffectLab,mix64
from sfld_rl.env import VectorEnv
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values
from sfld_rl.stone_grid import HEALTH,KEYS,METRICS,menus,position,crossover_selection
from sfld_rl.visual_policy import CHECKPOINT,CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[1]
WORKER={}


def prepare(out,smoke,budgets,workers,roots=None):
    if out.exists():raise FileExistsError(out)
    roots=roots or ([20261015403,20261015404] if smoke else [20261015401,20261015402])
    audit=audit_seeds(roots)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Deployed policy changed')
    profile=profile_values(ROOT/'profiles/synthetic.profile');catalog=menus(profile)
    if smoke:catalog=[catalog[0],catalog[135],catalog[900]]
    # IDs are stable catalog identifiers, including smoke subsets.
    out.mkdir(parents=True)
    for source,name in ((CHECKPOINT,'checkpoint.pt'),(ROOT/'profiles/synthetic.profile','synthetic.profile'),
                        (ROOT/'build-v0612/Release/sfld_rl.dll','sfld_rl.dll')):copy(source,out/name)
    sources=[Path(__file__),ROOT/'tests/test_stone_grid.py']
    sources += [ROOT/'tools'/n for n in ('train_rl.py','benchmark_player_ideas.py','train_continuation_study.py','train_current_study.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted((ROOT/'src').glob('*.cpp'))+sorted((ROOT/'include/sfld').glob('*.hpp'))
    plan=dict(schema='sfld-final-stone-grid-v1',smoke=smoke,menus=catalog,health=[1,75] if smoke else HEALTH,
        keys=[0,10] if smoke else KEYS,budgets=budgets,workers=workers,
        screening=dict(seed=roots[0],trials=2 if smoke else 8),
        confirmation=dict(seed=roots[1],trials=16 if smoke else 2048,per_family=2 if smoke else 12),
        horizon=168,event_start_hour=17,sleep_until_hour=0,metrics=METRICS,
        checkpoint_sha256=CHECKPOINT_SHA,profile_sha256=train_rl.sha(out/'synthetic.profile'),
        library_sha256=train_rl.sha(out/'sfld_rl.dll'),sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        objective='Restricted mean hours from the living room-76 stone choice until current-run completion, censored at 168 hours.',
        state='Synthetic, no temporary effects, full prior-shop history, four rejected stones. HP below 20% is alive, not recovery. '
              'HP100 is a stress case: current room-75 boss damage allows at most 77.05% immediately after a full-health boss fight.',
        coverage='Every pair of held stones and every eligible third stone, in both run pools; balanced menus, not every three-offer combination. '
                 'Each menu uses one legal synthetic rejected history. Keys 0-10 and declared HP grid; not every integer HP.',
        seeds='For trial t, mix64(root xor mix64(menu_id+70001) xor mix64(t+300001)). '
              'Shared across arms, key counts, HP and budgets for that menu. Fresh confirmation root. No actual future is exposed.',
        selection='For each budget, choose up to 12 key reversals and 12 HP reversals by smaller endpoint screening advantage; at most one per menu/family. '
                  'Compare keys 0/10 at fixed reachable HP or HP1/75 at fixed keys. Freeze before confirmation; evaluate all three offers.',
        limitations='Eight samples per screening action are reconnaissance only. No confident winner label on the broad grid. '
                    'Frozen downstream policy, no retraining, no estimate of natural state frequency, no global-optimality claim. '
                    'Initial effects, prior paid-refill count and elapsed event time remain separate dimensions.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for source in sources:copy(source,out/'source'/source.relative_to(ROOT))
    status(out,'prepared',menus=len(catalog),screening_trajectories=len(catalog)*len(plan['health'])*len(plan['keys'])*3*plan['screening']['trials']*len(budgets))


def init_worker(folder):
    out=Path(folder);plan=json.loads((out/'plan.json').read_text(encoding='utf-8'))
    torch.set_num_threads(1)
    saved,model=train_rl.load_checkpoint(out/'checkpoint.pt',torch.device('cuda'));model.eval()
    WORKER.update(out=out,plan=plan,saved=saved,model=model,profile=profile_values(out/'synthetic.profile'),
                  menus={m['id']:m for m in plan['menus']})


def evaluate(task):
    phase,budget,menu_id,identity,states=task
    out=WORKER['out'];plan=WORKER['plan'];menu=WORKER['menus'][menu_id]
    target=out/phase/f'b{budget}-{identity}.npz';meta=target.with_suffix('.json')
    if target.exists() and meta.exists():
        old=json.loads(meta.read_text(encoding='utf-8'))
        if old['data_sha256']!=train_rl.sha(target):raise RuntimeError('Saved grid data changed')
        return old
    trials=plan[phase]['trials'];total=len(states)*trials*3;n=min(1024,total);start=time.monotonic()
    data=np.full((len(states),trials,3,len(METRICS)),np.nan,dtype=np.float64)
    baseline=np.full(len(states),-1,dtype=np.int8)
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],n,budgets=[budget],seed=plan[phase]['seed'],
                   seed_namespace=231,auto_reset=False,deadline_hours=plan['horizon'],
                   event_start_hour=plan['event_start_hour'],sleep_until_hour=plan['sleep_until_hour']) as env:
        transfer_changes(WORKER['saved']['metadata']['environment_signature'],env.metadata,True)
        env.reset();lab=EffectLab(env);rule=InteractionPolicy(env,WORKER['profile'],'base')
        snaps=[lab.fixture(position(menu,s['keys'],s['hp'],s.get('blessings',''),s.get('curses','')),budget) for s in states]
        active=np.zeros(n,bool);first=np.zeros(n,bool);jobs=np.full((n,3),-1,dtype=np.int64)
        cursor=0;finished=0;steps=0
        def assign(slot):
            nonlocal cursor
            if cursor<total:
                state,part=divmod(cursor,trials*3);trial,arm=divmod(part,3);cursor+=1
                jobs[slot]=(state,trial,arm);active[slot]=True;first[slot]=True
                seed=mix64(plan[phase]['seed']^mix64(menu_id+70001)^mix64(trial+300001))
                lab.restore(int(slot),snaps[state],seed)
            else:
                active[slot]=False;first[slot]=False
                lab.restore(int(slot),snaps[0],mix64(plan[phase]['seed']^0xFACE))
        try:
            for slot in range(n):assign(slot)
            obs,masks=lab.observe()
            with torch.inference_mode():
                while active.any():
                    proposed=train_rl.policy_actions(WORKER['model'],obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                    actions,targets,_=rule.apply(obs,masks,proposed)
                    for slot in np.flatnonzero(active&first):
                        s,t,a=jobs[slot];choice=int(actions[slot])-rule.ai['gem_0']
                        if not 0<=choice<3:raise RuntimeError('Policy missed final stone choice')
                        if baseline[s] not in (-1,choice):raise RuntimeError('Nondeterministic initial choice')
                        baseline[s]=choice;actions[slot]=rule.ai['gem_'+str(a)];targets[slot]=-1
                    first[:]=False
                    if not masks[np.arange(n),actions].all():raise RuntimeError('Illegal grid action')
                    steps+=int(active.sum())
                    obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if np.any(targets>=0) else None)
                    if np.any(trunc&active) or np.any(info['invalid_actions'][active]):raise RuntimeError('Invalid or truncated grid trajectory')
                    completed=info['first_completion_hours']>=0
                    ended=np.flatnonzero(active&(completed|done))
                    for slot in ended:
                        s,t,a=jobs[slot]
                        if not np.isnan(data[s,t,a,0]):raise RuntimeError('Duplicate grid result')
                        if info['mushrooms'][slot]>budget:raise RuntimeError('Budget exceeded')
                        data[s,t,a]=[info['first_completion_hours'][slot] if completed[slot] else plan['horizon'],
                            int(completed[slot]),info['deaths'][slot],info['mushrooms'][slot],info['actions'][slot],
                            int(completed[slot] and info['deaths'][slot]==0)]
                        finished+=1;assign(int(slot))
                    idle_done=np.flatnonzero(~active&done)
                    for slot in idle_done:assign(int(slot))
                    if len(ended) or len(idle_done):obs,masks=lab.observe()
            if finished!=total or not np.isfinite(data).all():raise RuntimeError('Incomplete grid shard')
        finally:
            for snap in snaps:snap.close()
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp.npz');np.savez_compressed(temporary,data=data,baseline=baseline);temporary.replace(target)
    result=dict(phase=phase,budget=budget,menu_id=menu_id,identity=identity,states=states,trials=trials,
        trajectories=total,actions=steps,seconds=time.monotonic()-start,data_sha256=train_rl.sha(target))
    write_once(meta,result);return result


def pool_stage(out,plan,phase,tasks):
    records=[];begin=time.monotonic();last=begin
    with ProcessPoolExecutor(max_workers=plan['workers'],mp_context=multiprocessing.get_context('spawn'),
                             initializer=init_worker,initargs=(str(out),)) as executor:
        pending=[executor.submit(evaluate,t) for t in tasks]
        for future in as_completed(pending):
            records.append(future.result());now=time.monotonic()
            if now-last>20 or len(records)==len(tasks):
                status(out,phase,shards=len(records),total_shards=len(tasks),
                       trajectories=sum(r['trajectories'] for r in records),seconds=now-begin)
                last=now
    return records


def run(out,smoke=False,budgets=(0,),workers=4,roots=None):
    if not out.exists():prepare(out,smoke,list(budgets),workers,roots)
    plan=json.loads((out/'plan.json').read_text(encoding='utf-8'))
    for name,digest in plan['sources'].items():
        if train_rl.sha(ROOT/name)!=digest:raise RuntimeError('Frozen source changed: '+name)
    states=[dict(hp=h,keys=k) for h in plan['health'] for k in plan['keys']]
    tasks=[('screening',b,m['id'],'menu-'+str(m['id']),states) for b in plan['budgets'] for m in plan['menus']]
    screening=pool_stage(out,plan,'screening',tasks)
    def arrays():
        for b in plan['budgets']:
            for m in plan['menus']:
                with np.load(out/'screening'/f'b{b}-menu-{m["id"]}.npz') as data:
                    yield (b,m['id']),data['data'].reshape(len(plan['health']),len(plan['keys']),plan['screening']['trials'],3,len(METRICS))
    chosen=crossover_selection(plan,arrays(),plan['confirmation']['per_family'])
    selection=out/'selection.json'
    if selection.exists():
        if json.loads(selection.read_text(encoding='utf-8'))!=chosen:raise RuntimeError('Frozen selection changed')
    else:write_once(selection,chosen)
    tasks=[('confirmation',r['budget'],r['menu_id'],'case-'+str(r['id']),r['states']) for r in chosen]
    confirmation=pool_stage(out,plan,'confirmation',tasks) if tasks else []
    for name,digest in plan['sources'].items():
        if train_rl.sha(ROOT/name)!=digest:raise RuntimeError('Frozen source changed: '+name)
    write_once(out/'summary.json',dict(screening_trajectories=sum(r['trajectories'] for r in screening),
        confirmation_trajectories=sum(r['trajectories'] for r in confirmation),
        actions=sum(r['actions'] for r in screening+confirmation),selected_comparisons=len(chosen)))
    status(out,'complete',screening_trajectories=sum(r['trajectories'] for r in screening),
        confirmation_trajectories=sum(r['trajectories'] for r in confirmation))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budgets',default='0');p.add_argument('--workers',type=int,default=4);p.add_argument('--smoke',action='store_true')
    p.add_argument('--screen-seed',type=int);p.add_argument('--confirmation-seed',type=int)
    a=p.parse_args()
    if not 1<=a.workers<=8:p.error('workers must be 1..8')
    if bool(a.screen_seed is None)!=bool(a.confirmation_seed is None):p.error('Specify both seed roots together')
    roots=[a.screen_seed,a.confirmation_seed] if a.screen_seed is not None else None
    run(a.output.resolve(),a.smoke,tuple(map(int,a.budgets.split(','))),a.workers,roots)
