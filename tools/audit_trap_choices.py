"""Natural trapped-door choices: is the adviser right to take the trap?

The deployed adviser plays fresh weeks. At every door screen pairing an
untrapped sacrifice or cursed door with a trapped mystery or monster door, a
snapshot is kept with the visible context. Every position where the adviser
takes the trap is kept, plus up to --safe-cap positions where it takes the
safe door. Each position is then played both ways by the same frozen adviser
on matched fresh seeds. Run several shards with different --shard values and
merge them with --merge.
"""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
import audit_local_optimality as audit

EFFECTS=('disarm','lockpick','escape','one_hit','recovery','elixir','key_moment','raider')
CURSES=('broken_armor','poison','clumsy','gold_hangover','hard_lock')


def context(obs, I, s, kinds, gems, side):
    v=lambda n:float(obs[s,I[n]])
    held=lambda group,kind:any(v(f'{group}.{k}.kind.{kind}')>.5 and v(f'{group}.{k}.active')>.5 for k in range(3))
    door=lambda i:next(k for k in kinds if v(f'doors.{i}.kind.{k}')>.5)
    return dict(room=int(round(v('room_div100')*100)),hp=round(v('hp'),4),keys=int(round(v('keys_div20')*20)),
                trapped_kind=door(side),safe_kind=door(1-side),cursed_trap=v(f'doors.{side}.cursed_trap')>.5,
                hours_left=round(v('hours_left_div168')*168,2),
                blessings=[e for e in EFFECTS if held('blessings',e)],curses=[c for c in CURSES if held('curses',c)],
                gems=[g for g in gems if v('owned_gem.'+g)>.5])


def sample(env, lab, adviser, weeks, root, safe_cap):
    I={n:i for i,n in enumerate(env.observation_names)};names=env.observation_names
    kinds=[n.split('.')[-1] for n in names if n.startswith('doors.0.kind.')]
    gems=[n.split('.',1)[1] for n in names if n.startswith('owned_gem.')]
    door=[env.action_names.index('door_0'),env.action_names.index('door_1')]
    states=[];snaps=[];safe=0;finished=0;obs,masks=env.reset()
    while finished<weeks:
        actions,targets=adviser(obs,masks)
        for s in np.flatnonzero(obs[:,I['phase.doors']]>.5):
            if env.episode_indices[s]>=weeks:continue
            for side in (0,1):
                trapped=obs[s,I[f'doors.{side}.trap']]>.5 and any(obs[s,I[f'doors.{side}.kind.{k}']]>.5 for k in ('mystery','monster'))
                other=1-side
                plain=obs[s,I[f'doors.{other}.trap']]<.5 and any(obs[s,I[f'doors.{other}.kind.{k}']]>.5 for k in ('sacrifice','cursed'))
                if not (trapped and plain and masks[s,door[0]] and masks[s,door[1]]):continue
                took=bool(actions[s]==door[side])
                if not took and safe>=safe_cap:continue
                safe+=not took
                c=context(obs,I,s,kinds,gems,side);c.update(id=len(states),episode=int(env.episode_indices[s]),
                    trapped_side=side,adviser_took_trap=took)
                states.append(c);snaps.append(lab.capture(int(s)))
        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
        if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid sampling week')
        ended=np.flatnonzero(done)
        if len(ended):
            finished+=int((env.episode_indices[ended]<weeks).sum());obs,masks=env.reset_done(ended)
    return states,snaps


