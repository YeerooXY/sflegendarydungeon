"""Verify and archive the declared multiseed comparison, including negative results."""
import argparse
import json
from pathlib import Path
import shutil
from statistics import mean,NormalDist
from publish_effect_study import sha,stats


def read(path):return json.loads(path.read_text(encoding='utf8'))


def verify_cell(cell,plan,candidate,cohort,budget,profile_sha):
    ident=cell['identity'];rows=cell['rows']
    if ident['cohort']!=plan[cohort] or ident['candidate']!=candidate or ident['budget']!=budget:raise ValueError('Evaluation identity changed')
    if ident['checkpoint_sha256']!=candidate['sha256'] or ident['profile_sha256']!=profile_sha or ident['library_sha256']!=plan['library_sha256']:raise ValueError('Evaluation input changed')
    if [r['event'] for r in rows]!=list(range(plan[cohort]['events'])):raise ValueError('Incomplete event cohort')
    if any(r['invalid_actions'] or r['mushrooms']>budget or r['mushrooms']!=r['recovery_mushrooms']+r['reroll_mushrooms'] or not 167.999<r['elapsed_hours']<=168.000001 for r in rows):raise ValueError('Invalid accounting')
    if mean(r['completions'] for r in rows)!=cell['summary']['completions']['mean']:raise ValueError('Completion summary changed')


