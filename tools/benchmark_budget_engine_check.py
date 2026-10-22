"""Test the frozen budget router on the newer engine without selecting on results."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

import torch
import train_rl
import benchmark_budget_curve as curve
from benchmark_player_ideas import transfer_changes, write_once
from sfld_rl.env import VectorEnv
from train_continuation_study import audit_seeds


def run(source, output):
    if output.exists():
        raise FileExistsError('Use a fresh check directory')
    original = curve.read(source / 'plan.json')
    root_seed = 2026101481
    audit = audit_seeds([root_seed])
    output.mkdir(parents=True)
    resources = {'sfld_rl.dll': curve.ROOT / 'build-v0611/Release/sfld_rl.dll',
                 'synthetic.profile': source / 'synthetic.profile',
                 'parent.pt': source / 'parent.pt', 'specialist.pt': source / 'specialist.pt'}
    for name, path in resources.items():
        shutil.copyfile(path, output / name)
    plan = {**original, 'schema': 'sfld-budget-engine-check-v1', 'engine': '0.6.11',
            'final': {'events': 1024, 'seed': root_seed, 'namespace': 231},
            'supplies': [0, 1000, 5000, 10000, 20000],
            'inputs': {name: train_rl.sha(output / name) for name in resources},
            'source_router_sha256': train_rl.sha(source / 'router-selection.json'),
            'purpose': 'Mechanics sensitivity of the preselected router, not additional training or selection.'}
    write_once(output / 'plan.json', plan)
    write_once(output / 'seed-audit.json', audit)
    shutil.copyfile(source / 'router-selection.json', output / 'router-selection.json')
    shutil.copyfile(__file__, output / Path(__file__).name)
    torch.set_num_threads(2)
    models = {}
    transfers = {}
    with VectorEnv(output / 'sfld_rl.dll', [output / 'synthetic.profile'], 1,
                   budgets=[0], seed=root_seed, seed_namespace=231,
                   deadline_hours=168, max_actions=100000, auto_reset=False) as env:
        metadata = train_rl.compact_metadata(env.metadata)
        for name in ('parent', 'specialist'):
            saved, model = train_rl.load_checkpoint(output / (name + '.pt'), torch.device('cuda'))
            model.eval()
            transfers[name] = transfer_changes(saved['metadata']['environment_signature'], env.metadata, True)
            # Inference only: record compatibility first, then attest this environment
            # for the common collector. Checkpoint files and network weights are unchanged.
            saved = deepcopy(saved)
            saved['metadata']['environment_signature'] = metadata
            models[name] = (saved, model)
    write_once(output / 'engine-transfer.json', transfers)
    router = {row['supply']: row['selected'] for row in curve.read(source / 'router-selection.json')}
    results = []
    for supply in plan['supplies']:
        name = router[supply]
        setup = {**plan, 'checkpoint_sha256': plan['inputs'][name + '.pt']}
        rows = curve.collect(output, setup, 'final', supply, *models[name])
        projected = [curve.project(row, supply, supply, 0, 'normal') for row in rows]
        results.append({'budget': supply, 'policy': name, 'summary': curve.summarize(projected)})
    write_once(output / 'summary.json', {'plan': plan, 'results': results})
    curve.status(output, 'complete', policy_weeks=len(results) * plan['final']['events'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())
