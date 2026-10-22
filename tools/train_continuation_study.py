"""Continue the deployed actor on the corrected engine with untouched final tests.

Each invocation creates a new frozen experiment. It never changes the deployed
checkpoint. The two continuation branches share a parent, not training seeds.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from statistics import mean, NormalDist
import traceback

import torch

import train_rl
import train_current_study as evaluation
from train_multiseed_study import train
from migrate_current_engine import migrate
from benchmark_player_ideas import write_once
from publish_effect_study import stats
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA


ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500, 1000]


def status(out, phase, **details):
    value = {'phase': phase, 'updated_utc': datetime.now(timezone.utc).isoformat(), **details}
    train_rl.write_json(out / 'status.json', value)
    print(json.dumps(value), flush=True)


def audit_seeds(roots):
    pattern = re.compile(r'"(?:root_seed|seed)"\s*:\s*"?(\d+)')
    historical = set()
    paths = []
    # Plans and run metadata include training, validation, final and ancestor
    # roots. Avoid reading unrelated VOD observations or large episode tables.
    for folder in ('docs', 'out'):
        for name in ('plan.json', 'run.json'):
            paths.extend((ROOT / folder).rglob(name))
    for path in paths:
        historical.update(map(int, pattern.findall(path.read_text(encoding='utf-8-sig'))))
    fingerprints = [train_rl.seed_fingerprint(seed) for seed in roots]
    if len(set(fingerprints)) != len(fingerprints):
        raise RuntimeError('New study roots share a native seed fingerprint')
    if set(fingerprints) & {train_rl.seed_fingerprint(seed) for seed in historical}:
        raise RuntimeError('A new study root reuses a historical seed fingerprint')
    return {'roots': roots, 'fingerprints': fingerprints,
            'historical_root_count': len(historical), 'metadata_files_checked': len(paths),
            'historical_overlap': False}


def prepare(out, smoke):
    if train_rl.sha(CHECKPOINT) != CHECKPOINT_SHA:
        raise RuntimeError('Deployed parent checkpoint identity changed')
    if out.exists():
        raise FileExistsError('Use a new output directory: ' + str(out))
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required for this training study')
    base = 2026101490 if smoke else 2026101400
    roots = [base + n for n in (1, 2, 11, 12)]
    audit = audit_seeds(roots)
    out.mkdir(parents=True, exist_ok=False)
    status(out, 'preparing', smoke=smoke)
    for source, name in (
        (CHECKPOINT, 'original-raw.pt'),
        (ROOT / 'build-v0610/Release/sfld_rl.dll', 'sfld_rl.dll'),
        (ROOT / 'profiles/synthetic.profile', 'synthetic.profile'),
    ):
        evaluation.copy(source, out / name)
    sources = [Path(__file__), ROOT / 'CMakeLists.txt']
    sources += [ROOT / 'tools' / name for name in (
        'train_rl.py', 'train_current_study.py', 'train_multiseed_study.py',
        'migrate_current_engine.py', 'benchmark_player_ideas.py', 'publish_effect_study.py')]
    sources += sorted((ROOT / 'tools/sfld_rl').glob('*.py'))
    sources += sorted(p for folder in ('src', 'include') for p in (ROOT / folder).rglob('*')
                      if p.suffix in ('.cpp', '.hpp', '.h', '.c'))
    plan = {
        'schema': 'sfld-corrected-engine-continuation-v1', 'smoke': smoke,
        'created_utc': datetime.now(timezone.utc).isoformat(), 'budgets': BUDGETS,
        'parent': str(CHECKPOINT.relative_to(ROOT)), 'parent_sha256': CHECKPOINT_SHA,
        'engine': '0.6.10', 'gpu': torch.cuda.get_device_name(0),
        'profile_sha256': train_rl.sha(out / 'synthetic.profile'),
        'library_sha256': train_rl.sha(out / 'sfld_rl.dll'),
        'sources': {p.relative_to(ROOT).as_posix(): train_rl.sha(p) for p in sources},
        'branches': [{'name': f'raw-{i + 1}', 'family': 'raw', 'seed': roots[i],
                      'steps': 2048 if smoke else 33554432} for i in range(2)],
        'settings': {
            'n_envs': 8 if smoke else 512, 'rollout_steps': 16 if smoke else 128,
            'minibatch_size': 64 if smoke else 2048,
            'validation_events': 2 if smoke else 256, 'validate_every': 8 if smoke else 64,
            'learning_rate': .00007, 'entropy_coefficient': .0075,
            'gae_lambda': .99, 'target_kl': .02,
        },
        'selection': {'seed': roots[2], 'namespace': 201, 'events': 2 if smoke else 1024},
        'final': {'seed': roots[3], 'namespace': 202, 'events': 4 if smoke else 8192},
        'objective': 'Maximize completed runs in 168 hours; random eligible stones; '
                     'all legal choices within each gross mushroom cap. No forced key/reroll quotas.',
        'transfer': 'Explicitly transfer unchanged weights and Adam moments to engine 0.6.10. '
                    'This starts new training experiments, not a resume of the old mechanics.',
        'selection_rule': 'Compare parent and both internally selected checkpoints on a fresh '
                          'equal-budget mean. Ties retain the parent. Freeze winner before final tests.',
        'evaluation_controller': 'Same Practical(learned) soldier-search and OHW-downgrade guards '
                                 'as the deployed adviser, applied equally to parent and challenger.',
        'promotion_gate': 'Positive lower simultaneous 99% bound on equal-budget gain, '
                          'and no negative upper bound at an individual budget. Bonferroni '
                          'adjustment across seven contrasts. Smoke results cannot promote.',
        'deployment': 'Record eligibility only; never replace the deployed checkpoint automatically.',
        'limits': 'Finite continuation on a synthetic working profile does not prove global '
                  'optimality or live-game accuracy. Final seeds are not used for tuning.',
    }
    write_once(out / 'plan.json', plan)
    write_once(out / 'seed-audit.json', audit)
    for path in sources:
        evaluation.copy(path, out / 'source' / path.relative_to(ROOT))
    args = train_rl.parse([
        '--mode', 'train', '--output', str(out / 'unused'),
        '--library', str(out / 'sfld_rl.dll'), '--profile', str(out / 'synthetic.profile'),
        '--budgets', ','.join(map(str, BUDGETS)), '--device', 'cuda', '--shop-policy', 'learned'])
    with train_rl.make_env(args, n_envs=1) as env:
        if env.metadata['schema']['engine'] != plan['engine']:
            raise RuntimeError('The library is not the declared corrected engine')
    migrate(out / 'original-raw.pt', out / 'parent-raw.pt', args)
    return plan


def verify_inputs(out, plan):
    for path, expected in plan['sources'].items():
        if train_rl.sha(ROOT / path) != expected or train_rl.sha(out / 'source' / path) != expected:
            raise RuntimeError('Frozen study source changed: ' + path)
    for path, key in (('sfld_rl.dll', 'library_sha256'), ('synthetic.profile', 'profile_sha256'),
                      ('original-raw.pt', 'parent_sha256')):
        if train_rl.sha(out / path) != plan[key]:
            raise RuntimeError('Frozen study input changed: ' + path)


def report(out, plan, result):
    winner = result['selection']['winner']
    lines = [
        '# Corrected-engine training continuation', '',
        ('Smoke test only; these scores are not research results.' if plan['smoke'] else
         'Two independent continuations of the deployed actor on engine 0.6.10.'), '',
        f"Additional training decisions: {result['training_decisions']:,}.",
        f"Completed training events: {result['completed_training_events']:,}.",
        f"Selected candidate: `{winner}`. Promotion gate passed: {result['promotion_eligible']}.", '',
        f"Final test: {plan['final']['events']:,} fresh 168-hour events per budget and policy.", '',
        '| Mushroom cap | Existing policy | Selected policy |',
        '|---:|---:|---:|',
    ]
    cells = {(row['identity']['candidate']['name'], row['identity']['budget']): row
             for row in result['final']}
    for budget in BUDGETS:
        old = cells['parent-raw', budget]['summary']['completions']['mean']
        new = cells[winner, budget]['summary']['completions']['mean']
        lines.append(f'| {budget} | {old:.4f} | {new:.4f} |')
    if result['contrasts']:
        overall = result['contrasts'][-1]
        low, high = overall['family_99_ci']
        lines += ['', f"Equal-budget gain: {overall['mean']:+.5f} clears/week; "
                  f'simultaneous 99% interval [{low:+.5f}, {high:+.5f}].']
    lines += ['', 'The deployed checkpoint has not been replaced. Finite training on the working '
              'simulator cannot establish the globally optimal or best real-game strategy.', '']
    (out / 'RESULTS.md').write_text('\n'.join(lines), encoding='utf-8')


def run(out, smoke):
    torch.set_num_threads(2)
    plan = prepare(out, smoke)
    parent = {'name': 'parent-raw', 'family': 'raw', 'checkpoint': 'parent-raw.pt',
              'sha256': train_rl.sha(out / 'parent-raw.pt'), 'steps': 0, 'style': 'raw'}
    candidates = [parent]
    training_decisions = completed_events = 0
    for branch in plan['branches']:
        verify_inputs(out, plan)
        status(out, 'training', branch=branch['name'], target_decisions=branch['steps'])
        candidates.append(train(out, plan, branch))
        summary = evaluation.read(out / branch['name'] / 'training-summary.json')
        training_decisions += summary['state']['steps']
        completed_events += summary['state']['completed_training_events']
    write_once(out / 'training-selection.json', candidates)
    status(out, 'selection', training_decisions=training_decisions)
    selection_cells = {(c['name'], b): evaluation.evaluate(out, plan, c, 'selection', b)
                       for c in candidates for b in BUDGETS}
    scores = {c['name']: mean(selection_cells[c['name'], b]['summary']['completions']['mean']
                             for b in BUDGETS) for c in candidates}
    selection = {'winner': max(scores, key=scores.get), 'scores': scores}
    write_once(out / 'selection.json', selection)
    winner = next(c for c in candidates if c['name'] == selection['winner'])
    status(out, 'final_evaluation', selection=selection)
    selected = [parent] if parent == winner else [parent, winner]
    final = {(c['name'], b): evaluation.evaluate(out, plan, c, 'final', b)
             for c in selected for b in BUDGETS}
    contrasts = []
    if parent != winner:
        z = NormalDist().inv_cdf(1 - .01 / (2 * 7))
        deltas = []
        for budget in BUDGETS:
            old, new = final[parent['name'], budget]['rows'], final[winner['name'], budget]['rows']
            if [r['event'] for r in old] != [r['event'] for r in new]:
                raise RuntimeError('Final comparison has mismatched episode indices')
            delta = [b['completions'] - a['completions'] for a, b in zip(old, new)]
            deltas.append(delta)
            contrasts.append({'budget': budget, **stats(delta, z)})
        contrasts.append({'budget': 'equal-budget mean', **stats([mean(x) for x in zip(*deltas)], z)})
    promotion = (not smoke and bool(contrasts) and contrasts[-1]['family_99_ci'][0] > 0
                 and all(row['family_99_ci'][1] >= 0 for row in contrasts[:-1]))
    verify_inputs(out, plan)
    result = {'selection': selection, 'contrasts': contrasts, 'promotion_eligible': promotion,
              'training_decisions': training_decisions, 'completed_training_events': completed_events,
              'deployed_policy_changed': False,
              'final': [{k: v for k, v in cell.items() if k != 'rows'} for cell in final.values()]}
    write_once(out / 'summary.json', result)
    report(out, plan, result)
    status(out, 'complete', training_decisions=training_decisions,
           completed_training_events=completed_events, selection=selection, promotion_eligible=promotion)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    existed = output.exists()
    try:
        run(output, args.smoke)
    except Exception as exc:
        # Never alter an existing experiment if the user supplied its directory.
        if not existed and output.is_dir():
            status(output, 'failed', error=str(exc), traceback=traceback.format_exc())
        raise
