"""Validate and archive the corrected-engine training study and held-out results."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import statistics
from statistics import NormalDist

ROOT=Path(__file__).resolve().parents[1]
LABELS={'parent-raw':'Previous unrestricted','trained-raw':'Further-trained unrestricted',
        'parent-browser':'Previous browser','parent-mask':'Untrained shop-mask control',
        'trained-shop':'Further-trained shop policy'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def stats(values,z):
    mean=statistics.mean(values);se=statistics.stdev(values)/math.sqrt(len(values))
    return {'mean':mean,'standard_error':se,'paired_99_ci':[mean-2.5758293035489*se,mean+2.5758293035489*se],
            'family_99_ci':[mean-z*se,mean+z*se]}

def publish(source,target,report):
    result=read(source/'summary.json');plan=result['plan']
    if plan['smoke']:raise ValueError('A smoke test is not a training result')
    if target.exists() or report.exists():raise FileExistsError('Choose a fresh report/archive destination')
    for path,fingerprint in plan['sources'].items():
        if sha(source/'source'/path)!=fingerprint:raise ValueError('Source snapshot hash differs: '+path)
    for file,key in [('sfld_rl.dll','library_sha256'),('synthetic.profile','profile_sha256'),('original-checkpoint.pt','parent_sha256')]:
        if sha(source/file)!=plan[key]:raise ValueError('Binary/profile/parent identity differs')
    cells={};rows=[];training={}
    for branch in plan['branches']:
        training[branch['name']]=read(source/branch['name']/'training-summary.json')
        if training[branch['name']]['state']['steps']!=plan['steps_per_branch']:raise ValueError('Training incomplete')
    for c in result['candidates']:
        if sha(source/c['checkpoint'])!=c['sha256']:raise ValueError('Checkpoint changed after selection')
        for b in plan['budgets']:
            cell=read(source/f"final-{c['name']}-{b}.json");identity=cell['identity']
            if (identity['cohort']!=plan['final'] or identity['candidate']!=c or identity['budget']!=b or
                identity['checkpoint_sha256']!=c['sha256'] or identity['profile_sha256']!=plan['profile_sha256'] or
                identity['library_sha256']!=plan['library_sha256']):raise ValueError('Final cohort differs from frozen plan')
            if [r['event'] for r in cell['rows']]!=list(range(plan['final']['events'])):raise ValueError('Incomplete final cohort')
            for r in cell['rows']:
                if (r['budget']!=b or r['candidate']!=c['name'] or r['invalid_actions'] or r['mushrooms']>b or
                    r['mushrooms']!=r['recovery_mushrooms']+r['reroll_mushrooms'] or
                    not 0<r['actions']<100000 or not 167.999<=r['elapsed_hours']<=168.00000001 or
                    any(not math.isfinite(v) for v in r.values() if isinstance(v,(int,float)))):
                    raise ValueError('Invalid final event')
            cells[c['name'],b]=cell;rows.extend(cell['rows'])
    pairs=[('trained-raw','parent-raw'),('trained-shop','parent-browser'),
           ('trained-shop','parent-mask'),('parent-mask','parent-browser')]
    count=len(pairs)*(len(plan['budgets'])+1)
    z=NormalDist().inv_cdf(1-.01/(2*count));comparisons=[]
    for new,old in pairs:
        differences=[]
        for b in plan['budgets']:
            a=cells[old,b]['rows'];c=cells[new,b]['rows']
            delta=[y['completions']-x['completions'] for x,y in zip(a,c)]
            differences.append(delta)
            comparisons.append({'new':new,'old':old,'budget':b,**stats(delta,z)})
        # Treat the shared event index across budgets as a cluster.
        pooled=[statistics.mean(v) for v in zip(*differences)]
        comparisons.append({'new':new,'old':old,'budget':'equal-budget mean',**stats(pooled,z)})
    if read(source/'selection.json')!=result['selection']:
        raise ValueError('Final summary differs from the frozen selection')
    selected=result['selection']['practical']['overall']
    contrast=next((c for c in comparisons if c['new']==selected and
                   c['old']=='parent-browser' and c['budget']=='equal-budget mean'),None)
    deployment={'selected_on_validation':selected,
                'promote':contrast is not None and contrast['family_99_ci'][0]>0,
                'comparison':contrast,'rule':read(source/'deployment-rule.json')}
    target.mkdir(parents=True)
    # Archived hashes identify exact bytes, including original line endings.
    (target/'.gitattributes').write_text('** -text\n',encoding='utf-8')
    for name in ('plan.json','selection.json','training-selection.json','parent-checkpoint.transfer.json','seed-audit.json','deployment-rule.json',
                 'sfld_rl.dll','synthetic.profile','original-checkpoint.pt'):
        shutil.copyfile(source/name,target/name)
    for c in result['candidates']:
        dest=target/c['checkpoint'];dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copyfile(source/c['checkpoint'],dest)
    for branch in plan['branches']:
        dest=target/branch['name'];dest.mkdir(exist_ok=True)
        for name in ('curve.jsonl','run.json','best.json','training-summary.json'):
            shutil.copyfile(source/branch['name']/name,dest/name)
        shutil.copyfile(source/(branch['name']+'-invocation.json'),target/(branch['name']+'-invocation.json'))
    shutil.copytree(source/'source',target/'source')
    shutil.copyfile(__file__,target/'source/tools/publish_current_study.py')
    behavior=[]
    for name in ('parent-raw','trained-raw'):
        file=source/f'behavior-{name}.json'
        if file.exists():
            data=read(file)
            if data['accounting']['illegal_actions'] or data['accounting']['truncations'] or data['teacher_comparison']:
                raise ValueError('Invalid diagnostic cohort')
            (target/(file.name+'.gz')).write_bytes(gzip.compress(file.read_bytes(),mtime=0))
            for row in data['summaries']:
                behavior.append({'candidate':name,'budget':row['budget'],'events':row['events'],
                    'rerolls_per_week':row['actions'].get('reroll',0)/row['events'],
                    'full_refills_per_week':row['actions'].get('heal_full',0)/row['events'],
                    'partial_refills_per_week':row['actions'].get('heal_step',0)/row['events']})
    if behavior:
        write(target/'behavior-summary.json',behavior)
        shutil.copyfile(ROOT/'tools/audit_rl_policy.py',target/'source/tools/audit_rl_policy.py')
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (target/'events.csv.gz').write_bytes(gzip.compress(buffer.getvalue().encode(),mtime=0))
    validation=[read(p) for p in sorted(source.glob('selection-*.json'))]
    write(target/'validation.json',validation)
    result.update(comparisons=comparisons,training=training,behavior=behavior,deployment=deployment,
        inference={'family_confidence':.99,'completion_contrasts':count,'method':'Paired normal intervals with Bonferroni family correction; equal-budget estimates cluster shared event indices.'})
    write(target/'summary.json',result)
    decisions=sum(v['state']['steps'] for v in training.values())
    weeks=sum(v['state']['completed_training_events'] for v in training.values())
    table=['| Weekly mushrooms | Previous unrestricted | Trained unrestricted | Previous browser | Untrained shop mask | Trained shop policy |',
           '|---:|---:|---:|---:|---:|---:|']
    for b in plan['budgets']:
        names=['parent-raw','trained-raw','parent-browser','parent-mask','trained-shop']
        table.append('| '+str(b)+' | '+' | '.join(f"{cells[n,b]['summary']['completions']['mean']:.4f}" for n in names)+' |')
    intervals=['| Comparison | Change in equally weighted weekly clears | Family 99% interval |','|---|---:|---|']
    for c in comparisons:
        if c['budget']=='equal-budget mean':
            lo,hi=c['family_99_ci']
            intervals.append(f"| {LABELS[c['new']]} minus {LABELS[c['old']]} | {c['mean']:+.4f} | {lo:+.4f} to {hi:+.4f} |")
    spent=['| Cap | Trained shop: refresh mushrooms/week | Recovery mushrooms/week | Total mushrooms per clear |','|---:|---:|---:|---:|']
    for b in plan['budgets']:
        s=cells['trained-shop',b]['summary']
        spent.append(f"| {b} | {s['reroll_mushrooms']['mean']:.2f} | {s['recovery_mushrooms']['mean']:.2f} | {s['mushrooms']['mean']/s['completions']['mean']:.2f} |")
    picks=['| Branch | Additional decisions used | Completed training weeks | Selected extra decisions |','|---|---:|---:|---:|']
    for b in result['branches']:
        state=training[b['name']]['state'];picks.append(f"| {b['name']} | {state['steps']:,} | {state['completed_training_events']:,} | {b['steps']:,} |")
    archive=target.name
    deployment_text=(f"The practical candidate selected before final evaluation was **{LABELS[selected]}**. "
        + ('It passes the predeclared promotion gate: the lower family-adjusted 99% bound '
           'on the equal-budget improvement over the previous browser is positive.'
           if deployment['promote'] else
           'It does not pass the predeclared promotion gate; retain the previous browser policy.'))
    text=f'''# Further learning on the corrected dungeon, 2026-10-11

This study adds **{decisions:,} PPO decisions**, completing **{weeks:,} training
weeks** across two independent branches. A decision is one action, not a dungeon
clear or a whole event. Both start from the same previous checkpoint and learn
on engine 0.6.9/profile v4, including the corrected first-run stone pool,
trap-free opening, first-run containers, damage modifiers and effect timing.

## Untouched final evaluation

Each cell has {plan['final']['events']:,} seven-day events. The five candidates,
including the untrained shop-mask control, total **{len(rows):,} final weeks**.
All candidates use matching event indices and seed roots within each budget.

'''+ '\n'.join(table)+'\n\n'+ '\n'.join(intervals)+'\n\n'+deployment_text+f'''

Intervals describe simulation sampling error. They do not include uncertainty
in live dungeon mechanics or variation between training seeds. There is one
training seed per branch. The archive includes every budget-specific contrast
and the untrained mask control, which separates the change in shop action
selection from subsequent learning.

## What was trained

The unrestricted branch learns every legal stone, door, shop, combat and recovery
decision. The shop branch has five rolling refresh credits per paid shop, a
four-key gate, and stops for a useful One Hit, Recovery, Escape or strong Lockpick
offer. Unused credits carry forward. When no refresh is available, the remaining
legal purchases, healing and skipping stay learned. The older browser instead
turned an unavailable proposed reroll into Skip; the mask-only parent measures
that difference without adding training.

The shop constraint is part of the action distribution during both sampling and
PPO updates. Actions are not replaced after sampling while retaining an incorrect
PPO likelihood. Stones retain random eligible menus and learned preferences;
there is no forced Pendant ranking or key-hoarding reward.

Both branches receive only completion increments as rewards, with discount one.
GAE lambda is 0.99, learning rate 0.0001, entropy coefficient 0.005 and target KL
0.02. Training uses 512 environments, 128-step rollouts, four PPO epochs and
2,048-item minibatches. Adam moments and weights are inherited exactly through
an explicitly recorded environment transfer; no training is attributed to that
transfer. Ordinary resume compatibility checks remain strict.

'''+ '\n'.join(picks)+'''

All evaluated policies retain the safe soldier-search rule. Practical policies
also retain the browser's safe final-boss wait after two partial refills when
time permits, and avoid waiting above the final boss's damage ceiling. Those
recovery rules are identical across the practical comparison arms. F2P and paid
budgets remain separate; the F2P row is a research control, not a new viewer default.

## Spending

'''+ '\n'.join(spent)+f'''

## Selection and reproducibility

A separate diagnostic cohort describes the unrestricted policies' actions at
100/500/1,000 mushrooms, with 128 complete weeks per cap. It is not used for
checkpoint selection or performance claims. Its [action summary]({archive}/behavior-summary.json)
and compressed detailed counts distinguish refreshes from full and partial
refills. Differences describe visited trajectories, not causal action values.

Internal validation uses 256 events per budget at step zero and every 4,194,304
decisions. The earlier checkpoint remains eligible. A separate {plan['selection']['events']:,}-event
cohort per budget selects the overall and budget-specific candidates before
the final cohort runs; exact ties keep the earlier candidate. Final scores do
not change that frozen selection. Historical published averages are retained.

The [selection]({archive}/selection.json), [frozen plan]({archive}/plan.json),
[paired results]({archive}/summary.json), [compressed event rows]({archive}/events.csv.gz),
selected checkpoints, native library, profile and exact source snapshots are
archived with hashes. Zero illegal actions and zero safety truncations are
required; all budgets and recovery/refresh totals are checked before publication.

Run `out/rl-env/Scripts/python.exe tools/train_current_study.py --output out/NEW_STUDY`
to reproduce the declared experiment. Existing destinations are immutable.
The model remains an approximation to the live game; these results do not
establish a guaranteed optimal strategy.
'''
    report.write_text(text,encoding='utf-8')
    write(target/'manifest.json',{str(p.relative_to(target).as_posix()):sha(p) for p in sorted(target.rglob('*')) if p.is_file()})
    print(json.dumps({'additional_decisions':decisions,'training_weeks':weeks,'final_weeks':len(rows),'report':str(report)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'out/rl-current-2026-10-11')
    parser.add_argument('--target',type=Path,default=ROOT/'docs/rl-current-2026-10-11')
    parser.add_argument('--report',type=Path,default=ROOT/'docs/RL_CURRENT_2026_10_11.md')
    args=parser.parse_args();publish(args.source,args.target,args.report)
