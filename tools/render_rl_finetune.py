"""Render a standalone fine-tuning study after validating its paired evidence.

No Torch, NumPy, network, or web server is needed. Training and selection
artifacts remain unchanged; every final estimate is rebuilt from indexed events.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics

from render_rl_study import Artifacts, agrees, fingerprint, integer, number, paired_results, require, validate_evaluation


def inside(study, value):
    path = Path(str(value).replace('\\', '/'))
    path = (path if path.is_absolute() else study / path).resolve()
    require(path.is_relative_to(study), 'Artifact path escapes the study folder')
    return path


def cohort(groups, plan, protocol, namespace, namespace_byte):
    require({group['budget'] for group in groups.values()} == set(plan['budgets']), 'Evaluation budgets differ from study plan')
    require(len(groups) == len(plan['budgets']), 'Expected exactly one profile per declared budget')
    for group in groups.values():
        signature = group['signature']
        require(signature['root_seed'] == int(protocol['seed']) and group['events'] == protocol['events'], 'Evaluation seed or event count differs from study plan')
        require(signature['namespace'] == namespace and signature['namespace_byte'] == namespace_byte, 'Wrong evaluation namespace')
        require(signature['library_sha256'] == plan['library_sha256'] and signature['profile_sha256'] == plan['profile_sha256'], 'Evaluation library/profile differs from study plan')
        require(signature['deadline_hours'] == 168., 'Fine-tuning report requires seven-day events')


def first_stage(study, plan, artifacts):
    common = artifacts.read(study / 'common-validation.json')
    require(common['schema'] == 'sfld-ppo-finetune-selection-v1', 'Unknown first-stage selection schema')
    names, signatures, candidates = set(), None, common['candidates']
    require(candidates and candidates[0]['checkpoint_sha256'] == plan['parent_checkpoint_sha256'], 'First-stage parent must precede variants')
    for candidate in candidates:
        require(candidate['name'] not in names, 'Duplicate first-stage candidate')
        names.add(candidate['name'])
        artifacts.verify(inside(study, candidate['checkpoint']), candidate['checkpoint_sha256'])
        evaluation = artifacts.read(inside(study, candidate['evaluation_file']))
        groups = validate_evaluation(evaluation)
        cohort(groups, plan, plan['common_validation'], 'validation', 0xB2)
        current = {key: group['signature'] for key, group in groups.items()}
        require(signatures is None or signatures == current, 'First-stage candidates use different cohorts')
        signatures = current
        require(evaluation['checkpoint_sha256'] == candidate['checkpoint_sha256'] and evaluation['action_policy'] == 'greedy', 'First-stage policy differs from its declaration')
        agrees(candidate['selection_score'], evaluation['selection_score'], 'First-stage selection score')
    winner = max(candidates, key=lambda item: item['selection_score'])
    require(common['selected']['name'] == winner['name'] and common['selected']['checkpoint_sha256'] == winner['checkpoint_sha256'], 'Extension parent is not the first-stage winner')
    agrees(common['selected']['selection_score'], winner['selection_score'], 'First-stage selected score')
    return common


def training_runs(study, plan, totals, artifacts, common):
    jobs = totals['jobs']
    require(jobs and len({job['name'] for job in jobs}) == len(jobs), 'Missing or repeated training jobs')
    variants = {entry['name']: entry for entry in plan['variants']}
    require(len(variants) == len(plan['variants']), 'Duplicate planned variant')
    require(set(variants).issubset({job['name'] for job in jobs}), 'Planned variant missing from training totals')
    require(len(jobs) == len(variants) + 1, 'Expected every variant and one extension job')
    result = []
    for job in jobs:
        name = job['name']
        require(isinstance(name, str) and Path(name).name == name and name not in ('', '.', '..'), 'Invalid training job name')
        folder = inside(study, name)
        curve = artifacts.read(folder / 'curve.jsonl', jsonl=True)
        summary = artifacts.read(folder / 'training-summary.json')
        manifest = artifacts.read(folder / 'run.json')
        state = summary['state']
        points, previous_steps, events = [], 0, 0
        for row in curve:
            require(row['phase'] in ('ppo', 'initial_validation'), 'Unexpected phase in fine-tuning curve')
            steps = integer(row['steps'], 'curve steps')
            if row['phase'] == 'ppo':
                require(steps > previous_steps, 'Repeated or reordered PPO steps')
                previous_steps = steps
                events += integer(row['completed_events'], 'completed training events')
            else:
                require(steps == 0 and previous_steps == 0, 'Initial validation must precede PPO')
            summaries = row.get('validation_summaries', [])
            if summaries:
                require({s['budget'] for s in summaries} == set(plan['budgets']) and len(summaries) == len(plan['budgets']), 'Curve validation budgets differ')
                for entry in summaries:
                    require(all(integer(entry[key], key) == 0 for key in ('failed_events', 'invalid_actions', 'truncations')), 'Failed validation appears in training curve')
                    stats = entry['completions']
                    mean = number(stats['mean'], 'curve validation mean')
                    require(mean >= 0 and number(stats['standard_error'], 'curve standard error') >= 0, 'Invalid curve estimate')
                    require(integer(entry['events'], 'curve validation events', 2) >= 2, 'Too few validation events')
                    points.append({'steps': steps, 'budget': entry['budget'], 'mean': mean})
                agrees(row['validation_score'], statistics.mean(s['completions']['mean'] for s in summaries), 'Curve validation score')
        planned = variants.get(name, plan['extension'])
        steps = integer(job['steps'], 'job steps')
        require(steps == previous_steps == state['steps'] == planned['steps'], 'Training steps disagree with curve, summary or plan')
        require(events == job['completed_training_events'] == state['completed_training_events'], 'Training event counts disagree')
        require(state.get('bc_steps', 0) == 0, 'Additional behavior cloning must be reported separately')
        require(int(manifest['metadata']['seed']) == int(planned['seed']), 'Training job seed differs from plan')
        parent_hash = plan['parent_checkpoint_sha256'] if name in variants else common['selected']['checkpoint_sha256']
        require(manifest['metadata']['finetune']['parent_sha256'] == parent_hash, 'Training job inherited the wrong checkpoint')
        env = manifest['metadata']['environment']
        require(env['library_sha256'] == plan['library_sha256'] and env['deadline_hours'] == 168., 'Training library or horizon differs')
        require(len(env['profiles']) == 1 and env['profiles'][0]['sha256'] == plan['profile_sha256'], 'Training profile differs')
        require(set(manifest['metadata']['budgets']) == set(plan['budgets']), 'Training budgets differ')
        require(not list(folder.glob('failure-*.json')), 'A training job has a recorded failure')
        wall = number(job['wall_seconds'], 'job wall seconds')
        require(wall >= 0, 'Training time must be nonnegative')
        actual_config = {**planned, **{key: manifest.get('ppo_config', {}).get(key, planned.get(key))
                                     for key in ('learning_rate', 'entropy_coefficient', 'gae_lambda', 'target_kl')}}
        actual_config['rollout_steps'] = manifest['metadata'].get('rollout_steps', planned.get('rollout_steps'))
        for key in ('learning_rate', 'entropy_coefficient', 'gae_lambda', 'target_kl', 'rollout_steps'):
            if key in planned:
                agrees(actual_config[key], planned[key], 'Training config ' + key)
        result.append({'name': name, 'steps': steps, 'completed_training_events': events,
                       'wall_seconds': wall, 'config': actual_config, 'points': points})
    require(integer(totals['new_ppo_steps'], 'total new PPO steps') == sum(job['steps'] for job in result), 'Total PPO decisions disagree with jobs')
    require(integer(totals['completed_training_events'], 'total training events') == sum(job['completed_training_events'] for job in result), 'Total training events disagree with jobs')
    require(number(totals['wall_seconds'], 'total wall seconds') >= 0, 'Total wall time must be nonnegative')
    return result


def collect(study, output):
    study = Path(study).resolve()
    artifacts = Artifacts(output)
    plan = artifacts.read(study / 'study-plan.json')
    require(plan['schema'] == 'sfld-ppo-finetune-plan-v1', 'Unknown fine-tuning study schema')
    budgets = plan['budgets']
    require(budgets and len(set(budgets)) == len(budgets), 'Budgets must be unique')
    for budget in budgets:
        integer(budget, 'budget')
    for key in ('parent_checkpoint_sha256', 'library_sha256', 'profile_sha256'):
        fingerprint(plan[key], key)
    for key in ('common_validation', 'selection_validation', 'final'):
        integer(plan[key]['events'], key + ' events', 2)
        seed = int(plan[key]['seed'])
        require(0 <= seed < 2**64, 'Invalid study root seed')
    require(len({int(plan[key]['seed']) for key in ('common_validation', 'selection_validation', 'final')}) == 3,
            'Selection and final cohorts require distinct declared root seeds')
    common = first_stage(study, plan, artifacts)
    selection = artifacts.read(study / 'selection.json')
    require(selection.get('schema') == 'sfld-ppo-finetune-selection-v1', 'Unknown fine-tuning selection schema')
    require(isinstance(selection['selection_rule'], str) and selection['selection_rule'], 'Missing declared selection rule')
    candidates, signatures = [], None
    for candidate in selection['candidates']:
        require(candidate['name'] not in {item['name'] for item in candidates}, 'Repeated selection candidate')
        checkpoint = inside(study, candidate['checkpoint'])
        artifacts.verify(checkpoint, candidate['checkpoint_sha256'])
        evaluation = artifacts.read(inside(study, candidate['evaluation_file']))
        groups = validate_evaluation(evaluation)
        cohort(groups, plan, plan['selection_validation'], 'validation', 0xB2)
        current = {key: value['signature'] for key, value in groups.items()}
        require(signatures is None or signatures == current, 'Selection candidates use different cohorts')
        signatures = current
        require(evaluation['action_policy'] == 'greedy', 'Selection must evaluate greedy neural policies')
        require(evaluation['checkpoint_sha256'] == candidate['checkpoint_sha256'], 'Selection evaluation checkpoint differs')
        agrees(candidate['selection_score'], evaluation['selection_score'], 'Candidate selection score')
        candidates.append({**candidate, 'summaries': evaluation['summaries']})
    require(candidates, 'Selection needs candidates')
    require(candidates[0]['checkpoint_sha256'] == plan['parent_checkpoint_sha256'], 'Parent must be first for exact selection ties')
    winner = max(candidates, key=lambda item: item['selection_score'])
    selected = selection['selected']
    require(selected['name'] == winner['name'] and selected['checkpoint_sha256'] == winner['checkpoint_sha256'], 'Selected policy does not follow the declared mean/first-tie rule')
    agrees(selected['selection_score'], winner['selection_score'], 'Selected score')
    artifacts.verify(inside(study, selected['checkpoint']), selected['checkpoint_sha256'])
    artifacts.verify(study / 'parent-checkpoint.pt', plan['parent_checkpoint_sha256'])
    final = artifacts.read(study / 'final-learner.json')
    parent = artifacts.read(study / 'final-parent.json')
    for report, expected in ((final, selected['checkpoint_sha256']), (parent, plan['parent_checkpoint_sha256'])):
        require(report['checkpoint_sha256'] == expected, 'Final policy hash differs from frozen selection')
        require(report['action_policy'] == 'greedy', 'Final comparison must use greedy neural policies')
        for key in ('library_sha256', 'profile_sha256'):
            require(report[key] == plan[key], 'Final declared environment differs from plan')
        groups = validate_evaluation(report)
        cohort(groups, plan, plan['final'], 'final', 0xC3)
        for key, group in groups.items():
            for field in ('schema_sha256', 'max_actions', 'deadline_hours', 'library_sha256', 'profile_sha256'):
                require(group['signature'][field] == signatures[key][field], 'Final environment differs from selection: ' + field)
    pairs = paired_results(final, parent)
    final_protocol = selection['final_protocol']
    require(int(final_protocol['seed']) == int(plan['final']['seed']) and final_protocol['events'] == plan['final']['events'], 'Frozen final protocol differs from plan')
    totals = artifacts.read(study / 'training-totals.json')
    jobs = training_runs(study, plan, totals, artifacts, common)
    for filename in ('orchestrator-revision.json', 'verification.json', 'finetune-verification.json',
                     'independent-final-audit.json', 'archived-evaluation.json'):
        if (study / filename).is_file():
            artifacts.read(study / filename)
    if (study / 'sfld_rl.dll').is_file():
        artifacts.verify(study / 'sfld_rl.dll', plan['library_sha256'])
    if (study / 'source-manifest.json').is_file():
        manifest = artifacts.read(study / 'source-manifest.json')
        for filename, expected in manifest.items():
            artifacts.read(inside(study, 'source/' + filename.replace('\\', '/')), expected=expected, plain_text=True)
    source = study / 'source'
    if source.is_dir():
        for path in sorted(source.rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.cpp', '.hpp', '.h', '.txt'):
                artifacts.read(path, plain_text=True)
    revisions = study / 'orchestrator-revisions'
    if revisions.is_dir():
        for path in sorted(revisions.rglob('*.py')):
            artifacts.read(path, plain_text=True)
    return {'schema': 'sfld-ppo-finetune-report-v1', 'plan': plan, 'common_validation': common, 'selection': {**selection, 'candidates': candidates},
            'pairs': pairs, 'training_totals': totals, 'jobs': jobs, 'artifacts': list(artifacts.rows.values()),
            'interpretation': 'Pointwise paired sampling intervals compare frozen policies in this simulator; they exclude uncertain mechanics and do not establish an optimal live-game strategy.'}


def build_report(study, output_html):
    """Public entry point shared by CLI and the copy-and-verify publisher."""
    return collect(study, output_html)


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legendary Dungeon · Further learning</title>
<style>
:root{color-scheme:dark;--bg:#10171f;--panel:#182430;--line:#314555;--text:#e7eef2;--muted:#aec0cc;--good:#69d6b1;--old:#caa6ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:32px 20px 64px}h1{font-size:clamp(1.8rem,4vw,2.8rem);margin:0}h2{font-size:1.35rem;margin:0 0 12px}p{margin:10px 0}.muted,small{color:var(--muted)}section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px;margin:20px 0}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{border:1px solid var(--line);border-radius:8px;padding:15px}.card strong{display:block;font-size:1.7rem}select,button{font:inherit;background:var(--bg);color:var(--text);padding:7px 12px;border:1px solid var(--line);border-radius:6px}label{display:inline-flex;gap:10px;align-items:center}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{padding:11px;text-align:right;border-bottom:1px solid var(--line)}th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:500}.positive{color:var(--good)}.negative{color:#ffb5a4}svg{display:block;width:100%;height:auto}a{color:#9dcdff}.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.9rem}.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px}details{margin:12px 0}summary{cursor:pointer}code{font-size:.85rem;overflow-wrap:anywhere}.links{padding-left:20px}.links li{margin:8px 0}#curve-empty{display:none}.foot{font-size:.9rem}@media(max-width:650px){.cards{grid-template-columns:1fr}main{padding:24px 12px}section{padding:16px}th,td{padding:8px}}
</style>
<main><h1>What did further learning improve?</h1>
<p class="muted">Legendary Dungeon simulator · Frozen baseline against the selected continuation</p>
<p id="headline"></p><div class="cards" id="cards"></div>
<section><h2>Completions over seven days</h2><p>Both policies play the same indexed event seeds. The difference is <strong>selected policy minus baseline</strong>; its interval uses each paired event.</p>
<div class="table-wrap"><table><thead><tr><th>Mushroom cap</th><th>Baseline</th><th>Selected</th><th>Difference</th><th>Paired 95% interval</th><th>Events each</th></tr></thead><tbody id="comparison"></tbody></table></div>
<svg id="differences" viewBox="0 0 1000 300" role="img" aria-label="Paired completion differences and 95 percent intervals"></svg>
<p class="muted foot">These are pointwise sampling intervals, without a multiple-comparison adjustment. They do not include uncertainty in the dungeon mechanics. A small improvement here is not a guarantee in the live game.</p></section>
<section><h2>Where the mushrooms went</h2><div class="table-wrap"><table><thead><tr><th>Cap</th><th>Baseline total</th><th>Selected total</th><th>Baseline heals / rerolls</th><th>Selected heals / rerolls</th></tr></thead><tbody id="spending"></tbody></table></div><p class="muted foot">Mean gross spending per event. Found mushrooms do not extend the spending cap in this model.</p></section>
<section><h2>Learning curves</h2><label>Show budget <select id="budget"><option value="all">All budgets, equal mean</option></select></label><svg id="curves" viewBox="0 0 1000 370" role="img" aria-label="Validation completion means versus additional PPO decisions"></svg><p id="curve-empty">No validation points are available.</p><div id="legend" class="legend"></div><p class="muted foot">Each line starts at the beginning of that training job. These validation cohorts chose checkpoints; they are not the independent final results above.</p></section>
<section><h2>How the policy was selected</h2><p id="selection"></p><div class="table-wrap"><table><thead><tr><th>Candidate</th><th>Selection mean</th><th>Chosen</th></tr></thead><tbody id="candidates"></tbody></table></div><details><summary>Training settings and complete effort</summary><div class="table-wrap"><table><thead><tr><th>Job</th><th>New PPO decisions</th><th>Completed events</th><th>Learning rate</th><th>Entropy</th><th>GAE λ</th><th>Rollout length</th></tr></thead><tbody id="training"></tbody></table></div><p class="muted foot">Totals include every branch and the extension, even when an earlier checkpoint won. Prior training inherited from the baseline is excluded from these additional-work totals.</p></details></section>
<section><h2>Evidence and limits</h2><p>Rewards count completed runs only. Arena legendaries, ordinary items and gold are not separate optimization targets. The policy sees revealed state and legal actions; it does not receive future outcomes or RNG state.</p><p>The learned behavior depends on the configured door, damage, blessing and shop model. This comparison establishes performance for these frozen policies under that model; it does not establish a globally optimal strategy.</p><p id="cohort" class="muted"></p><button id="download">Download report data</button><details><summary>Verified artifacts and SHA256 fingerprints</summary><ul class="links" id="artifacts"></ul></details></section>
</main><script id="report-data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('report-data').textContent),$=id=>document.getElementById(id),fmt=(x,n=3)=>Number(x).toLocaleString('en-US',{minimumFractionDigits:n,maximumFractionDigits:n}),int=x=>Number(x).toLocaleString('en-US'),svgNS='http://www.w3.org/2000/svg';
function row(body,values){const tr=document.createElement('tr');values.forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});$(body).append(tr);return tr}
const improved=D.pairs.filter(p=>p.paired_difference.normal_95_ci[0]>0).length,worse=D.pairs.filter(p=>p.paired_difference.normal_95_ci[1]<0).length;
$('headline').textContent=`The selected policy was ${D.selection.selected.name}. Its paired interval is above zero at ${improved} of ${D.pairs.length} budgets and below zero at ${worse}. The remaining comparisons include zero.`;
[[int(D.training_totals.new_ppo_steps),'additional PPO decisions'],[int(D.training_totals.completed_training_events),'completed training events'],[int(D.plan.final.events),'fresh final events per policy and cap']].forEach(([value,label])=>{const card=document.createElement('div');card.className='card';const strong=document.createElement('strong');strong.textContent=value;card.append(strong,document.createTextNode(label));$('cards').append(card)});
D.pairs.forEach(p=>{const delta=p.paired_difference,ci=delta.normal_95_ci,tr=row('comparison',[int(p.budget),fmt(p.teacher.mean),fmt(p.learner.mean),(delta.mean>0?'+':'')+fmt(delta.mean),`[${fmt(ci[0])}, ${fmt(ci[1])}]`,int(p.events)]);tr.children[3].className=ci[0]>0?'positive':ci[1]<0?'negative':'';const a=p.teacher_spending,b=p.learner_spending;row('spending',[int(p.budget),fmt(a.mushrooms,2),fmt(b.mushrooms,2),`${fmt(a.recovery_mushrooms,2)} / ${fmt(a.reroll_mushrooms,2)}`,`${fmt(b.recovery_mushrooms,2)} / ${fmt(b.reroll_mushrooms,2)}`])});
function el(parent,tag,attrs={},text=null){const node=document.createElementNS(svgNS,tag);Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));if(text!==null)node.textContent=text;parent.append(node);return node}
function deltaChart(){const svg=$('differences'),lo=Math.min(0,...D.pairs.map(p=>p.paired_difference.normal_95_ci[0])),hi=Math.max(0,...D.pairs.map(p=>p.paired_difference.normal_95_ci[1])),pad=Math.max(.005,(hi-lo)*.12),x=v=>150+(v-lo+pad)/(hi-lo+2*pad)*790;svg.setAttribute('viewBox',`0 0 1000 ${D.pairs.length*44+70}`);el(svg,'line',{x1:x(0),x2:x(0),y1:20,y2:D.pairs.length*44+12,stroke:'#aec0cc','stroke-dasharray':'4 5'});D.pairs.forEach((p,i)=>{const y=40+i*44,c=p.paired_difference.normal_95_ci,color=c[0]>0?'#69d6b1':c[1]<0?'#ffb5a4':'#caa6ff';el(svg,'text',{x:8,y:y+5,fill:'#e7eef2','font-size':16},`${p.budget} mushrooms`);el(svg,'line',{x1:x(c[0]),x2:x(c[1]),y1:y,y2:y,stroke:color,'stroke-width':4});el(svg,'circle',{cx:x(p.paired_difference.mean),cy:y,r:6,fill:color});});for(let i=0;i<=4;i++){const value=lo-pad+(hi-lo+2*pad)*i/4;el(svg,'text',{x:x(value),y:D.pairs.length*44+47,fill:'#aec0cc','font-size':14,'text-anchor':'middle'},fmt(value))}}
deltaChart();D.plan.budgets.forEach(b=>{const option=document.createElement('option');option.value=b;option.textContent=`${b} mushrooms`;$('budget').append(option)});
const colors=['#69d6b1','#caa6ff','#f2c16a','#79baff','#f69daa'];
function curvePoints(job,budget){if(budget!=='all')return job.points.filter(p=>p.budget===Number(budget));const grouped=new Map();job.points.forEach(p=>{if(!grouped.has(p.steps))grouped.set(p.steps,[]);grouped.get(p.steps).push(p.mean)});return [...grouped].map(([steps,values])=>({steps,mean:values.reduce((a,b)=>a+b,0)/values.length}));}
function curves(){const svg=$('curves');svg.replaceChildren();$('legend').replaceChildren();const series=D.jobs.map(j=>({...j,points:curvePoints(j,$('budget').value)})),points=series.flatMap(s=>s.points);$('curve-empty').style.display=points.length?'none':'block';if(!points.length)return;const maxX=Math.max(1,...points.map(p=>p.steps)),minY=Math.max(0,Math.min(...points.map(p=>p.mean))-.1),maxY=Math.max(minY+.1,...points.map(p=>p.mean))+.1,x=v=>70+v/maxX*880,y=v=>300-(v-minY)/(maxY-minY)*265;for(let i=0;i<=4;i++){const v=minY+(maxY-minY)*i/4;el(svg,'line',{x1:70,x2:950,y1:y(v),y2:y(v),stroke:'#314555'});el(svg,'text',{x:58,y:y(v)+5,fill:'#aec0cc','font-size':14,'text-anchor':'end'},fmt(v,2));const step=maxX*i/4;el(svg,'text',{x:x(step),y:327,fill:'#aec0cc','font-size':14,'text-anchor':'middle'},`${fmt(step/1e6,1)}m`)}el(svg,'text',{x:510,y:358,fill:'#aec0cc','font-size':15,'text-anchor':'middle'},'Additional PPO decisions in this job');series.forEach((s,i)=>{const color=colors[i%colors.length];el(svg,'polyline',{points:s.points.map(p=>`${x(p.steps)},${y(p.mean)}`).join(' '),fill:'none',stroke:color,'stroke-width':2.5});const item=document.createElement('span'),swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=color;item.append(swatch,document.createTextNode(s.name));$('legend').append(item)})}
$('budget').addEventListener('change',curves);curves();
$('selection').textContent=`The branches were compared on ${int(D.plan.common_validation.events)} events per cap. The final candidates then shared a separate ${int(D.plan.selection_validation.events)}-event cohort. Highest equal mean across caps won; exact ties retained the first-listed baseline. The selected checkpoint was frozen before the ${int(D.plan.final.events)}-event final comparison.`;
D.selection.candidates.forEach(c=>row('candidates',[c.name,fmt(c.selection_score),c.name===D.selection.selected.name?'Yes':'']));D.jobs.forEach(j=>row('training',[j.name,int(j.steps),int(j.completed_training_events),j.config.learning_rate??'Inherited',j.config.entropy_coefficient??'Inherited',j.config.gae_lambda??'Inherited',j.config.rollout_steps??'Inherited']));
$('cohort').textContent=`Final root seed ${D.plan.final.seed}; namespace 0xC3. Native library ${D.plan.library_sha256.slice(0,12)}…; profile ${D.plan.profile_sha256.slice(0,12)}…. All reported events passed terminal, spending and action checks.`;
D.artifacts.forEach(a=>{const li=document.createElement('li'),link=document.createElement('a'),code=document.createElement('code');link.href=a.href;link.textContent=a.href.split('/').slice(-3).join('/');code.textContent=a.sha256;li.append(link,document.createElement('br'),code);$('artifacts').append(li)});
$('download').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(D,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='finetune-report-data.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
</script></html>'''


def render(data):
    encoded = json.dumps(data, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return HTML.replace('__DATA__', encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    data = build_report(args.study, args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'paired_budgets': len(data['pairs']), 'new_ppo_steps': data['training_totals']['new_ppo_steps']}))


if __name__ == '__main__':
    main()
