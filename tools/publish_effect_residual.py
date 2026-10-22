"""Freeze effect policy data, model and independent weekly comparison."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
from statistics import mean, NormalDist
import numpy as np
from benchmark_player_ideas import write_once
from publish_effect_study import sha, stats

ROOT = Path(__file__).resolve().parents[1]


def read(path): return json.loads(path.read_text(encoding='utf8'))


def publish(source, target, report):
    plan, result, fit = [read(source/name) for name in ('plan.json', 'summary.json', 'fit.json')]
    if plan['smoke']: raise ValueError('A smoke test is not research evidence')
    for path, digest in plan['sources'].items():
        if sha(source/'source'/path) != digest: raise ValueError('Frozen source changed: '+path)
    for name, key in (('sfld_rl.dll','library_sha256'), ('synthetic.profile','profile_sha256'), ('original-checkpoint.pt','parent_sha256')):
        if sha(source/name) != plan[key]: raise ValueError('Study input changed: '+name)
    counts = []; cases_count = 0
    refined = plan['schema'] == 'sfld-effect-boundary-study-v1'
    folders = [(source, plan)]
    if refined:
        folders.append((source/'base', read(source/'base/plan.json')))
        for name, digest in plan['base_files'].items():
            if sha(source/'base'/name) != digest: raise ValueError('Reused input changed')
    for folder, data_plan in folders:
        for budget in data_plan['training_budgets']:
            metadata = read(folder/f'labels-{budget}.json')
            if sha(folder/f'labels-{budget}.npz') != metadata['data_sha256']: raise ValueError('Training labels changed')
            with np.load(folder/f'labels-{budget}.npz') as arrays:
                if not np.isfinite(arrays['outcomes']).all(): raise ValueError('Missing outcomes')
                if arrays['outcomes'].shape != (metadata['cases'], data_plan['rollouts']['pairs_per_state'], 2, 4): raise ValueError('Outcome shape differs')
            counts.append(metadata['completed_partial_weeks']); cases_count += metadata['cases']
    if sum(counts) != fit['partial_weeks'] or cases_count != fit['states']: raise ValueError('Training counts differ')
    names = ['parent'] + [c['name'] for c in plan['candidates']]
    scores = {name: mean(read(source/f'selection-{name}-{b}.json')['summary']['completions']['mean'] for b in plan['budgets']) for name in names}
    winner = max(scores, key=scores.get)
    if result['selection'] != {'winner': winner, 'scores': scores}: raise ValueError('Selection was not honored')
    cells = {}; contrasts = []
    if winner != 'parent':
        deltas = []; z = NormalDist().inv_cdf(1-.01/(2*7))
        for name in ('parent', winner):
            for budget in plan['budgets']:
                cell = read(source/f'final-{name}-{budget}.json'); rows = cell['rows']
                if cell['identity']['cohort'] != plan['final']: raise ValueError('Final cohort differs')
                if name != 'parent' and cell['identity']['candidate']['effect_artifact_sha256'] != sha(source/(name+'.json')): raise ValueError('Final candidate changed')
                if [r['event'] for r in rows] != list(range(plan['final']['events'])): raise ValueError('Incomplete final event cohort')
                if any(r['invalid_actions'] or r['mushrooms'] > budget or r['mushrooms'] != r['recovery_mushrooms']+r['reroll_mushrooms'] or not 167.999 < r['elapsed_hours'] <= 168.000001 for r in rows): raise ValueError('Invalid final accounting')
                cells[name,budget] = cell
        for budget in plan['budgets']:
            delta = [b['completions']-a['completions'] for a,b in zip(cells['parent',budget]['rows'],cells[winner,budget]['rows'])]
            deltas.append(delta); contrasts.append({'budget': budget, **stats(delta,z)})
        contrasts.append({'budget': 'equal-budget mean', **stats([mean(row) for row in zip(*deltas)],z)})
        if contrasts != result['contrasts']: raise ValueError('Final contrasts changed')
        passed = contrasts[-1]['family_99_ci'][0]>0 and all(c['family_99_ci'][1]>=0 for c in contrasts[:-1])
        if passed != result['promotion']: raise ValueError('Gate differs')
    if target.exists() or report.exists(): raise FileExistsError('Archive already exists')
    target.mkdir(parents=True)
    # Preserve all inputs, paired labels and rows; compression is in the data
    # formats themselves, so reproduction needs no proprietary model service.
    for path in source.iterdir():
        if path.is_file(): shutil.copyfile(path, target/path.name)
    shutil.copytree(source/'source', target/'source')
    if refined: shutil.copytree(source/'base', target/'base')
    for path in (Path(__file__), ROOT/'tools/audit_effect_residual.py', ROOT/'tests/test_effect_residual.py',
                 ROOT/'CMakeLists.txt', ROOT/'tools/requirements-rl.txt', ROOT/'tools/requirements-rl-distill.txt'):
        destination = target/'source'/path.relative_to(ROOT); destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    (target/'.gitattributes').write_text('** -text\n', encoding='utf8')
    lines = ['# Learning blessing preservation and curse replacement', '',
        'The existing neural adviser remains the base. A small supervised action-value model can override it only for optional containers/barrels with valuable stacked blessings, cursed chests with three curses, and blessing/cursed doors beside a visible monster. Healing, shops, stones and unrelated doors retain their previous policy. Before room 25, the residual never prefers a trapped special door over a safe visible monster.', '',
        f'Training used {fit["states"]:,} visible positions and {fit["partial_weeks"]:,} paired-branch remaining-week simulations ({fit["partial_weeks"]//2:,} paired futures). Each trial forces one action and then follows the frozen parent until the 168-hour deadline. The deployment prediction is the difference in completed runs; gold, damage and mushrooms spent are not substituted for that objective.', '',
        'The state bank combines reservoirs from 1,024 naturally played parent weeks with deliberately difficult positions. These partial rollouts are not additional fresh full weeks and their frequency is artificial. The two actions receive the same fresh future seed; no hidden RNG state is supplied to the policy. Twenty percent of position/event groups are held out when fitting; equivalent engineered positions across budgets remain in the same split.', '',
        f'Independent selection used {plan["selection"]["events"]:,} full weeks per budget for each of five candidates. It selected `{winner}` before any final seeds were used. '+(f'The final comparison uses {plan["final"]["events"]:,} matched full weeks per budget per policy.' if cells else 'No final comparison was run because the parent won selection.'), '',
        '| Selection candidate | Equal-budget mean clears/week |', '| --- | ---: |']
    lines.extend(f'| {name} | {score:.5f} |' for name, score in scores.items())
    if cells:
        lines += ['', '| Weekly mushroom budget | Previous adviser | Selected adviser | Difference |',
                  '| ---: | ---: | ---: | ---: |']
        for budget in plan['budgets']:
            old = cells['parent',budget]['summary']['completions']['mean']
            new = cells[winner,budget]['summary']['completions']['mean']
            lines.append(f'| {budget} | {old:.4f} | {new:.4f} | {new-old:+.4f} |')
        pooled = contrasts[-1]; lo,hi = pooled['family_99_ci']
        lines += ['', f'The equally weighted budget difference is **{pooled["mean"]:+.5f} clears/week**, with a family-adjusted 99% interval **[{lo:+.5f}, {hi:+.5f}]**. Seven declared contrasts are covered; event indices are paired and clustered across budgets.', '',
                  ('The selected model passes the statistical promotion gate. Browser acceptance and deployment are recorded separately in `deployment.json`.' if result['promotion'] else 'The candidate does not pass the declared promotion gate. The previous adviser remains the default.')]
    else:
        closest = max((name for name in names if name != 'parent'), key=scores.get)
        lines += ['', 'The parent won selection. No final comparison seeds were consumed and no residual was deployed.', '',
                  f'Screening means below compare the parent with `{closest}`, the closest challenger. These screening outcomes selected the winner; they are not an independent final confirmation.', '',
                  '| Weekly mushroom budget | Previous adviser | Closest challenger |', '| ---: | ---: | ---: |']
        for budget in plan['budgets']:
            old = read(source/f'selection-parent-{budget}.json')['summary']['completions']['mean']
            new = read(source/f'selection-{closest}-{budget}.json')['summary']['completions']['mean']
            lines.append(f'| {budget} | {old:.4f} | {new:.4f} |')
    if refined:
        lines += ['', f'This follow-up reuses {fit["reused_partial_weeks"]:,} first-study continuations and adds {fit["new_partial_weeks"]:,} new ones. Those reused simulations must not be counted as independent new evidence. Extra visible-state features summarize each effect and the longest/shortest remaining duration. Trees jointly fit completion difference and negative next-clear-time difference divided by 24; the latter guides splits, while only the completion prediction chooses an action. The room-66 boundary variants remain held out. Selection uses fresh whole-week seeds; a separate final cohort is reserved for a challenger that wins selection.']
    diagnostic = source/'effect-decisions.json'
    if diagnostic.exists():
        audit = read(diagnostic)
        audited_name = audit['identity']['candidate']
        lines += ['', '## Specific decisions', '',
                  f'The diagnostic audit below uses `{audited_name}`. '+('It remains an experimental candidate; the existing adviser is unchanged. ' if winner == 'parent' else '')+
                  'These are diagnostic states, not rules that apply at every health level or budget.', '',
                  '| Position, room 60 / free play | Parent | Learned choice |', '| --- | --- | --- |']
        wanted = ['recovery_oldest-crate-greed','recovery_oldest-blessing_door-hero',
                  'one_hit_oldest-blessing_door-hero','expiring-crate-greed',
                  'poison_oldest-cursed_chest-h50','short_curses-cursed_chest-h50']
        for name in wanted:
            row = next(x for x in audit['panel'] if x['budget']==0 and x['case']==name)
            lines.append(f'| {name} | {row["parent"]} | {row["chosen"]} |')
        lines += ['', '`door_0` in these panel rows acquires the blessing; `door_1` takes the ordinary monster. `interact` opens the container and `skip` leaves it.', '',
                  'The behavioral audit uses a separate cohort of 256 weeks per budget. It records changed actions and representative visible states, separately from the selection and final comparison.']
    lines += ['', '## Limits and reproduction', '',
        'Results are conditional on the current simulator. In particular, a newly acquired container blessing/curse starts next room. Replacing Recovery can therefore forfeit the current healing tick; replacing Poison can suppress the current poison tick. That exact live-game timing remains unverified. Duplicate effects replace in place, even if weaker; a fourth distinct effect evicts the oldest. The fitted policy may change when these rules or acquisition probabilities are corrected.', '',
        'This is a local action-value learning experiment, not a proof of a globally optimal strategy. A near tie in screening does not establish equivalence. The model uses no scikit-learn dependency at runtime; exported trees are checked against the fitted model on the same feature schema.', '',
        'Reproduce from the archived source, profile, native library, seed plan and paired-label arrays. Reusing any completed cohort reproduces that cohort; it is not new independent evidence.', '',
        f'[Declared plan]({target.name}/plan.json), [full results]({target.name}/summary.json), [training diagnostics]({target.name}/fit.json), [data and source checksums]({target.name}/manifest.json).', '']
    report.write_text('\n'.join(lines), encoding='utf8')
    write_once(target/'manifest.json', {'files': {p.relative_to(target).as_posix(): sha(p) for p in sorted(target.rglob('*')) if p.is_file()}})
    print(json.dumps({'report': str(report), 'winner': winner, 'promotion': result['promotion'], 'files': len(list(target.rglob('*')))}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--target', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args(); publish(a.source.resolve(), a.target.resolve(), a.report.resolve())
