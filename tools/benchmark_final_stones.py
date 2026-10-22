"""Every final stone set at HP1%, keys0/1/3/5/10 and a gross five-mushroom cap.

Identical final triples are simulated once and reused for their three held
pairs. This is exact after choosing the stone: the engine and actor see the
same owned stones, offered-history flags, HP, keys and remaining resources.
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
from benchmark_player_ideas import transfer_changes, write_once
from train_continuation_study import audit_seeds, status
from train_current_study import copy
from sfld_rl.effect_lab import EffectLab, mix64
from sfld_rl.env import VectorEnv
from sfld_rl.final_stones import KEYS, METRICS, catalog, counts, menu
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values
from sfld_rl.stone_grid import position
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[1]
WORKER={}


def prepare(out, workers, smoke):
    if out.exists():raise FileExistsError(out)
    roots=[20261015591,20261015592] if smoke else [20261015501,20261015502]
    audit=audit_seeds(roots)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Deployed actor changed')
    profile=profile_values(ROOT/'profiles/synthetic.profile');entries=catalog(profile,roots[1])
    if smoke:
        entries=[entries[0],entries[120],entries[-1]]
        for e in entries:e['histories']=e['histories'][:2]
    out.mkdir(parents=True)
    for source,name in ((CHECKPOINT,'checkpoint.pt'),(ROOT/'profiles/synthetic.profile','synthetic.profile'),
                        (ROOT/'build-v0612/Release/sfld_rl.dll','sfld_rl.dll')):copy(source,out/name)
    sources=[Path(__file__),ROOT/'tests/test_final_stones.py',ROOT/'tools/benchmark_stone_grid.py']
    sources += [ROOT/'tools'/n for n in ('train_rl.py','benchmark_player_ideas.py','train_continuation_study.py','train_current_study.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted((ROOT/'src').glob('*.cpp'))+sorted((ROOT/'include/sfld').glob('*.hpp'))
    plan=dict(schema='sfld-final-stones-v1',smoke=smoke,entries=entries,counts=counts(profile),workers=workers,
        keys=[0,10] if smoke else KEYS,hp=1,budget=5,horizon=168,event_start_hour=17,sleep_until_hour=0,
        futures=dict(seed=roots[0],per_history=4 if smoke else 128),history=dict(seed=roots[1]),metrics=METRICS,
        checkpoint_sha256=CHECKPOINT_SHA,profile_sha256=train_rl.sha(out/'synthetic.profile'),
        library_sha256=train_rl.sha(out/'sfld_rl.dll'),sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        objective='Minimize mean remaining hours until current-run completion, capped at 168 hours; gross spending at most five mushrooms.',
        state='Alive at the final stone choice after room75, HP1%, no temporary effects, no previous paid refills; stage4 shop pending. '
              'This is not re-entry at HP1%. Natural recovery and the current learned recovery policy operate after deaths.',
        history_panel='All seven six-rejection sets for every first-run triple. Eight frozen uniformly sampled legal six-rejection sets '
                      'per later-run triple. These are controlled history panels, not a sample of natural game-state frequencies. '
                      'Reported sampling confidence is conditional on these panels, not confidence over all possible histories.',
        reuse='After gem_0 is forced, each final set has identical observable state and future transition rules regardless of which '
              'of its three stones was last. One rollout therefore answers three possible held-pair/addition cases. Reuse is not new evidence.',
        seeds='mix64(future_root xor mix64(run+80001) xor mix64(history_index*replicates+replicate+400001)). '
              'Shared across sets and key levels within each run class. Histories and future seeds are not exposed to the policy.',
        inference='All pairwise third-stone contrasts, all held pairs and key levels, jointly Bonferroni-corrected at 95%. '
                  'Paired normal approximation with stratified variance across the fixed history panel. No forced winner for unresolved cells.',
        limitations='Current frozen downstream policy and synthetic game profile; no global-optimality claim. '
                    'Ranks cover potential third stones across histories; real play must restrict to the three actual offers and exclude prior rejections. '
                    'Initial effects, overnight schedule, refill history and shorter event deadlines are not varied.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for source in sources:copy(source,out/'source'/source.relative_to(ROOT))
    total=sum(len(e['histories'])*len(plan['keys'])*plan['futures']['per_history'] for e in entries)
    status(out,'prepared',sets=len(entries),trajectories=total)


def init_worker(folder):
    out=Path(folder);plan=json.loads((out/'plan.json').read_text(encoding='utf-8'))
    torch.set_num_threads(1)
    saved,model=train_rl.load_checkpoint(out/'checkpoint.pt',torch.device('cuda'));model.eval()
    WORKER.update(out=out,plan=plan,saved=saved,model=model,profile=profile_values(out/'synthetic.profile'),
                  entries={e['id']:e for e in plan['entries']})


def evaluate(identity):
    out=WORKER['out'];plan=WORKER['plan'];entry=WORKER['entries'][identity]
    target=out/'results'/f'set-{identity}.npz';meta=target.with_suffix('.json')
    if target.exists() and meta.exists():
        old=json.loads(meta.read_text(encoding='utf-8'))
        if old['data_sha256']!=train_rl.sha(target):raise RuntimeError('Saved data changed')
        return old
    keys=plan['keys'];histories=len(entry['histories']);reps=plan['futures']['per_history']
    total=len(keys)*histories*reps;n=min(1024,total);started=time.monotonic()
    data=np.full((len(keys),histories,reps,len(METRICS)),np.nan,dtype=np.float64)
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],n,budgets=[plan['budget']],seed=plan['futures']['seed'],
                   seed_namespace=232,auto_reset=False,deadline_hours=plan['horizon'],
                   event_start_hour=plan['event_start_hour'],sleep_until_hour=plan['sleep_until_hour']) as env:
        transfer_changes(WORKER['saved']['metadata']['environment_signature'],env.metadata,True)
        env.reset();lab=EffectLab(env);rule=InteractionPolicy(env,WORKER['profile'],'base')
        snapshots=[[lab.fixture(position(menu(entry,h),k,plan['hp']),plan['budget']) for h in range(histories)] for k in keys]
        active=np.zeros(n,bool);first=np.zeros(n,bool);jobs=np.full((n,3),-1,dtype=np.int64)
        cursor=finished=steps=0
        def assign(slot):
            nonlocal cursor
            if cursor<total:
                k,part=divmod(cursor,histories*reps);h,r=divmod(part,reps);cursor+=1
                jobs[slot]=(k,h,r);active[slot]=True;first[slot]=True
                seed=mix64(plan['futures']['seed']^mix64(entry['run']+80001)^mix64(h*reps+r+400001))
                lab.restore(int(slot),snapshots[k][h],seed)
            else:
                active[slot]=False;first[slot]=False
                lab.restore(int(slot),snapshots[0][0],mix64(plan['futures']['seed']^0xFADE))
        try:
            for slot in range(n):assign(slot)
            obs,masks=lab.observe()
            with torch.inference_mode():
                while active.any():
                    proposed=train_rl.policy_actions(WORKER['model'],obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                    actions,targets,_=rule.apply(obs,masks,proposed)
                    actions[active&first]=rule.ai['gem_0'];targets[active&first]=-1;first[:]=False
                    if not masks[np.arange(n),actions].all():raise RuntimeError('Illegal final-set action')
                    steps+=int(active.sum())
                    obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if np.any(targets>=0) else None)
                    if np.any(trunc&active) or np.any(info['invalid_actions'][active]):raise RuntimeError('Invalid or truncated continuation')
                    completed=info['first_completion_hours']>=0
                    ended=np.flatnonzero(active&(completed|done))
                    for slot in ended:
                        k,h,r=jobs[slot]
                        if not np.isnan(data[k,h,r,0]):raise RuntimeError('Duplicate result')
                        if info['mushrooms'][slot]>plan['budget']:raise RuntimeError('Mushroom cap exceeded')
                        data[k,h,r]=[info['first_completion_hours'][slot] if completed[slot] else plan['horizon'],
                            int(completed[slot]),info['deaths'][slot],info['mushrooms'][slot],info['actions'][slot],
                            int(completed[slot] and info['deaths'][slot]==0)]
                        finished+=1;assign(int(slot))
                    idle_done=np.flatnonzero(~active&done)
                    for slot in idle_done:assign(int(slot))
                    if len(ended) or len(idle_done):obs,masks=lab.observe()
            if finished!=total or not np.isfinite(data).all():raise RuntimeError('Missing final-set outcome')
        finally:
            for row in snapshots:
                for snapshot in row:snapshot.close()
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp.npz');np.savez_compressed(temporary,data=data);temporary.replace(target)
    result=dict(id=identity,run=entry['run'],stones=entry['stones'],keys=keys,hp=plan['hp'],budget=plan['budget'],
        histories=histories,replicates=reps,trajectories=total,actions=steps,seconds=time.monotonic()-started,
        data_sha256=train_rl.sha(target))
    write_once(meta,result);return result


def run(out,workers=8,smoke=False):
    if not out.exists():prepare(out,workers,smoke)
    plan=json.loads((out/'plan.json').read_text(encoding='utf-8'))
    def check():
        for name,digest in plan['sources'].items():
            if train_rl.sha(ROOT/name)!=digest:raise RuntimeError('Frozen source changed: '+name)
    check();records=[];begin=last=time.monotonic()
    with ProcessPoolExecutor(max_workers=plan['workers'],mp_context=multiprocessing.get_context('spawn'),
                             initializer=init_worker,initargs=(str(out),)) as executor:
        pending=[executor.submit(evaluate,e['id']) for e in plan['entries']]
        for future in as_completed(pending):
            records.append(future.result());now=time.monotonic()
            if now-last>20 or len(records)==len(pending):
                status(out,'simulating',sets=len(records),total_sets=len(pending),
                       trajectories=sum(r['trajectories'] for r in records),seconds=now-begin)
                last=now
    check()
    summary=dict(trajectories=sum(r['trajectories'] for r in records),actions=sum(r['actions'] for r in records),sets=len(records))
    write_once(out/'summary.json',summary);status(out,'complete',**summary)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=8);p.add_argument('--smoke',action='store_true');a=p.parse_args()
    if not 1<=a.workers<=8:p.error('workers must be 1..8')
    run(a.output.resolve(),a.workers,a.smoke)
