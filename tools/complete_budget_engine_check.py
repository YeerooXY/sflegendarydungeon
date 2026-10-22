"""Complete the frozen budget/Ultimate plans on engine 0.6.11.

Policies and entry allocations come from the independent 0.6.10 pilot. There is
no selection using newer-engine outcomes. Reuse the five predeclared check cells
and collect the remaining caps on the same fresh 1,024-event cohort.
"""
import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path
import shutil

import torch
import train_rl
import benchmark_budget_curve as curve
from benchmark_player_ideas import transfer_changes, write_once
from sfld_rl.env import VectorEnv


def run(source, check, output):
    if output.exists():
        raise FileExistsError('Use a fresh output directory')
    checked = curve.read(check / 'summary.json')
    original = curve.read(source / 'plan.json')
    chosen = curve.read(source / 'selection.json')
    output.mkdir(parents=True)
    for name in ('parent.pt', 'specialist.pt', 'sfld_rl.dll', 'synthetic.profile'):
        shutil.copyfile(check / name, output / name)
    for name in ('candidate-pilots', 'source', 'training'):
        shutil.copytree(source / name, output / name)
    for path in source.glob('selection-supply-*.json.gz'):
        shutil.copyfile(path, output / path.name)
    for name in ('selection.json', 'router-selection.json'):
        shutil.copyfile(source / name, output / name)
    for name in ('seed-audit.json', 'engine-transfer.json'):
        shutil.copyfile(check / name, output / name)
    for path in check.glob('final-supply-*.json.gz'):
        shutil.copyfile(path, output / path.name)
    plan = {**original, 'schema': 'sfld-budget-current-engine-v1', 'engine': '0.6.11',
            'selection_engine': '0.6.10', 'training_engine': '0.6.10',
            'final': checked['plan']['final'],
            'inputs': {name: train_rl.sha(output / name) for name in original['inputs']},
            'allocation_source_sha256': train_rl.sha(source / 'selection.json'),
            'engine_check_source_sha256': train_rl.sha(check / 'summary.json'),
            'limits': 'Policies trained and pilot-selected on 0.6.10, transferred unchanged to 0.6.11. '
                      'All entry plans remain fixed before this cohort. No new-engine selection or optimality claim. '
                      'Pointwise 95% intervals cover Monte Carlo error only.'}
    for script in (Path(__file__), curve.ROOT / 'tools/benchmark_budget_engine_check.py'):
        relative = script.relative_to(curve.ROOT).as_posix()
        plan['sources'][relative] = train_rl.sha(script)
        shutil.copyfile(script, output / 'source' / relative)
    write_once(output / 'plan.json', plan)
    torch.set_num_threads(2)
    models = {}
    with VectorEnv(output / 'sfld_rl.dll', [output / 'synthetic.profile'], 1,
                   budgets=[0], seed=plan['final']['seed'], seed_namespace=plan['final']['namespace'],
                   deadline_hours=168, max_actions=100000, auto_reset=False) as env:
        current = train_rl.compact_metadata(env.metadata)
        for name in ('parent', 'specialist'):
            saved, model = train_rl.load_checkpoint(output / (name + '.pt'), torch.device('cuda'))
            model.eval()
            transfer_changes(saved['metadata']['environment_signature'], env.metadata, True)
            saved = deepcopy(saved)
            saved['metadata']['environment_signature'] = current
            models[name] = (saved, model)
    router = {row['supply']: row['selected'] for row in curve.read(source / 'router-selection.json')}
    supplies = sorted({row['supply'] for row in chosen})
    cells = {}
    for supply in supplies:
        name = router[supply]
        setup = {**plan, 'checkpoint_sha256': plan['cell_checkpoints'][str(supply)]}
        cells[supply] = curve.collect(output, setup, 'final', supply, *models[name])
    results, all_rows = [], []
    for selection in chosen:
        rows = [curve.project(row, selection['budget'], selection['supply'],
                              selection['reserved_entries'], selection['mode'])
                for row in cells[selection['supply']]]
        results.append({**selection, 'summary': curve.summarize(rows)})
        all_rows.extend(rows)
    with gzip.open(output / 'final-plans.json.gz', 'xt', encoding='utf-8') as stream:
        json.dump(all_rows, stream, separators=(',', ':'))
    normal = [row for row in chosen if row['mode'] == 'normal']
    marginal = []
    for left, right in zip(normal, normal[1:]):
        change = curve.interval((b['completions'] - a['completions']) * 100 /
                                (right['budget'] - left['budget'])
                                for a, b in zip(cells[left['supply']], cells[right['supply']]))
        marginal.append({'from': left['budget'], 'to': right['budget'],
                         'extra_clears_per_100_mushrooms': change})
    pilot_weeks = sum(1 for _ in (source / 'candidate-pilots').rglob('selection-supply-*.json.gz')) * original['selection']['events']
    write_once(output / 'summary.json', {'plan': plan, 'results': results, 'marginal': marginal,
               'selection_policy_weeks': pilot_weeks, 'final_policy_weeks': len(supplies) * plan['final']['events']})
    curve.status(output, 'complete', final_policy_weeks=len(supplies) * plan['final']['events'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--check', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.check.resolve(), args.output.resolve())
