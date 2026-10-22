"""Independently verify and archive the corrected-engine continuation study."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
from statistics import mean, NormalDist

from publish_multiseed_study import verify_cell
from publish_effect_study import sha, stats


def read(path):
    return path.read_text(encoding='utf-8')


def publish(source, target):
    plan = json.loads(read(source / 'plan.json'))
    result = json.loads(read(source / 'summary.json'))
    candidates = json.loads(read(source / 'training-selection.json'))
    if plan['smoke'] or target.exists():
        raise ValueError('Use a finished production study and a new archive directory.')
    for name, digest in plan['sources'].items():
        if sha(source / 'source' / name) != digest:
            raise ValueError('Source snapshot changed: ' + name)
    for name, key in [('original-raw.pt', 'parent_sha256'), ('synthetic.profile', 'profile_sha256'),
                      ('sfld_rl.dll', 'library_sha256')]:
        if sha(source / name) != plan[key]:
            raise ValueError('Study input changed: ' + name)
    for candidate in candidates:
        if sha(source / candidate['checkpoint']) != candidate['sha256']:
            raise ValueError('Checkpoint changed.')
    decisions = weeks = 0
    for branch in plan['branches']:
        state = json.loads(read(source / branch['name'] / 'training-summary.json'))['state']
        if state['steps'] != branch['steps'] or json.loads(read(source / (branch['name'] + '-status.json')))['exit_code']:
            raise ValueError('Training did not complete.')
        decisions += state['steps']; weeks += state['completed_training_events']
    if decisions != result['training_decisions'] or weeks != result['completed_training_events']:
        raise ValueError('Training accounting differs.')
    cells = {}
    for candidate in candidates:
        for budget in plan['budgets']:
            cell = json.loads(read(source / f'selection-{candidate["name"]}-{budget}.json'))
            verify_cell(cell, plan, candidate, 'selection', budget, plan['profile_sha256'])
            cells[candidate['name'], budget] = cell
    scores = {candidate['name']: mean(cells[candidate['name'], b]['summary']['completions']['mean']
                                     for b in plan['budgets']) for candidate in candidates}
    selection = {'winner': max(scores, key=scores.get), 'scores': scores}
    if selection != result['selection'] or selection != json.loads(read(source / 'selection.json')):
        raise ValueError('Selection differs from the frozen decision.')
    winner = next(c for c in candidates if c['name'] == selection['winner'])
    parent = candidates[0]
    final = {}
    for candidate in ([parent] if parent == winner else [parent, winner]):
        for budget in plan['budgets']:
            cell = json.loads(read(source / f'final-{candidate["name"]}-{budget}.json'))
            verify_cell(cell, plan, candidate, 'final', budget, plan['profile_sha256'])
            final[candidate['name'], budget] = cell
    contrasts = []; deltas = []
    if parent != winner:
        z = NormalDist().inv_cdf(1 - .01 / (2 * 7))
        for budget in plan['budgets']:
            delta = [b['completions'] - a['completions'] for a, b in
                     zip(final[parent['name'], budget]['rows'], final[winner['name'], budget]['rows'])]
            deltas.append(delta); contrasts.append({'budget': budget, **stats(delta, z)})
        contrasts.append({'budget': 'equal-budget mean', **stats([mean(x) for x in zip(*deltas)], z)})
    eligible = bool(contrasts) and contrasts[-1]['family_99_ci'][0] > 0 and all(r['family_99_ci'][1] >= 0 for r in contrasts[:-1])
    if contrasts != result['contrasts'] or eligible != result['promotion_eligible']:
        raise ValueError('Final comparison differs from the stored result.')
    target.mkdir(parents=True)
    for path in source.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(('selection-', 'final-')) and path.suffix == '.json':
            with gzip.open(target / (path.name + '.gz'), 'wb') as stream:
                stream.write(path.read_bytes())
        elif path.suffix in ('.json', '.profile', '.dll', '.md'):
            shutil.copyfile(path, target / path.name)
    shutil.copytree(source / 'source', target / 'source')
    for candidate in candidates:
        path = Path(candidate['checkpoint']); (target / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / path, target / path)
    for branch in plan['branches']:
        for path in (source / branch['name']).iterdir():
            if path.suffix in ('.json', '.jsonl'):
                shutil.copyfile(path, target / branch['name'] / path.name)
    (target / '.gitattributes').write_text('** -text\n', encoding='utf-8')
    evidence = {'training_decisions': decisions, 'training_weeks': weeks,
                'selection_policy_weeks': sum(c['events'] for c in cells.values()),
                'final_policy_weeks': sum(c['events'] for c in final.values()),
                'accounting_verified': True, 'promotion_eligible': eligible,
                'selected_checkpoint': winner, 'publisher_sha256': sha(__file__)}
    (target / 'validation.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    (target / 'manifest.json').write_text(json.dumps({'files': {
        p.relative_to(target).as_posix(): sha(p) for p in sorted(target.rglob('*')) if p.is_file()
    }}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args(); publish(args.source, args.target)
