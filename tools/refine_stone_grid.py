"""Resolve every integer HP/key cell for two screening-selected final menus."""
import argparse
import json
from pathlib import Path
from benchmark_stone_grid import pool_stage,ROOT
from benchmark_player_ideas import write_once
from train_continuation_study import audit_seeds,status
from train_current_study import copy
import train_rl


def run(parent,out):
    if out.exists():raise FileExistsError(out)
    source=json.loads((parent/'plan.json').read_text(encoding='utf-8'))
    selected=json.loads((parent/'selection.json').read_text(encoding='utf-8'))
    # Selection order was frozen from screening. Do not inspect confirmation.
    chosen=[next((r for r in selected if r['family']==family),None) for family in ('keys','health')]
    chosen=[r for r in chosen if r is not None]
    if not chosen:raise RuntimeError('No screening reversals to refine')
    unique={(r['budget'],r['menu_id']) for r in chosen}
    seed=20261015405;audit=audit_seeds([seed]);out.mkdir(parents=True)
    for name in ('checkpoint.pt','sfld_rl.dll','synthetic.profile'):copy(parent/name,out/name)
    plan=dict(source);plan.update(schema='sfld-final-stone-dense-v1',smoke=False,health=list(range(1,78)),
        keys=list(range(11)),dense=dict(seed=seed,trials=256),selection=chosen,
        parent_plan_sha256=train_rl.sha(parent/'plan.json'),parent_selection_sha256=train_rl.sha(parent/'selection.json'),
        selection_rule='Top key reversal and top HP reversal in frozen screening selection; confirmation is not consulted.',
        coverage='All integer HP1..77 and keys0..10 for the two selected menus only. All three arms, 256 fresh futures per cell. '
                 'Simultaneous confidence comparison across all dense-grid pairwise contrasts; unresolved cells remain unresolved.')
    plan['sources']={**source['sources'],Path(__file__).relative_to(ROOT).as_posix():train_rl.sha(Path(__file__))}
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for name in plan['sources']:copy(ROOT/name,out/'source'/name)
    states=[dict(hp=h,keys=k) for h in plan['health'] for k in plan['keys']]
    # Smaller shards let workers share a large menu while preserving paired seeds.
    tasks=[('dense',b,m,f'menu-{m}-part-{part}',states[start:start+64]) for b,m in sorted(unique)
           for part,start in enumerate(range(0,len(states),64))]
    records=pool_stage(out,plan,'dense',tasks)
    for name,digest in plan['sources'].items():
        if train_rl.sha(ROOT/name)!=digest:raise RuntimeError('Frozen source changed: '+name)
    write_once(out/'summary.json',dict(trajectories=sum(r['trajectories'] for r in records),
        actions=sum(r['actions'] for r in records),menus=[dict(budget=b,menu_id=m) for b,m in sorted(unique)],
        shards=records))
    status(out,'complete',trajectories=sum(r['trajectories'] for r in records))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('parent',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.parent.resolve(),a.output.resolve())
