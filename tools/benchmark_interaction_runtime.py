"""Select and confirm interaction candidates using the actual desktop guards."""
import argparse
import json
from pathlib import Path
from statistics import mean,stdev,NormalDist
import time
import numpy as np
import torch
import train_rl
from train_continuation_study import audit_seeds,status
from train_current_study import copy,read
from benchmark_player_ideas import write_once,transfer_changes
from publish_effect_study import stats
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.interaction_policy import InteractionPolicy,VARIANTS

ROOT=Path(__file__).resolve().parents[1]
BUDGETS=[0,20,50,100,500,1000]


def evaluate(out,plan,candidate,cohort,budget):
    dest=out/f'{cohort}-{candidate["name"]}-{budget}.json'
    if dest.exists():raise FileExistsError(dest)
    setup=plan[cohort];checkpoint=Path(candidate['checkpoint'])
    if train_rl.sha(checkpoint)!=candidate['sha256']:raise RuntimeError('Checkpoint changed')
    saved,model=train_rl.load_checkpoint(checkpoint,torch.device('cuda'));model.eval()
    rows={};start=time.monotonic();profile=profile_values(out/'synthetic.profile')
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],min(512,setup['events']),budgets=[budget],
                   seed=setup['seed'],seed_namespace=setup['namespace'],auto_reset=False,max_actions=100000) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        rule=InteractionPolicy(env,profile,candidate['variant']);rule.count_before_episode=setup['events']
        obs,masks=env.reset()
        with torch.inference_mode():
            while len(rows)<setup['events']:
                actions=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                actions,targets,_=rule.apply(obs,masks,actions)
                if np.any(targets>=0):raise RuntimeError('Unexpected wait override')
                obs,masks,_,done,trunc,info=env.step(actions)
                ended=np.flatnonzero(done|trunc)
                if len(ended):
                    totals=env.reward_totals()
                    for slot in ended:
                        index=int(info['episode_index'][slot])
                        if index>=setup['events']:continue
                        if index in rows:raise RuntimeError('Duplicate week')
                        row=dict(event=index)
                        for key in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','actions','invalid_actions'):
                            row[key]=float(info[key][slot])
                        row.update({k:float(v[slot]) for k,v in totals.items()})
                        if (trunc[slot] or not done[slot] or row['invalid_actions'] or row['mushrooms']>budget or
                            row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or not 167.99<=row['elapsed_hours']<=168.000001):
                            raise RuntimeError('Invalid week')
                        rows[index]=row
                    obs,masks=env.reset_done(ended)
    ordered=[rows[i] for i in range(setup['events'])]
    def interval(values):
        m=mean(values);se=stdev(values)/len(values)**.5
        return dict(mean=m,standard_error=se,normal_95_ci=[m-1.96*se,m+1.96*se])
    result=dict(candidate=candidate,budget=budget,cohort=setup,rows=ordered,corrections=rule.counts,
                summary={k:interval([r[k] for r in ordered]) for k in ordered[0] if k!='event'},seconds=time.monotonic()-start)
    write_once(dest,result);print(json.dumps(dict(cell=dest.name,clears=result['summary']['completions']['mean'],seconds=result['seconds'])),flush=True)
    return result


