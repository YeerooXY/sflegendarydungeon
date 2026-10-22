"""Matched training effort for an unconstrained control and Pendant-priority PPO."""
import json
from pathlib import Path
import subprocess
import sys

import train_rl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/pendant-adaptation-2026-10-11'
PARENT = ROOT / 'docs/shop-arena-2026-10-11/high-budget-checkpoint.pt'
LIBRARY = ROOT / 'docs/rl-history-2026-10-11/sfld_rl.dll'
PROFILE = ROOT / 'docs/rl-history-2026-10-11/synthetic.profile'
BUDGETS = '0,20,50,100,500,1000'
STEPS = 16777216


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dependencies = [Path(__file__), ROOT / 'tools/train_rl.py', ROOT / 'tools/sfld_rl/ppo.py',
                    ROOT / 'tools/sfld_rl/env.py', ROOT / 'tools/sfld_rl/stone_priority.py',
                    ROOT / 'tools/sfld_rl/shop_experiments.py', ROOT / 'tools/benchmark_rl_shop_arena.py']
    plan = {'schema': 'sfld-pendant-adaptation-v1', 'steps_per_branch': STEPS,
            'budgets': [int(b) for b in BUDGETS.split(',')], 'parent_sha256': train_rl.sha(PARENT),
            'library_sha256': train_rl.sha(LIBRARY), 'profile_sha256': train_rl.sha(PROFILE),
            'sources': {str(p.relative_to(ROOT)): train_rl.sha(p) for p in dependencies},
            'branches': [{'name': 'control', 'stone_priority': 'learned', 'seed': 10001001},
                         {'name': 'priority', 'stone_priority': 'pendant_priority', 'seed': 10001002}],
            'training': {'n_envs': 256, 'rollout_steps': 128, 'validation_events_per_cap': 256,
                         'validate_every_updates': 128, 'learning_rate': .0001,
                         'entropy_coefficient': .003, 'gae_lambda': .95, 'target_kl': .015},
            'selection': 'Each branch selects highest equal-mean completions across its fixed validation caps; exact ties keep earlier checkpoint, including step zero.',
            'final': {'seed': 10003001, 'namespace': 0xDC, 'events_per_cap': 2048,
                      'candidates': ['parent', 'parent_priority', 'trained_control', 'trained_priority']},
            'reward': 'Completed runs only; no bonus for choosing Pendant.',
            'mechanics': 'Unchanged 0.6.7 working profile, including 6.25% arena legendary rarity.',
            'training_constraint': 'Legal stone menu with Pendant: Rabbit then Moonstone then Pendant; other menus and legal healing remain learned. Applied to the distribution for both collection and PPO updates, saved in checkpoint metadata.'}
    plan_path = OUT / 'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise RuntimeError('Frozen study inputs changed')
    train_rl.write_json(plan_path, plan)
    selected = {}
    for branch in plan['branches']:
        dest = OUT / branch['name']
        command = [sys.executable, str(ROOT / 'tools/train_rl.py'), '--mode', 'train',
                   '--finetune-from', str(PARENT), '--library', str(LIBRARY), '--profile', str(PROFILE),
                   '--output', str(dest), '--budgets', BUDGETS, '--seed', str(branch['seed']),
                   '--device', 'cuda', '--torch-threads', '2', '--n-envs', '256',
                   '--total-steps', str(STEPS), '--rollout-steps', '128', '--validation-events', '256',
                   '--validate-every', '128', '--checkpoint-every', '128', '--learning-rate', '0.0001',
                   '--entropy-coefficient', '0.003', '--gae-lambda', '0.95', '--target-kl', '0.015',
                   '--stone-priority', branch['stone_priority']]
        if not (dest / 'training-summary.json').exists():
            print(json.dumps({'starting': branch['name'], 'steps': STEPS}), flush=True)
            with (OUT / (branch['name'] + '.log')).open('w', encoding='utf-8') as log:
                subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        pointer = json.loads((dest / 'best.json').read_text())
        checkpoint = dest / pointer['checkpoint']
        if train_rl.sha(checkpoint) != pointer['sha256']:
            raise RuntimeError('Selected checkpoint hash differs')
        selected[branch['name']] = {'path': str(checkpoint.relative_to(OUT)), **pointer}
        print(json.dumps({'finished': branch['name'], 'selected_steps': pointer['steps'],
                          'validation_score': pointer['selection_score']}), flush=True)
    # Freeze both selections before inspecting any final comparison.
    train_rl.write_json(OUT / 'selection.json', selected)
    candidates = [('parent', PARENT, 'baseline'), ('parent_priority', PARENT, 'pendant_priority'),
                  ('trained_control', OUT / selected['control']['path'], 'baseline'),
                  ('trained_priority', OUT / selected['priority']['path'], 'baseline')]
    for name, checkpoint, policy in candidates:
        command = [sys.executable, str(ROOT / 'tools/benchmark_rl_shop_arena.py'),
                   '--checkpoint', str(checkpoint), '--library', str(LIBRARY), '--profile', str(PROFILE),
                   '--output', str(OUT / ('final-' + name)), '--budgets', BUDGETS,
                   '--policies', policy, '--arena-rate', '6.25', '--events', '2048', '--n-envs', '512',
                   '--seed', str(plan['final']['seed']), '--namespace', hex(plan['final']['namespace']), '--device', 'cuda']
        print(json.dumps({'evaluating': name}), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)
    for path in dependencies:
        if train_rl.sha(path) != plan['sources'][str(path.relative_to(ROOT))]:
            raise RuntimeError('Study dependency changed during execution')
    print('Study complete.', flush=True)


if __name__ == '__main__':
    main()