def run(args):
    out=args.output/f'shard-{args.shard}';out.mkdir(parents=True,exist_ok=True)
    seed=args.seed+args.shard
    plan=dict(weeks=args.weeks,trials=args.trials,budget=args.budget,seed=seed,namespace=args.namespace,safe_cap=args.safe_cap,
              checkpoint_sha256=audit.CHECKPOINT_SHA,library_sha256=train_rl.sha(audit.LIBRARY),profile_sha256=train_rl.sha(audit.PROFILE))
    write_once(out/'plan.json',plan)
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    root=(args.namespace<<56)|(train_rl.seed_fingerprint(seed)<<24);started=time.monotonic()
    with VectorEnv(audit.LIBRARY,[audit.PROFILE],512,budgets=[args.budget],seed=seed,seed_namespace=args.namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu')
        states,snaps=sample(env,lab,adviser,args.weeks,root,args.safe_cap)
        print(json.dumps(dict(stage='sample',positions=len(states),took_trap=sum(s['adviser_took_trap'] for s in states),
                              seconds=round(time.monotonic()-started))),flush=True)
        try:
            door=[env.action_names.index('door_0'),env.action_names.index('door_1')]
            jobs=[(s['id'],a,t) for s in states for t in range(args.trials) for a in door]
            records=audit.branch(env,lab,adviser,snaps,jobs,mix64(root^0x7A),'trap-choices')
        finally:
            for s in snaps:s.close()
    for s in states:
        d=np.array([records[s['id'],door[s['trapped_side']],t][0]-records[s['id'],door[1-s['trapped_side']],t][0]
                    for t in range(args.trials)])
        s.update(trap_minus_safe=float(d.mean()),se=float(d.std(ddof=1)/args.trials**.5))
    write_once(out/'results.json',dict(plan=plan,states=states,seconds=time.monotonic()-started,scope=__doc__.strip()))
    print(json.dumps(dict(finished=str(out),positions=len(states),seconds=round(time.monotonic()-started))),flush=True)


def merge(directory):
    states=[]
    for path in sorted(Path(directory).glob('shard-*/results.json')):states+=json.loads(path.read_text(encoding='utf-8'))['states']
    def report(label, group):
        if not group:return
        d=np.array([s['trap_minus_safe'] for s in group]);se=np.sqrt(np.sum([s['se']**2 for s in group]))/len(group)
        print(f'{label:58} n={len(group):5}  trap-safe {d.mean():+.4f} (95% CI {d.mean()-1.96*se:+.4f} to {d.mean()+1.96*se:+.4f})')
    for took in (True, False):
        print(f"\nAdviser took the {'TRAPPED' if took else 'SAFE'} door")
        g=[s for s in states if s['adviser_took_trap']==took];report('all',g)
        for name,test in (('Disarm active',lambda s:'disarm' in s['blessings']),('no Disarm',lambda s:'disarm' not in s['blessings']),
                          ('cursed trap (Gambler)',lambda s:s['cursed_trap']),('Masochist held',lambda s:'masochist' in s['gems']),
                          ('safe door = sacrifice',lambda s:s['safe_kind']=='sacrifice'),('safe door = cursed',lambda s:s['safe_kind']=='cursed'),
                          ('trapped = monster',lambda s:s['trapped_kind']=='monster'),('trapped = mystery',lambda s:s['trapped_kind']=='mystery'),
                          ('HP < 30%',lambda s:s['hp']<.3),('HP 30-60%',lambda s:.3<=s['hp']<.6),('HP >= 60%',lambda s:s['hp']>=.6),
                          ('within 3 rooms of a boss',lambda s:s['room']%25>=22),('curse held',lambda s:bool(s['curses'])),
                          ('no Disarm, no cursed trap',lambda s:'disarm' not in s['blessings'] and not s['cursed_trap'])):
            report('  '+name,[s for s in g if test(s)])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--shard',type=int,default=0);p.add_argument('--weeks',type=int,default=64)
    p.add_argument('--trials',type=int,default=128);p.add_argument('--budget',type=int,default=0)
    p.add_argument('--safe-cap',type=int,default=100)
    p.add_argument('--seed',type=int,default=2026102320);p.add_argument('--namespace',type=int,default=0x5A)
    p.add_argument('--merge',action='store_true')
    a=p.parse_args()
    merge(a.output) if a.merge else run(a)
