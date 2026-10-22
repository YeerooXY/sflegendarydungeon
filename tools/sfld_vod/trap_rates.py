"""Count native-reviewed trap opportunities without treating unknowns as absence."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

from .review import DOORS, PHYSICAL, wilson
from .traps import ABSENCE_CHECKS


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ident(item):
    return item.get('observation_id', item.get('id'))


def _unique(items, label):
    result = {}
    for item in items:
        ident = _ident(item)
        if not ident or ident in result:
            raise ValueError(f'{label} requires unique observation IDs.')
        result[ident] = item
    return result


def normalize_samples(specifications, evidence_root):
    """Build public metadata only after validating local native image proofs.

    Each specification declares its manifest, queue, patch, expected count and
    fixed time boundary. The manifest fixes the denominator before labeling.
    """
    root = Path(evidence_root).resolve()
    dataset = {'schema': 1, 'scope': 'visible_pre_entry_trap_overlay', 'samples': [], 'records': []}
    for spec in specifications:
        paths = {key: root / spec[key] for key in ('manifest', 'queue', 'patch')}
        manifest, queue, patch = (json.loads(paths[key].read_text(encoding='utf-8'))
                                  for key in ('manifest', 'queue', 'patch'))
        selected = _unique(manifest['items'], 'Selection manifest')
        queued = _unique(queue['items'], 'Review queue')
        patched = _unique(patch['patches'], 'Review patches')
        if set(selected) != set(queued) or set(selected) != set(patched):
            raise ValueError('Manifest, queue and patches must contain exactly the same selected IDs.')
        if len(selected) != spec['expected_count']:
            raise ValueError('Sample count does not match its declared intended size.')
        before = manifest.get('selected_before_image_inspection', manifest.get('selected_before_visual_inspection'))
        if before is not True:
            raise ValueError('Sampling must be declared before visual inspection.')
        sample = {key: spec[key] for key in ('sample_id', 'source_id', 'expected_count', 'boundary_source_seconds')}
        sample.update({'sampling': manifest['sampling'], 'selected_before_visual_inspection': True,
                       'selected_observation_ids': list(selected),
                       'selection_manifest_sha256': _hash(paths['manifest']),
                       'selection_input_sha256': manifest.get('selection_public_sha256', manifest.get('source_sha256')),
                       'queue_sha256': _hash(paths['queue']), 'patch_sha256': _hash(paths['patch'])})
        dataset['samples'].append(sample)
        for ident, selected_item in selected.items():
            q, p = queued[ident], patched[ident]
            if not (selected_item['expected_fingerprint'] == q['expected_fingerprint'] == p['expected_fingerprint']):
                raise ValueError('Native review fingerprint differs from its original selection.')
            if p['source_id'] != spec['source_id']:
                raise ValueError('Patch source differs from the sample source.')
            evs = p['evidence']
            if [e['sha256'] for e in evs] != [e['sha256'] for e in q['evidence']]:
                raise ValueError('Patch evidence differs from the native review queue.')
            if p['review']['evidence_sha256'] != [e['sha256'] for e in evs]:
                raise ValueError('Review evidence hashes differ from its patch.')
            for ev in evs:
                path = (root / ev['image']).resolve()
                if not path.is_relative_to(root) or not path.is_file() or _hash(path) != ev['sha256']:
                    raise ValueError('Native proof image missing, changed, or outside evidence root.')
                if type(ev.get('native_height')) is not int or ev['native_height'] < 720:
                    raise ValueError('Trap review requires native 720p or higher source frames.')
            context = q.get('context', selected_item.get('context'))
            kinds = q.get('door_kinds') or [d['kind'] for d in q['original_doors']]
            selected_doors = selected_item.get('doors', selected_item.get('original_doors'))
            if (context != selected_item['context'] or q['source_seconds'] != selected_item['source_seconds']
                    or (selected_doors is not None and kinds != [d['kind'] for d in selected_doors])):
                raise ValueError('Queued context, timestamp or door types differ from the original selection.')
            review_doors = p['review']['doors']
            if len(kinds) != 2 or len(review_doors) != 2 or len(p['set']['door_traps']) != 2:
                raise ValueError('Selection and review must retain exactly two offered positions.')
            if [d['trap'] for d in review_doors] != p['set']['door_traps']:
                raise ValueError('Patch trap statuses differ from the manual review.')
            doors = [{**d, 'kind': kind, 'evidence_sha256': [e['sha256'] for e in evs]}
                     for d, kind in zip(review_doors, kinds)]
            dataset['records'].append({'observation_id': ident, 'sample_id': spec['sample_id'],
                'source_id': spec['source_id'], 'source_seconds': q['source_seconds'],
                'expected_fingerprint': p['expected_fingerprint'], 'context': context, 'doors': doors,
                'evidence': [{key: ev[key] for key in ('sha256', 'source_seconds', 'native_height', 'role')}
                             for ev in evs], 'reviewer': p['review'].get('reviewer')})
    validate(dataset)
    return dataset


def validate(dataset):
    samples = dataset.get('samples', [])
    records = dataset.get('records', [])
    by_id = _unique(records, 'Trap records')
    sample_ids = set()
    manifest_ids = set()
    for sample in samples:
        sid = sample.get('sample_id')
        if not sid or sid in sample_ids:
            raise ValueError('Sample identifiers must be unique.')
        sample_ids.add(sid)
        selected = sample.get('selected_observation_ids', [])
        if len(set(selected)) != len(selected) or len(selected) != sample.get('expected_count'):
            raise ValueError('Manifest count must match the declared sample size without duplicates.')
        if manifest_ids.intersection(selected):
            raise ValueError('Samples cannot reuse an observation.')
        manifest_ids.update(selected)
        if sample.get('selected_before_visual_inspection') is not True:
            raise ValueError('Selection must precede visual inspection.')
        for ident in selected:
            row = by_id.get(ident)
            if row is None or row.get('sample_id') != sid or row.get('source_id') != sample.get('source_id'):
                raise ValueError('Selected observation missing or assigned to a different sample/source.')
            if row['source_seconds'] < sample['boundary_source_seconds']:
                raise ValueError('Observation precedes the fixed sample boundary.')
    if manifest_ids != set(by_id):
        raise ValueError('Every trap record must be in its complete selection manifest.')
    for row in records:
        ctx = row.get('context', {})
        if ctx.get('stone_status') != 'verified' or not isinstance(ctx.get('gems'), list):
            raise ValueError('A verified full stone set is required.')
        if any(not isinstance(g, str) or not g for g in ctx['gems']) or len(set(ctx['gems'])) != len(ctx['gems']):
            raise ValueError('Stone identifiers must be nonempty and unique.')
        if not ctx.get('run_id') or ctx.get('phase') != 'ordinary':
            raise ValueError('Trap sample records must identify an ordinary run phase.')
        if type(row.get('source_seconds')) not in (int, float) or not math.isfinite(row['source_seconds']):
            raise ValueError('Source time must be finite.')
        evs = row.get('evidence', [])
        if not evs:
            raise ValueError('Native proof metadata is required.')
        for ev in evs:
            if not re.fullmatch('[a-f0-9]{64}', ev.get('sha256', '')):
                raise ValueError('Native evidence needs a SHA256 hash.')
            if type(ev.get('native_height')) is not int or ev['native_height'] < 720:
                raise ValueError('Native source resolution must be at least 720p.')
            if type(ev.get('source_seconds')) not in (int, float) or not math.isfinite(ev['source_seconds']):
                raise ValueError('Evidence source time must be finite.')
        doors = row.get('doors', [])
        if len(doors) != 2:
            raise ValueError('Both offered positions must be retained.')
        for door in doors:
            if door.get('kind') not in DOORS:
                raise ValueError('Each side needs its exact door type.')
            value = door.get('trap')
            if value is not None and type(value) is not bool:
                raise ValueError('Trap status must be true, false, or unknown.')
            if not isinstance(door.get('reason'), str) or not door['reason'].strip():
                raise ValueError('Every side needs a visual reason, including unknowns.')
            if door.get('evidence_sha256') != [e['sha256'] for e in evs]:
                raise ValueError('Each side must link its reviewed native evidence.')
            if value is True and not door.get('visual'):
                raise ValueError('A visible trap requires its visual type.')
            if value is not True and door.get('visual'):
                raise ValueError('Unknown/absent traps cannot have asserted trap visuals.')
            if value is False and (len({e['source_seconds'] for e in evs}) < 2 or
                    not all(door.get('checks', {}).get(key) is True for key in ABSENCE_CHECKS)):
                raise ValueError('Absence requires two stable native frames and visibility checks.')
    return dataset


def _rate(values):
    n = len(values); positive = sum(v is True for v in values); negative = sum(v is False for v in values)
    unknown = n - positive - negative
    return {'opportunities': n, 'present': positive, 'absent': negative, 'unknown': unknown,
            'known_only': wilson(positive, positive + negative) if positive + negative else None,
            'missing_data_fraction_bounds': [positive / n, (positive + unknown) / n] if n else None}


def pair_status(doors):
    values = [d['trap'] for d in doors]
    return True if any(v is True for v in values) else False if all(v is False for v in values) else None


def model_pair_chance(row, trap_chance):
    count = sum(d['kind'] in ('monster', 'mystery') for d in row['doors'])
    if count and 'masochist' in row['context']['gems']:
        return 1.0
    return 1 - (1 - trap_chance) ** count


def _summarize(rows, trap_chance):
    sides = [d for r in rows for d in r['doors']]
    types = sorted({d['kind'] for d in sides})
    return {'pairs': _rate([pair_status(r['doors']) for r in rows]),
            'all_offered_sides': _rate([d['trap'] for d in sides]),
            'by_exact_door_type': {kind: _rate([d['trap'] for d in sides if d['kind'] == kind]) for kind in types},
            'pairs_with_two_visible_traps': sum(all(d['trap'] is True for d in r['doors']) for r in rows),
            'model_expected_trap_pairs_for_observed_pairs': sum(model_pair_chance(r, trap_chance) for r in rows),
            'observation_ids': [r['observation_id'] for r in rows]}


def _groups(rows, context, trap_chance):
    groups = defaultdict(list)
    for row in rows:
        groups[json.dumps(context(row), sort_keys=True)].append(row)
    return [{'context': json.loads(key), **_summarize(group, trap_chance)} for key, group in sorted(groups.items())]


def _family(row):
    return sorted('physical' if d['kind'] in PHYSICAL else d['kind'] for d in row['doors'])


def analyze(dataset, trap_chance=.10):
    validate(dataset)
    if not 0 <= trap_chance <= 1:
        raise ValueError('Configured trap chance must be between zero and one.')
    rows = dataset['records']
    return {'schema': 1, 'scope': dataset.get('scope'), 'samples': dataset['samples'],
        'overall_descriptive': _summarize(rows, trap_chance),
        'by_source': _groups(rows, lambda r: {'source_id': r['source_id']}, trap_chance),
        'by_full_context': _groups(rows, lambda r: {'source_id': r['source_id'], **{
            k: r['context'].get(k) for k in ('run_id', 'stage', 'run_class', 'version', 'theme', 'mode')},
            'gems': sorted(r['context']['gems'])}, trap_chance),
        'by_gambler': _groups(rows, lambda r: {'gambler_present': 'gambler' in r['context']['gems']}, trap_chance),
        'by_pair_family': _groups(rows, lambda r: {'family': _family(r)}, trap_chance),
        'by_exact_pair': _groups(rows, lambda r: {'doors': sorted(d['kind'] for d in r['doors'])}, trap_chance),
        'by_model_eligible_side_count': _groups(rows, lambda r: {
            'monster_or_mystery_sides': sum(d['kind'] in ('monster', 'mystery') for d in r['doors']),
            'masochist_present': 'masochist' in r['context']['gems']}, trap_chance),
        'configured_model': {'trap_chance_per_eligible_attempt': trap_chance,
            'eligible_types': ['monster', 'mystery'], 'maximum_traps_per_pair': 1,
            'one_eligible_side_pair_chance': trap_chance,
            'two_eligible_sides_pair_chance': 1 - (1 - trap_chance) ** 2,
            'rule': 'Randomize side order, then attempt each monster/mystery side until the first successful trap. Masochist forces the first eligible side. Gambler changes the trap effect, not this chance.',
            'status': 'Configured assumption, not fitted to these observations.'},
        'interpretation': [
            'Two fixed-boundary convenience blocks selected independently of existing trap labels; clustered segments, not random event-wide samples.',
            'A pair is positive if either side is visibly trapped, negative only when both are explicitly clear, otherwise unknown. Two trapped sides remain two positives and are never rejected.',
            'Door-side frequencies and pair frequencies have different denominators. All exact door styles are shown; eligibility in the game is not inferred from absence or engine rules.',
            'Wilson intervals use only known outcomes and cover binomial sampling uncertainty only, not temporal clustering, missed offers, source selection or visual-label error.',
            'Missing-data bounds retain all selected offers and put every unresolved outcome first at zero and then at one. Unreadable records cannot disappear from the denominator.',
            'This measures visible pre-entry trap overlays, not the damage/cursing outcome, trap subtype probabilities across events, or the probability of actually choosing a trapped door.',
            'Full stone sets, stage and source remain separate. No simulator probabilities were changed.']}


def render(report):
    stats = report['overall_descriptive']; pair = stats['pairs']; known = pair['known_only']
    lines = ['# Native trap occurrence audit', '',
        f"{pair['present']} of {pair['opportunities']} selected ordinary door choices contain a visible trap. "
        f"{pair['unknown']} pair outcomes remain unknown. These are two short, clustered footage segments, not an event-wide probability estimate.", '',
        '| Source | Selected pairs | Trap pairs | Clear pairs | Unknown | Fraction among resolved pairs |',
        '|---|---:|---:|---:|---:|---:|']
    for group in report['by_source']:
        p = group['pairs']; frac = p['known_only']; text = f"{frac['estimate']:.2%}" if frac else 'unknown'
        lines.append(f"| {group['context']['source_id']} | {p['opportunities']} | {p['present']} | {p['absent']} | {p['unknown']} | {text} |")
    if known:
        lo, hi = known['wilson_95']
        lines += ['', f"Pooled descriptive result: **{known['estimate']:.2%} of resolved pairs** ({pair['present']}/{known['opportunities']}), Wilson 95% interval **{lo:.2%}–{hi:.2%}**. This interval assumes independent binomial outcomes; it does not cover the selection and clustering limitations."]
    if pair['unknown']:
        lo, hi = pair['missing_data_fraction_bounds']
        lines += ['', f"Retaining all **{pair['opportunities']} selected pairs**, the observed fraction lies between **{lo:.2%} and {hi:.2%}** when all {pair['unknown']} unresolved pairs are treated first as clear and then as trapped. These missing-data bounds are not confidence intervals."]
    lines += ['', '## Individual offered positions', '',
        'These denominators count positions, not two-room choices. No physical door style is silently removed as supposedly trap-ineligible.', '',
        '| Exact type | Positions reviewed | Trap present | Explicitly clear | Unknown |',
        '|---|---:|---:|---:|---:|']
    for kind, values in stats['by_exact_door_type'].items():
        lines.append(f"| {kind} | {values['opportunities']} | {values['present']} | {values['absent']} | {values['unknown']} |")
    lines += ['', '## Stage and complete stone sets', '',
        '| Source / run | Stage | Full stones | Pairs | Trap pairs | Unknown |',
        '|---|---:|---|---:|---:|---:|']
    for group in report['by_full_context']:
        c = group['context']; p = group['pairs']
        lines.append(f"| {c['source_id']} / {c['run_id']} | {c['stage']} | {', '.join(c['gems']) or 'none'} | {p['opportunities']} | {p['present']} | {p['unknown']} |")
    model = report['configured_model']
    lines += ['', '## Comparison with the configured model', '',
        f"This comparison uses `trap_chance={model['trap_chance_per_eligible_attempt']:.2f}`. The engine attempts a trap on each monster/mystery side in randomized order and stops after the first success. "
        f"That means **{model['one_eligible_side_pair_chance']:.0%} per pair with one eligible side** and **{model['two_eligible_sides_pair_chance']:.0%} with two**, not a flat {model['trap_chance_per_eligible_attempt']:.0%} per choice. Masochist forces a trap on the first eligible side; Gambler changes its effect.", '',
        f"For the actual sampled door pairs and stones, that assumption predicts {stats['model_expected_trap_pairs_for_observed_pairs']:.2f} trapped pairs in {pair['opportunities']}. This is a descriptive check; it does not establish the model's eligibility rules or calibrate its chance.", '',
        f"Pairs with two visibly trapped sides: **{stats['pairs_with_two_visible_traps']}**. The analyzer permits this outcome even though the simulator currently caps traps at one.", '',
        '## Sampling and uncertainty', '']
    for sample in report['samples']:
        lines.append(f"- `{sample['sample_id']}`: {sample['sampling']} Fixed boundary {sample['boundary_source_seconds']} seconds; intended and retained sample {sample['expected_count']}.")
    lines += ['', *report['interpretation'], '',
        'Public evidence metadata and complete fixed-denominator manifests: `data/vods/trap-systematic-2026-10-08.json`. Native image files are kept locally; public records retain timestamps, SHA256 hashes, full context and each side\'s visual reason.', '',
        'Reproduce: `python tools/analyze_vod_traps.py --input data/vods/trap-systematic-2026-10-08.json --output docs/vod-trap-rates-2026-10-08.json`. Add `--normalize-root out/vods` to validate native proof hashes and rebuild the public dataset from the original sample manifests.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/vods/trap-systematic-2026-10-08.json'))
    parser.add_argument('--output', type=Path, default=Path('docs/vod-trap-rates-2026-10-08.json'))
    parser.add_argument('--normalize-root', type=Path)
    parser.add_argument('--trap-chance', type=float, default=.10)
    args = parser.parse_args()
    if args.normalize_root:
        prefix = 'trap-systematic-2026-10-08/'
        specs = [dict(sample_id='n3day1', source_id='2878232329', expected_count=30, boundary_source_seconds=3000,
                      manifest=prefix+'n3day1/selection-manifest.json', queue=prefix+'n3day1/queue.json', patch=prefix+'n3day1/patch.json'),
                 dict(sample_id='sep22', source_id='2881153716', expected_count=30, boundary_source_seconds=9000,
                      manifest=prefix+'sep22/manifest.json', queue=prefix+'sep22/queue.json', patch=prefix+'sep22/validated-patches.json')]
        dataset = normalize_samples(specs, args.normalize_root)
        args.input.parent.mkdir(parents=True, exist_ok=True)
        args.input.write_text(json.dumps(dataset, indent=2)+'\n', encoding='utf-8')
    else:
        dataset = json.loads(args.input.read_text(encoding='utf-8'))
    report = analyze(dataset, args.trap_chance)
    report.update({'input': args.input.as_posix(), 'input_sha256': _hash(args.input)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(render(report), encoding='utf-8')
    print(json.dumps(report['overall_descriptive'], indent=2))


if __name__ == '__main__':
    main()