def publish(source,target,report):
    plan=read(source/'plan.json');result=read(source/'summary.json');candidates=read(source/'training-selection.json')
    if plan['smoke']:raise ValueError('Smoke tests are not research evidence')
    if target.exists() or report.exists():raise FileExistsError('Archive already exists')
    for p,h in plan['sources'].items():
        if sha(source/'source'/p)!=h:raise ValueError('Frozen source changed: '+p)
    for family,h in plan['parents'].items():
        if sha(source/f'original-{family}.pt')!=h:raise ValueError('Parent changed')
    for name,key in [('synthetic.profile','profile_sha256'),('sfld_rl.dll','library_sha256')]:
        if sha(source/name)!=plan[key]:raise ValueError('Study input changed')
    for c in candidates:
        if sha(source/c['checkpoint'])!=c['sha256']:raise ValueError('Candidate changed')
    selection={};cells={};training=[]
    for b in plan['branches']:
        summary=read(source/b['name']/'training-summary.json');state=summary['state']
        if state['steps']!=b['steps'] or read(source/(b['name']+'-status.json'))['exit_code']:raise ValueError('Training incomplete')
        training.append({'name':b['name'],'steps':state['steps'],'weeks':state['completed_training_events'],
                         'seconds':summary['wall_seconds_this_invocation']})
    for c in candidates:
        for b in plan['budgets']:
            cell=read(source/f'selection-{c["name"]}-{b}.json');verify_cell(cell,plan,c,'selection',b,plan['profile_sha256']);cells['selection',c['name'],b]=cell
    for family in plan['parents']:
        scores={c['name']:mean(cells['selection',c['name'],b]['summary']['completions']['mean'] for b in plan['budgets']) for c in candidates if c['family']==family}
        selection[family]={'winner':max(scores,key=scores.get),'scores':scores}
    if selection!=result['selection']:raise ValueError('Selection was not honored')
    if (source/'raw-frozen-selection.json').exists():
        early=read(source/'raw-frozen-selection.json')
        if {k:early[k] for k in ('winner','scores')}!=selection['raw']:
            raise ValueError('Raw selection changed after its final evaluation began')
    selected=[];contrasts={};promotion={};z=NormalDist().inv_cdf(1-.01/(2*14))
    for family in plan['parents']:
        parent=next(c for c in candidates if c['name']=='parent-'+family)
        winner=next(c for c in candidates if c['name']==selection[family]['winner'])
        arms=[parent] if parent==winner else [parent,winner];selected.extend(arms)
        for c in arms:
            for b in plan['budgets']:
                cell=read(source/f'final-{c["name"]}-{b}.json');verify_cell(cell,plan,c,'final',b,plan['profile_sha256']);cells['final',c['name'],b]=cell
        if parent==winner:promotion[family]=False;continue
        deltas=[];rows=[]
        for b in plan['budgets']:
            delta=[n['completions']-o['completions'] for o,n in zip(cells['final',parent['name'],b]['rows'],cells['final',winner['name'],b]['rows'])]
            deltas.append(delta);rows.append({'budget':b,**stats(delta,z)})
        rows.append({'budget':'equal-budget mean',**stats([mean(x) for x in zip(*deltas)],z)})
        contrasts[family]=rows;promotion[family]=rows[-1]['family_99_ci'][0]>0 and all(r['family_99_ci'][1]>=0 for r in rows[:-1])
    if contrasts!=result['contrasts'] or promotion!=result['promotion']:raise ValueError('Final result changed')
    stress=[]
    for scenario in plan['sensitivity_profiles']:
        folder=source/scenario
        for c in selected:
            for b in plan['budgets']:
                cell=read(folder/f'sensitivity-{c["name"]}-{b}.json');verify_cell(cell,plan,c,'sensitivity',b,sha(folder/'synthetic.profile'));cells[scenario,c['name'],b]=cell
        for family in plan['parents']:
            parent='parent-'+family;winner=selection[family]['winner']
            before=mean(cells[scenario,parent,b]['summary']['completions']['mean'] for b in plan['budgets'])
            after=mean(cells[scenario,winner,b]['summary']['completions']['mean'] for b in plan['budgets'])
            stress.append({'scenario':scenario,'family':family,'parent':before,'selected':after,'difference':after-before})
    # Preserve source/inputs, full row cohorts, curves and each selected checkpoint.
    target.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file():shutil.copyfile(path,target/path.name)
    shutil.copytree(source/'source',target/'source')
    for c in candidates:
        dest=target/c['checkpoint'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/c['checkpoint'],dest)
    for b in plan['branches']:
        dest=target/b['name'];dest.mkdir(exist_ok=True)
        for p in (source/b['name']).iterdir():
            if p.suffix in ('.json','.jsonl'):shutil.copyfile(p,dest/p.name)
    for scenario in plan['sensitivity_profiles']:
        dest=target/scenario;dest.mkdir()
        for p in (source/scenario).iterdir():
            if p.suffix in ('.json','.profile'):shutil.copyfile(p,dest/p.name)
    for name in ('publish_multiseed_study.py','publish_effect_study.py'):
        support=target/'source/tools'/name;support.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(__file__).with_name(name),support)
    (target/'.gitattributes').write_text('** -text\n')
    (target/'validation.json').write_text(json.dumps({'training':training,'selection_policy_weeks':sum(c['events'] for (phase,_,_),c in cells.items() if phase=='selection'),
        'final_policy_weeks':sum(c['events'] for (phase,_,_),c in cells.items() if phase=='final'),
        'all_cohorts_accounted':True,'stress_summary':stress},indent=2)+'\n')
    lines=['# Further learning with independent continuations, 2026-10-12','',
        f'Four continuations added **{sum(t["steps"] for t in training):,} decisions** and completed **{sum(t["weeks"] for t in training):,} training weeks**. Two fresh training seeds continue each current parent. The unrestricted policy learns all legal choices; the shop policy retains five rolling refresh credits and the four-key gate. Random eligible stone menus and the 168-hour completion-only objective remain unchanged.','',
        f'Each candidate is selected internally on validation, then fresh screening chooses one per family. The untouched final comparison has **{plan["final"]["events"]:,} events per budget per evaluated policy**. A parent that wins screening still receives a fresh final estimate.','',
        '| Mushrooms/week | Previous unrestricted | Selected unrestricted | Previous shop policy | Selected shop policy |',
        '| ---: | ---: | ---: | ---: | ---: |']
    for b in plan['budgets']:
        names=['parent-raw',selection['raw']['winner'],'parent-shop',selection['shop']['winner']]
        vals=[cells['final',n,b]['summary']['completions']['mean'] for n in names]
        lines.append('| '+str(b)+' | '+' | '.join(f'{v:.4f}' for v in vals)+' |')
    lines+=['','| Policy family | Selected candidate | Overall difference | Family-adjusted 99% interval | Statistical gate |','| --- | --- | ---: | --- | --- |']
    for family in plan['parents']:
        if family not in contrasts:lines.append(f'| {family} | {selection[family]["winner"]} | 0 | Parent retained | No replacement |');continue
        row=contrasts[family][-1];lo,hi=row['family_99_ci']
        lines.append(f'| {family} | {selection[family]["winner"]} | {row["mean"]:+.5f} | [{lo:+.5f}, {hi:+.5f}] | {"Pass" if promotion[family] else "Fail"} |')
    lines+=['','The family adjustment covers fourteen declared contrasts. Event indices are paired within each budget and clustered across budgets. A statistical gate is separate from UI acceptance and deployment. The two continuations share a parent; they do not represent independent training from scratch.','',
        '| Stress scenario | Family | Parent equal-budget mean | Selected mean | Difference |','| --- | --- | ---: | ---: | ---: |']
    for s in stress:lines.append(f'| {s["scenario"]} | {s["family"]} | {s["parent"]:.4f} | {s["selected"]:.4f} | {s["difference"]:+.4f} |')
    if (source/'deployment-rule.json').exists():
        lines+=['','The user selected unrestricted optimization within the event budget before screening or final results were available. The default app therefore uses the unrestricted challenger only if it passes its declared final gate; otherwise it retains the unrestricted parent. The constrained family remains a comparison. See the [recorded deployment rule]('+target.name+'/deployment-rule.json).']
    lines+=['','Stress profiles vary barrel blessings (35%/65%), non-shop strong effects (15%/35%) and combat curses (15%/5%). They are hypothetical robustness checks, not estimates or confidence bounds for the live game. The selected checkpoint stays fixed; stress results do not choose another model. Their point estimates alone do not prove a separate advantage.','',
        'These are simulator comparisons. Unknown live mechanics, effect timing, incomplete live state projection and version changes still require observations. More training cannot establish a globally optimal live-game strategy from an uncertain model. The defensible claim is the best independently tested policy among the declared candidates under these assumptions.','',
        f'[Plan]({target.name}/plan.json), [selection]({target.name}/selection.json), [full results]({target.name}/summary.json), [validated accounting]({target.name}/validation.json), [source and data checksums]({target.name}/manifest.json).','']
    report.write_text('\n'.join(lines),encoding='utf8')
    (target/'manifest.json').write_text(json.dumps({'files':{p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}},indent=2)+'\n')
    print(json.dumps({'report':str(report),'promotion':promotion,'training_decisions':sum(t['steps'] for t in training)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();publish(a.source,a.target,a.report)
