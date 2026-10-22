"""Validate, summarize and archive effect-slot training and intervention probes."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
from statistics import NormalDist

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def stats(values,z):
    mean=statistics.mean(values);se=statistics.stdev(values)/math.sqrt(len(values))
    return {'mean':mean,'standard_error':se,'paired_99_ci':[mean-2.5758293035*se,mean+2.5758293035*se],
            'family_99_ci':[mean-z*se,mean+z*se]}


def analyze(source):
    result=read(source/'summary.json');plan=result['plan']
    if plan['smoke']:raise ValueError('Smoke tests are not study results')
    if plan.get('allow_seed_reuse'):raise ValueError('Reproduction reuses archived seeds; it is not an independent promotion study')
    for name,h in plan['sources'].items():
        if sha(source/'source'/name)!=h:raise ValueError('Source snapshot changed: '+name)
    for name,key in [('sfld_rl.dll','library_sha256'),('synthetic.profile','profile_sha256'),('original-checkpoint.pt','parent_sha256')]:
        if sha(source/name)!=plan[key]:raise ValueError('Study input changed: '+name)
    if read(source/'selection.json')!=result['selection']:raise ValueError('Selection changed after final test')
    training={b['name']:read(source/b['name']/'training-summary.json') for b in plan['branches']}
    for name,summary in training.items():
        state=summary['state']
        if state['steps']!=plan['steps_per_branch']:raise ValueError('Incomplete training')
        if name=='effect-curriculum' and state['completed_training_events']!=state['completed_partial_scenarios']+state['completed_whole_events']:
            raise ValueError('Partial/whole accounting mismatch')
    cells={};rows=[]
    for c in result['candidates']:
        if sha(source/c['checkpoint'])!=c['sha256']:raise ValueError('Checkpoint changed')
        for b in plan['budgets']:
            cell=read(source/f"final-{c['name']}-{b}.json");identity=cell['identity']
            if (identity['candidate']!=c or identity['cohort']!=plan['final'] or identity['budget']!=b or
                identity['checkpoint_sha256']!=c['sha256'] or identity['profile_sha256']!=plan['profile_sha256'] or identity['library_sha256']!=plan['library_sha256']):
                raise ValueError('Final inputs differ from frozen plan')
            if [r['event'] for r in cell['rows']]!=list(range(plan['final']['events'])):raise ValueError('Missing final events')
            for row in cell['rows']:
                if (row['invalid_actions'] or row['mushrooms']>b or row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or
                    not 167.999<=row['elapsed_hours']<=168.00000001 or not 0<row['actions']<100000):raise ValueError('Invalid final episode')
            cells[c['name'],b]=cell;rows+=cell['rows']
    z=NormalDist().inv_cdf(1-.01/(2*21));comparisons=[]
    for new,old in [('ordinary','parent'),('effect-curriculum','parent'),('effect-curriculum','ordinary')]:
        deltas=[]
        for b in plan['budgets']:
            values=[a['completions']-p['completions'] for a,p in zip(cells[new,b]['rows'],cells[old,b]['rows'])]
            deltas.append(values);comparisons.append({'new':new,'old':old,'budget':b,**stats(values,z)})
        comparisons.append({'new':new,'old':old,'budget':'equal-budget mean',**stats([statistics.mean(v) for v in zip(*deltas)],z)})
    winner=result['selection']['winner']
    contrast=next((c for c in comparisons if c['new']==winner and c['old']=='parent' and c['budget']=='equal-budget mean'),None)
    deployment={'selected_on_validation':winner,'promote':contrast is not None and contrast['family_99_ci'][0]>0,'contrast':contrast,'rule':plan['promotion_gate']}
    return result,training,cells,rows,comparisons,deployment


def publish(source,target,report):
    result,training,cells,rows,comparisons,deployment=analyze(source);plan=result['plan']
    if target.exists() or report.exists():raise FileExistsError('Archive/report already exist')
    probes=[read(source/f'probes-parent-{b}.json') for b in plan['probe']['budgets']]
    for b,p in zip(plan['probe']['budgets'],probes):
        if (p['identity']['setup']!=plan['probe'] or p['identity']['budget']!=b or
            p['identity']['checkpoint_sha256']!=sha(source/'parent-checkpoint.pt') or
            p['identity']['cases_sha256']!=sha(source/'probe-cases.json')):raise ValueError('Probe identity differs')
        if p['trajectories']!=len(p['summaries'])*2*plan['probe']['events']:raise ValueError('Missing probes')
        if sha(source/f'probes-parent-{b}.jsonl.gz')!=p['records_sha256']:raise ValueError('Probe rows changed')
    target.mkdir(parents=True);(target/'.gitattributes').write_text('** -text\n',encoding='utf-8')
    names=['plan.json','seed-audit.json','selection.json','training-selection.json','parent-checkpoint.transfer.json',
           'sfld_rl.dll','synthetic.profile','original-checkpoint.pt','challenges.json','probe-cases.json']
    names += [f'probes-parent-{b}{suffix}' for b in plan['probe']['budgets'] for suffix in ('.json','.jsonl.gz')]
    names += [f'{b["name"]}-invocation.json' for b in plan['branches']]
    for name in names:shutil.copyfile(source/name,target/name)
    for c in result['candidates']:
        dest=target/c['checkpoint'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/c['checkpoint'],dest)
    for branch in plan['branches']:
        for name in ('curve.jsonl','run.json','best.json','latest.json','training-summary.json'):
            shutil.copyfile(source/branch['name']/name,target/branch['name']/name)
        latest=read(source/branch['name']/'latest.json')
        endpoint=source/branch['name']/latest['checkpoint']
        if sha(endpoint)!=latest['sha256'] or latest['steps']!=plan['steps_per_branch']:raise ValueError('Endpoint checkpoint changed')
        shutil.copyfile(endpoint,target/branch['name']/latest['checkpoint'])
    shutil.copytree(source/'source',target/'source')
    for name in ('publish_effect_study.py','probe_effect_choices.py'):
        shutil.copyfile(ROOT/'tools'/name,target/'source/tools'/name)
    (target/'source/tests').mkdir(exist_ok=True)
    for name in ('test_effect_lab.py','test_rl_shop_policy.py'):
        shutil.copyfile(ROOT/'tests'/name,target/'source/tests'/name)
    # Keep selection summaries and all final event rows; don't mistake selection
    # data for independent tests when recreating the report.
    selection_cells=[{k:v for k,v in read(p).items() if k!='rows'} for p in sorted(source.glob('selection-*.json'))]
    write(target/'selection-cells.json',selection_cells)
    with gzip.open(target/'final-rows.jsonl.gz','wt',encoding='utf-8') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')
    summary={**result,'training':training,'comparisons':comparisons,'deployment':deployment}
    write(target/'summary.json',summary);write(target/'deployment.json',deployment)
    decisions=sum(v['state']['steps'] for v in training.values())
    partial=training['effect-curriculum']['state']['completed_partial_scenarios']
    whole=training['ordinary']['state']['completed_training_events']+training['effect-curriculum']['state']['completed_whole_events']
    lines=['# Effect-slot decisions: continued learning, 2026-10-11','',
        f'Trained **{decisions:,} additional PPO decisions**: equal-compute ordinary and effect-curriculum branches from the current shop-aware checkpoint. Training completed **{whole:,} full simulated weeks** and **{partial:,} partial challenge scenarios**. The latter are not full-week samples.','',
        f'Final evaluation contains **{len(rows):,} fresh simulated weeks** ({plan["final"]["events"]:,} matched events per policy and budget). Parent and both branch checkpoints were frozen before final evaluation.','',
        '## Seven-day completions','',
        '| Mushroom budget | Previous policy | Ordinary continuation | Effect curriculum |',
        '| ---: | ---: | ---: | ---: |']
    for b in plan['budgets']:
        means=[cells[n,b]['summary']['completions']['mean'] for n in ('parent','ordinary','effect-curriculum')]
        lines.append(f'| {b} | '+ ' | '.join(f'{x:.4f}' for x in means)+' |')
    lines+=['','Selected checkpoint steps: '+', '.join(f'{c["name"]}: {c["steps"]:,}' for c in result['candidates'])+'. A zero-step selection retains the initializer after later training failed to improve internal validation.','',
        'All policies retain random eligible stone menus, the five-credit shop constraint, safe soldier searches and the current final-boss wait. These are simulator averages, not measurements of live accounts.','',
        '## Independent comparison','',
        '| Comparison | Equal-budget change | Family-adjusted 99% interval |',
        '| --- | ---: | --- |']
    for c in comparisons:
        if c['budget']=='equal-budget mean':
            lo,hi=c['family_99_ci'];lines.append(f'| {c["new"]} minus {c["old"]} | {c["mean"]:+.4f} | [{lo:+.4f}, {hi:+.4f}] |')
    lines+=['',f'Independent selection chose **{deployment["selected_on_validation"]}**. '+('The selected checkpoint passes the promotion gate.' if deployment['promote'] else 'The promotion gate did not pass; retain the current browser policy.'),
        'Intervals account for the 21 declared weekly comparisons and cluster the same event index across budgets. One training seed per branch does not establish which training method is generally better.','',
        '## Opening versus leaving, from the same visible state','',
        f'Ran **{sum(p["trajectories"] for p in probes):,} counterfactual trajectories**: 36 declared positions, two actions, two budgets and {plan["probe"]["events"]:,} paired random futures. Force only the first action, then use the previous frozen policy until the next completion. A timeout is kept at 168 hours; it is not dropped.','',
        'All displayed positions begin at room 60, six keys and the named effects. The second stone is Rabbit. Blessings are One Hit (7 rooms), strong Recovery (5 rooms) and Raider (8 rooms), except the two-effect and expiring cases. "Oldest" and "newest" describe slot order. Curse cases contain three listed curses with long remaining durations unless marked short. Full progress strings and all outcomes are in the archive.','',
        'Negative time differences favor opening/entering the first option. These tests measure remaining clear time, not weekly completion gains or natural state frequencies. Intervals here adjust for 72 declared action contrasts.','',
        '| Position, no mushrooms | First choice | Other choice | Difference in remaining clear time | Family 99% interval | Previous policy |',
        '| --- | --- | --- | ---: | --- | --- |']
    for p in probes:
        if p['identity']['budget']!=0:continue
        for s in p['summaries']:
            d=s['first_minus_second_hours'];lo,hi=d['family_99_ci'];a,b=s['case']['actions']
            lines.append(f'| {s["case"]["name"]} | {a} | {b} | {d["mean"]:+.3f} h | [{lo:+.3f}, {hi:+.3f}] | {s["recommendation"]} |')
    lines+=['','## Interpretation and model limits','',
        '- The maintainer confirmed that duplicates replace in place even when weaker. Separate blessing and curse queues each hold three effects. A fourth distinct effect evicts the oldest in its own queue.',
        '- Full slots do not automatically mean skip: the value and remaining duration of the oldest effect matter. Even two slots are not risk-free because a duplicate may weaken a good effect.',
        '- Curse replacement is conditional. With three different curses and the current equal five-curse weights, one draw has a 40% chance to evict the oldest curse. After two draws it is absent with 56% probability: a later draw can reapply it. This ignores expiry, death and other transitions. These percentages come from placeholder identity weights, not live-game measurements.',
        '- New container effects currently activate on the next room. Replacing Poison can therefore suppress the current exit tick; replacing Recovery can suppress the current heal. Exact acquisition timing still needs footage validation and can materially affect these tactics.',
        '- Ordinary wood/bronze/silver container outcomes share a pooled VOD-based model. Stone and individual effect probabilities still contain assumptions. The curriculum changes training exposure, not those probabilities.',
        '- Shop buy/reroll constraints remain fixed. This experiment does not establish an optimal blessing-aware shop policy or a globally optimal strategy.','',
        '## Reproduce','',
        '```powershell',
        'cmake -S . -B build-perf',
        'cmake --build build-perf --config Release --target sfld_rl',
        'out/rl-env/Scripts/python.exe tools/train_effect_study.py --output out/effect-study-new --library build-perf/Release/sfld_rl.dll --allow-seed-reuse',
        'out/rl-env/Scripts/python.exe tools/probe_effect_choices.py --study out/effect-study-new --checkpoint out/effect-study-new/parent-checkpoint.pt --name parent',
        '```','',
        'These commands rerun the design with current code. Seed reuse is explicitly marked as reproduction and cannot support a new promotion claim. For historical code, use the archived source snapshots and pinned inputs; rebuilding or changing dependencies can change training arithmetic. Change and preregister the roots for a new independent final test. Native replay imports require a matching profile, budget and horizon and take a fresh future seed. Snapshots contain visible state only; they do not expose the hidden RNG.','',
        f'Archive: [{target.name}]({target.name}/summary.json). Previous study: [current-engine continuation](RL_CURRENT_2026_10_11.md).','']
    report.write_text('\n'.join(lines),encoding='utf-8')
    write(target/'manifest.json',{'schema':'sfld-effect-study-archive-v1','files':{
        p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}})
    print(json.dumps({'decisions':decisions,'full_training_weeks':whole,'partial_training_scenarios':partial,
                      'final_weeks':len(rows),'deployment':deployment,'report':str(report)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();publish(a.source.resolve(),a.target.resolve(),a.report.resolve())