def run(study,out):
    torch.set_num_threads(2)
    if out.exists():raise FileExistsError(out)
    selected=read(study/'selection.json')['winner'];all_candidates=read(study/'training-selection.json')
    selected=next(c for c in all_candidates if c['name']==selected)
    parent=next(c for c in all_candidates if c['name']=='parent')
    candidates=[]
    for name,source,variant in [('parent',parent,'base')]+[(f'new-{v}',selected,v) for v in VARIANTS]:
        candidates.append(dict(name=name,checkpoint=str((study/source['checkpoint']).resolve()),sha256=source['sha256'],variant=variant))
    audit=audit_seeds([2026101551,2026101552]);out.mkdir(parents=True)
    for name in ('sfld_rl.dll','synthetic.profile'):copy(study/name,out/name)
    sources=[Path(__file__),ROOT/'tools/audit_interactions.py',ROOT/'tools/train_rl.py',ROOT/'tools/benchmark_player_ideas.py',ROOT/'tools/publish_effect_study.py']
    sources+=sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    plan=dict(schema='sfld-interaction-runtime-v1',engine='0.6.11',budgets=BUDGETS,candidates=candidates,
        upstream_selection=read(study/'selection.json'),upstream_final_used=False,
        selection=dict(seed=2026101551,namespace=206,events=512),final=dict(seed=2026101552,namespace=207,events=4096),
        sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        library_sha256=train_rl.sha(out/'sfld_rl.dll'),profile_sha256=train_rl.sha(out/'synthetic.profile'),
        selection_rule='Equal mean completions over six budgets; parent first on ties. Freeze winner before final.',
        promotion_gate='Confirm trained base versus parent and selected guard versus base and parent, if a guard wins selection. '
                       'Twenty-one Bonferroni contrasts. Each gate needs positive equal-budget lower bound and no individual negative upper bound. '
                       'Promote guard only if it passes both comparisons; otherwise promote base if its parent comparison passes. '
                       'This fallback is declared before final data. If parent wins selection, retain parent.',
        limits='Core checkpoint selected upstream before its final test. Local action probes informed guard candidates only. '
               'This independent selection/final study includes the exact current desktop corrections for every candidate. '
               'Intervals exclude uncertainty in real game mechanics.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for p in sources:copy(p,out/'source'/p.relative_to(ROOT))
    status(out,'selection')
    cells={(c['name'],b):evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    scores={c['name']:mean(cells[c['name'],b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates}
    selection=dict(winner=max(scores,key=scores.get),scores=scores);write_once(out/'selection.json',selection)
    winner=next(c for c in candidates if c['name']==selection['winner'])
    status(out,'final',selection=selection)
    base=next(c for c in candidates if c['name']=='new-base')
    selected=[candidates[0]] if winner==candidates[0] else [candidates[0],base]+([] if winner==base else [winner])
    final={(c['name'],b):evaluate(out,plan,c,'final',b) for c in selected for b in BUDGETS}
    contrasts={};gates={}
    if winner!=candidates[0]:
        z=NormalDist().inv_cdf(1-.01/(2*21))
        pairs=[('parent','new-base')]+([] if winner==base else [('parent',winner['name']),('new-base',winner['name'])])
        for old_name,new_name in pairs:
            rows=[];deltas=[];name=new_name+' minus '+old_name
            for b in BUDGETS:
                old=final[old_name,b]['rows'];new=final[new_name,b]['rows']
                if [r['event'] for r in old]!=[r['event'] for r in new]:raise RuntimeError('Unmatched event indices')
                delta=[y['completions']-x['completions'] for x,y in zip(old,new)];deltas.append(delta)
                rows.append(dict(budget=b,**stats(delta,z)))
            rows.append(dict(budget='equal-budget mean',**stats([mean(x) for x in zip(*deltas)],z)))
            contrasts[name]=rows
            gates[name]=rows[-1]['family_99_ci'][0]>0 and all(c['family_99_ci'][1]>=0 for c in rows[:-1])
    deploy='parent'
    if gates.get('new-base minus parent'):deploy='new-base'
    if winner['name'] not in ('parent','new-base') and gates.get(winner['name']+' minus parent') and gates.get(winner['name']+' minus new-base'):
        deploy=winner['name']
    eligible=deploy!='parent'
    for path,sha in plan['sources'].items():
        if train_rl.sha(ROOT/path)!=sha:raise RuntimeError('Frozen source changed: '+path)
    result=dict(selection=selection,contrasts=contrasts,gates=gates,deploy_candidate=deploy,promotion_eligible=eligible,
                final=[{k:v for k,v in c.items() if k!='rows'} for c in final.values()])
    write_once(out/'summary.json',result);status(out,'complete',promotion_eligible=eligible,selection=selection)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.study.resolve(),a.output.resolve())
