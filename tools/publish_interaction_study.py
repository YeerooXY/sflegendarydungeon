"""Verify and archive interaction training, diagnostics and runtime comparison."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
from statistics import mean,NormalDist
from train_rl import sha
from publish_effect_study import stats


def read(p):return json.loads(p.read_text(encoding='utf-8'))


def check_rows(cell,events,budget):
    rows=cell['rows']
    if [r['event'] for r in rows]!=list(range(events)):raise ValueError('Incomplete/duplicate event cohort')
    if any(r['invalid_actions'] or r['mushrooms']>budget or
           r['mushrooms']!=r['recovery_mushrooms']+r['reroll_mushrooms'] or
           not 168-4/3600-1e-8<=r['elapsed_hours']<=168.000001 for r in rows):
        raise ValueError('Invalid event accounting')
    if mean(r['completions'] for r in rows)!=cell['summary']['completions']['mean']:raise ValueError('Altered mean')


def contrast(cells,old,new,budgets,z):
    result=[];ds=[]
    for b in budgets:
        d=[n['completions']-o['completions'] for o,n in zip(cells[old,b]['rows'],cells[new,b]['rows'])]
        ds.append(d);result.append(dict(budget=b,**stats(d,z)))
    result.append(dict(budget='equal-budget mean',**stats([mean(x) for x in zip(*ds)],z)))
    return result


def gate(rows):return rows[-1]['family_99_ci'][0]>0 and all(r['family_99_ci'][1]>=0 for r in rows[:-1])


def publish(source,target,runtime=False):
    if target.exists():raise FileExistsError(target)
    plan=read(source/'plan.json');summary=read(source/'summary.json')
    if plan.get('smoke') or read(source/'status.json')['phase']!='complete':raise ValueError('Need a completed full study')
    for name,digest in plan['sources'].items():
        if sha(source/'source'/name)!=digest:raise ValueError('Source snapshot changed: '+name)
    for name,key in [('synthetic.profile','profile_sha256'),('sfld_rl.dll','library_sha256')]:
        if sha(source/name)!=plan[key]:raise ValueError('Study input changed')
    candidates=plan['candidates'] if runtime else read(source/'training-selection.json')
    for c in candidates:
        if sha(Path(c['checkpoint']) if runtime else source/c['checkpoint'])!=c['sha256']:raise ValueError('Candidate changed')
    selection={};final={};selection_count=final_count=0
    for p in sorted(source.glob('*.json')):
        if not p.name.startswith(('selection-','final-')):continue
        cell=read(p)
        if runtime:
            c=cell['candidate'];budget=cell['budget'];cohort=cell['cohort']
        else:
            identity=cell['identity'];c=identity['candidate'];budget=identity['budget'];cohort=identity['cohort']
            if identity['library_sha256']!=plan['library_sha256'] or identity['profile_sha256']!=plan['profile_sha256']:
                raise ValueError('Cell inputs changed')
        group='selection' if p.name.startswith('selection-') else 'final'
        if c not in candidates or cohort!=plan[group]:raise ValueError('Cell identity changed')
        check_rows(cell,cohort['events'],budget)
        if group=='selection':selection[c['name'],budget]=cell;selection_count+=len(cell['rows'])
        else:final[c['name'],budget]=cell;final_count+=len(cell['rows'])
    scores={c['name']:mean(selection[c['name'],b]['summary']['completions']['mean'] for b in plan['budgets']) for c in candidates}
    chosen=dict(winner=max(scores,key=scores.get),scores=scores)
    if chosen!=summary['selection'] or chosen!=read(source/'selection.json'):raise ValueError('Selection mismatch')
    winner=chosen['winner'];training={}
    if runtime:
        calculated={};gates={};deploy='parent'
        if winner!='parent':
            pairs=[('parent','new-base')]+([] if winner=='new-base' else [('parent',winner),('new-base',winner)])
            for old,new in pairs:
                name=new+' minus '+old
                calculated[name]=contrast(final,old,new,plan['budgets'],NormalDist().inv_cdf(1-.01/(2*21)))
                gates[name]=gate(calculated[name])
            if gates['new-base minus parent']:deploy='new-base'
            if winner!='new-base' and gates[winner+' minus parent'] and gates[winner+' minus new-base']:deploy=winner
        if calculated!=summary['contrasts'] or gates!=summary['gates'] or deploy!=summary['deploy_candidate']:
            raise ValueError('Runtime promotion comparison mismatch')
        eligible=deploy!='parent'
    else:
        if sha(source/'challenges.json')!=plan['challenges_sha256']:raise ValueError('Curriculum changed')
        for b in plan['branches']:
            s=read(source/b['name']/'training-summary.json')['state'];training[b['name']]=s
            if s['steps']!=b['steps'] or read(source/(b['name']+'-status.json'))['exit_code']:raise ValueError('Incomplete training')
            if b['curriculum'] and s['completed_partial_scenarios']+s['completed_whole_events']!=s['completed_training_events']:
                raise ValueError('Partial-scenario accounting mismatch')
        if training!=summary['training']:raise ValueError('Training totals changed')
        calculated=[] if winner=='parent' else contrast(final,'parent',winner,plan['budgets'],NormalDist().inv_cdf(1-.01/(2*7)))
        if calculated!=summary['contrasts']:raise ValueError('Core comparison mismatch')
        eligible=bool(calculated) and gate(calculated)
    if eligible!=summary['promotion_eligible']:raise ValueError('Eligibility differs')
    target.mkdir(parents=True)
    for p in source.iterdir():
        if not p.is_file():continue
        if p.name.startswith(('selection-','final-')) and p.suffix=='.json':
            with gzip.open(target/(p.name+'.gz'),'wb') as stream:stream.write(p.read_bytes())
        elif p.suffix in ('.json','.profile','.dll','.md'):shutil.copyfile(p,target/p.name)
    shutil.copytree(source/'source',target/'source')
    if not runtime:
        if sha(source/'original-raw.pt')!=plan['parent_sha256']:raise ValueError('Original parent changed')
        shutil.copyfile(source/'original-raw.pt',target/'original-raw.pt')
    checkpoint_files={}
    for c in candidates:
        original=Path(c['checkpoint']) if runtime else source/c['checkpoint']
        relative=Path('checkpoints')/(c['name']+'.pt') if runtime else Path(c['checkpoint'])
        if c['sha256'] in checkpoint_files:continue
        (target/relative).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target/relative)
        checkpoint_files[c['sha256']]=relative.as_posix()
    if not runtime:
        for b in plan['branches']:
            for p in (source/b['name']).iterdir():
                if p.suffix in ('.json','.jsonl'):shutil.copyfile(p,target/b['name']/p.name)
    validation=dict(accounting_verified=True,promotion_eligible=eligible,selection_policy_weeks=selection_count,
                    final_policy_weeks=final_count,training=training,checkpoint_files=checkpoint_files,publisher_sha256=sha(__file__))
    (target/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    (target/'.gitattributes').write_text('** -text\n')
    (target/'manifest.json').write_text(json.dumps(dict(files={p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}),indent=2)+'\n')
    print(json.dumps(dict(target=str(target),selection_policy_weeks=selection_count,final_policy_weeks=final_count,promotion_eligible=eligible)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--runtime',action='store_true')
    a=p.parse_args();publish(a.source,a.target,a.runtime)
