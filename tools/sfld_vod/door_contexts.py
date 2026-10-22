"""Describe door families and mystery entries without imputing selected doors.

All denominators use the temporal review ledger's eligibility checks. Physical
door-slot shares and offer prevalence are different quantities: an observation
with two physical doors contributes two slots and one offer. Stone-gate anomalies
are retained and reported, never relabeled to satisfy a simulator rule.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .review import PHYSICAL
from .temporal import CONTENTS, normalize_generation, observation_counts, selection_basis

GATES = {'unlocked': 'spying', 'double_locked': 'lodestone', 'epic': 'devil'}
CONTEXT_FIELDS = ('version', 'stage', 'run_class', 'theme', 'mode', 'phase')


def fraction(count, denominator):
    return count / denominator if denominator else None


def family(style):
    return 'door' if style in PHYSICAL else style


def context(row):
    ctx = row['context']
    return {'source_id': row['source_id'],
            **{name: ctx.get(name, 'ordinary' if name == 'phase' else None)
               for name in CONTEXT_FIELDS},
            'gems': sorted(set(ctx['gems'])), 'generation': row['generation']}


def choice_known(row):
    return ('chosen_side' in row['verified_fields']
            and type(row.get('chosen_side')) is int and row['chosen_side'] in (0, 1))


def content_known(row):
    return ('entered_room' in row['verified_fields']
            and 'initial_content' in row['verified_fields']
            and row.get('outcome', {}).get('initial_content') in CONTENTS
            and bool(row.get('outcome', {}).get('evidence')))


def mystery_stats(rows):
    """Every supplied row offers mystery; separate selection and content gaps."""
    results = {'offered_pairs': len(rows), 'verified_selected_mystery_entries': 0,
               'initial_contents': {}, 'unknown_selection': 0, 'selected_other': 0,
               'selected_mystery_without_known_content': 0,
               'verified_following_entries': 0, 'unlinked_following_entries': 0,
               'unlinked_following_contents': {}, 'offered_runs': 0,
               'verified_selected_entry_runs': 0}
    contents = Counter()
    bases = Counter()
    unlinked = Counter()
    known_runs = set()
    for row in rows:
        covered = observation_counts([row])['coverage']
        initial = content_known(row)
        if initial:
            results['verified_following_entries'] += 1
        if covered.get('eligible_mystery_outcomes'):
            results['verified_selected_mystery_entries'] += 1
            contents[row['outcome']['initial_content']] += 1
            bases[selection_basis(row)] += 1
            known_runs.add((row['source_id'], row['context']['run_id']))
        elif covered.get('mystery_selection_unknown'):
            results['unknown_selection'] += 1
            if initial:
                results['unlinked_following_entries'] += 1
                unlinked[row['outcome']['initial_content']] += 1
        elif covered.get('mystery_not_selected'):
            results['selected_other'] += 1
        elif covered.get('selected_mystery_outcome_unknown'):
            results['selected_mystery_without_known_content'] += 1
        else:
            raise ValueError('Mystery subset contains an ineligible or non-mystery row.')
    results['initial_contents'] = dict(sorted(contents.items()))
    results['selection_bases'] = dict(sorted(bases.items()))
    results['initial_content_fractions'] = {
        name: fraction(count, results['verified_selected_mystery_entries'])
        for name, count in sorted(contents.items())}
    results['unlinked_following_contents'] = dict(sorted(unlinked.items()))
    results['offered_runs'] = len({(r['source_id'], r['context']['run_id']) for r in rows})
    results['verified_selected_entry_runs'] = len(known_runs)
    assert sum(results[key] for key in (
        'verified_selected_mystery_entries', 'unknown_selection', 'selected_other',
        'selected_mystery_without_known_content')) == len(rows)
    return results


def style_stats(rows, style):
    slots = sum(d['kind'] == style for r in rows for d in r['doors'])
    physical_slots = sum(d['kind'] in PHYSICAL for r in rows for d in r['doors'])
    offers = sum(any(d['kind'] == style for d in r['doors']) for r in rows)
    return {'style_slots': slots, 'physical_slots': physical_slots,
            'share_of_physical_slots': fraction(slots, physical_slots),
            'offers_containing_style': offers, 'eligible_offers': len(rows),
            'share_of_all_offers': fraction(offers, len(rows))}


def summarize(rows, style_rates=True):
    """Describe a previously validated eligible cohort, retaining exact labels."""
    styles = {name: style_stats(rows, name) for name in sorted(PHYSICAL)}
    pairs = Counter('/'.join(sorted(family(d['kind']) for d in r['doors'])) for r in rows)
    exact = defaultdict(list)
    for row in rows:
        ds = row['doors']
        if not any(d['kind'] == 'mystery' for d in ds):
            continue
        other = 'mystery' if all(d['kind'] == 'mystery' for d in ds) else next(
            d['kind'] for d in ds if d['kind'] != 'mystery')
        exact[other].append(row)
    # Include zero-count alternatives explicitly so "no verified data" is visible.
    alternatives = sorted(PHYSICAL | {'mystery', 'monster', 'golden'})
    mystery_exact = {other: mystery_stats(exact[other]) for other in alternatives}
    door_rows = [r for other, members in exact.items() if other in PHYSICAL for r in members]
    gates = {}
    for style, stone in GATES.items():
        present = [r for r in rows if stone in r['context']['gems']]
        absent = [r for r in rows if stone not in r['context']['gems']]
        gates[style] = {'required_stone_under_current_rule': stone,
                        'gate_present': style_stats(present, style),
                        'gate_absent': style_stats(absent, style),
                        'gate_absent_anomaly_ids': [r['id'] for r in absent
                            if any(d['kind'] == style for d in r['doors'])]}
    result = {'eligible_offers': len(rows), 'all_door_slots': 2 * len(rows),
            'physical_door_slots': sum(d['kind'] in PHYSICAL for r in rows for d in r['doors']),
            'offers_with_physical_door': sum(any(d['kind'] in PHYSICAL for d in r['doors']) for r in rows),
            'offers_with_two_physical_doors': sum(all(d['kind'] in PHYSICAL for d in r['doors']) for r in rows),
            'family_pair_counts': dict(sorted(pairs.items())),
            'mystery_by_exact_alternative': mystery_exact,
            'mystery_by_alternative_family': {
                'door': mystery_stats(door_rows), 'mystery': mystery_exact['mystery'],
                'monster': mystery_exact['monster'], 'golden': mystery_exact['golden']},
            'stone_gate_diagnostics': gates}
    if style_rates:
        result['physical_style_counts'] = styles
    else:
        result['physical_style_inventory'] = {
            name: {key: value for key, value in stats.items() if not key.startswith('share_')}
            for name, stats in styles.items()}
    return result


def separated_cohorts(rows):
    """Keep baseline and gated-stone exposures separate, retaining coexisting stones.

    Gate cohorts can overlap when both stones are held; never concatenate their
    rows as independent observations. Missing/incomplete stones are excluded.
    """
    eligible = [normalize_generation(row) for row in rows
                if observation_counts([row])['coverage'].get('eligible_decisions')]
    return {'no_stones': [r for r in eligible if not r['context']['gems']],
            **{stone: [r for r in eligible if stone in r['context']['gems']]
               for stone in GATES.values()}}


def export_cohorts(rows, directory, parent_hash):
    observation_counts(rows)  # Validate identity uniqueness before any writes.
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for cohort, members in separated_cohorts(rows).items():
        path = directory / f'{cohort}.jsonl'
        payload = ''.join(json.dumps(row, separators=(',', ':'))+'\n' for row in members).encode('utf-8')
        path.write_bytes(payload)
        files[cohort] = {'file': path.name, 'records': len(members),
                         'sha256': hashlib.sha256(payload).hexdigest()}
    manifest = {'schema': 1, 'parent_observations_sha256': parent_hash, 'cohorts': files,
                'normal_style_baseline': 'no_stones',
                'coexisting_stones_preserved': True,
                'overlap': 'Stone cohorts overlap when a character holds multiple gated stones; do not pool or sum their rows.',
                'scope': 'Each file contains all eligible offers in its stone cohort, including those without the gated door. This preserves exposure denominators.'}
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


def analyze(rows):
    # Validate the whole ledger first: per-row checks alone cannot detect duplicate
    # source/run/phase/room identities or repeated observation IDs.
    coverage = observation_counts(rows)['coverage']
    normalized = [normalize_generation(row) for row in rows]
    eligible = [row for row in normalized
                if observation_counts([row])['coverage'].get('eligible_decisions')]
    cohorts = defaultdict(list)
    for row in eligible:
        cohorts[json.dumps(context(row), sort_keys=True)].append(row)
    separated = separated_cohorts(eligible)
    return {'schema': 2, 'coverage': coverage,
            'definitions': {
                'physical_door_styles': sorted(PHYSICAL),
                'wall': 'Literal impassable stone wall, not shorthand for the full physical-door family.',
                'style_share': 'Occurrences of the exact style / all physical-door slots in the cohort.',
                'offer_share': 'Offers containing the style / all eligible ordinary offers in the cohort.',
                'mystery_entry': 'Same strict selection/content eligibility as temporal.observation_counts. Double-mystery needs no selected side because either side is mystery.',
                'unlinked_following_entry': 'Verified room content is visible after the offer, but the selected side remains unknown. It is not included in mystery probabilities.',
                'stone_gates': 'Diagnostics against current stone-availability rules. Anomalies remain in counts pending direct review.',
                'baseline': 'Explicitly verified empty stone sets; unlocked/double-locked/epic stone exposures do not enter this denominator.',
                'separate_stone_datasets': 'Gate-present cohorts retain every offered pair, full coexisting stones and exact source/stage context. They can overlap and must not be merged as independent samples.'},
            'overall_descriptive': summarize(eligible, style_rates=False),
            'baseline_no_stones_descriptive': summarize(separated['no_stones']),
            'stone_specific_datasets': {stone: summarize(separated[stone]) for stone in GATES.values()},
            'by_source': {source: summarize([r for r in eligible if r['source_id'] == source], style_rates=False)
                          for source in sorted({r['source_id'] for r in eligible})},
            'fully_conditioned_strata': [
                {'context': json.loads(key), **summarize(members)}
                for key, members in sorted(cohorts.items())],
            'limitations': [
                'These are descriptive observed frequencies, not fitted game probabilities. Players, stages, stone sets, versions and repeated rooms within runs are not independent draws.',
                'Ordinary offers exclude verified shops, bosses, arena choices and the first five scripted rooms. Unknown opening status or incomplete stones are excluded.',
                'Wall/mystery outcomes do not establish outcomes for locked, cursed, sacrificial or other door/mystery pairs. Keep exact alternatives available even when reporting their family aggregate.',
                'Normal style rates use only the no-stone cohort. Unlocked, double-locked and epic doors have separate stone-present datasets; pooled style percentages are intentionally not reported.',
                'Unknown selection, unobserved content and lethal traps are not recorded as empty rooms. A following monster or hovered cursor alone does not establish the chosen door.',
                'A chest or corpse on entry is separate from its later mimic revelation or awakening. Container reward odds are a separate analysis.']}


def percent(value):
    return '—' if value is None else f'{100 * value:.2f}%'


def render(result):
    overall = result['overall_descriptive']
    baseline = result['baseline_no_stones_descriptive']
    lines = ['# Door families and mystery-room contexts', '',
             'Descriptive audit only. **Wall means a literal wall.** It is one style in the physical-door family; its observed mystery contents cannot be applied to every door/mystery combination.', '',
             f"Input: `{result.get('inputs', {}).get('observations', 'in-memory ledger')}`. "
             f"Eligible ordinary offers: **{overall['eligible_offers']}**; physical-door slots: **{overall['physical_door_slots']}**. "
             f"Verified no-stone offers: **{baseline['eligible_offers']}**.", '',
             '## Normal-door baseline: no stones', '',
             'Only explicitly verified empty stone sets enter this table. Unlocked, double-locked and epic-door stone exposures are kept in separate datasets; the previous mixed-stone style percentages are no longer used. This baseline is mostly stage one and is not a controlled comparison with later stages.', '',
             '| Style | No-stone occurrences / physical slots | Share of no-stone physical slots | Offers containing style / all no-stone offers |',
             '|---|---:|---:|---:|']
    for style, base in baseline['physical_style_counts'].items():
        if style in GATES and not base['style_slots']:
            continue
        lines.append(f"| {style} | {base['style_slots']} / {base['physical_slots']} | {percent(base['share_of_physical_slots'])} | {base['offers_containing_style']} / {base['eligible_offers']} |")
    lines += ['', '## Pair families', '', '| Pair family | Observed offers |', '|---|---:|']
    lines += [f'| {pair} | {count} |' for pair, count in overall['family_pair_counts'].items()]
    lines += ['', '## Mystery entries by the exact alternative', '',
              'A following interior with no established selection stays unlinked. For double mystery, either selected side is a mystery, so the side itself is unnecessary.', '',
              'Verified resource constraints (keys, affordability or Lockpick charges) are identified separately in the JSON. Rapid-combat cases whose key accounting could hide a door purchase remain unresolved; hovering alone never establishes selection.', '',
              '| Other choice | Offered pairs | Verified mystery entries | Unknown selection | Selected other | Selected mystery, content unknown | Unlinked interiors |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for name, stats in overall['mystery_by_exact_alternative'].items():
        lines.append(f"| {name} | {stats['offered_pairs']} | {stats['verified_selected_mystery_entries']} | {stats['unknown_selection']} | {stats['selected_other']} | {stats['selected_mystery_without_known_content']} | {stats['unlinked_following_entries']} |")
    physical = overall['mystery_by_alternative_family']['door']
    lines += ['', f"Aggregate mystery/physical-door: **{physical['offered_pairs']} offers**, **{physical['verified_selected_mystery_entries']} verified mystery entries**. "
              'Inspect the exact-alternative rows before interpreting this aggregate; it can consist entirely of wall outcomes.', '',
              '| Other choice | Verified initial contents |', '|---|---|']
    for name, stats in overall['mystery_by_exact_alternative'].items():
        if stats['verified_selected_mystery_entries']:
            details = ', '.join(f'{kind}: {count} ({percent(stats["initial_content_fractions"][kind])})'
                                for kind, count in stats['initial_contents'].items())
            lines.append(f'| {name} | {details} |')
    lines += ['', '## Separate stone datasets', '',
              'Spying, Lodestone and Crown Jewel exposures are separate from the normal-door baseline. Each dataset includes every offer with the stone, not only sightings of its special door. Coexisting stones are retained, and overlapping cohorts must not be summed. Gate-absent observations remain review anomalies; exact full-stone/source/stage strata are preserved.', '',
              '| Style / required stone | Slots with gate / physical slots with gate | Offers with style / all offers with gate | Style slots without gate |',
              '|---|---:|---:|---:|']
    for style, gate in overall['stone_gate_diagnostics'].items():
        yes, no = gate['gate_present'], gate['gate_absent']
        lines.append(f"| {style} / {gate['required_stone_under_current_rule']} | {yes['style_slots']} / {yes['physical_slots']} ({percent(yes['share_of_physical_slots'])}) | {yes['offers_containing_style']} / {yes['eligible_offers']} ({percent(yes['share_of_all_offers'])}) | {no['style_slots']} |")
    anomalies = [(style, ident) for style, gate in overall['stone_gate_diagnostics'].items()
                 for ident in gate['gate_absent_anomaly_ids']]
    if anomalies:
        lines += ['', 'Gate-absent anomaly IDs: ' + '; '.join(f'`{ident}` ({style})' for style, ident in anomalies) + '.']
    lines += ['', '## Limits', ''] + [f'- {text}' for text in result['limitations']]
    lines += ['', 'Exact source/version/stage/full-stone/run-class/theme/mode strata, no-stone counts, initial-content counts, unlinked-content counts and gate denominators are retained in the companion JSON.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', type=Path, default=Path('data/vods/observations-2026-10-08.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('docs/vod-door-context-analysis-2026-10-08.json'))
    parser.add_argument('--cohort-directory', type=Path,
                        help='Also export separate no_stones, spying, lodestone and devil JSONL datasets.')
    args = parser.parse_args()
    payload = args.observations.read_bytes()
    rows = [json.loads(line) for line in payload.decode('utf-8-sig').splitlines() if line.strip()]
    result = analyze(rows)
    result['inputs'] = {'observations': str(args.observations),
                        'observations_sha256': hashlib.sha256(payload).hexdigest()}
    if args.cohort_directory:
        result['exported_cohorts'] = export_cohorts(rows, args.cohort_directory, result['inputs']['observations_sha256'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(render(result), encoding='utf-8')
    overall = result['overall_descriptive']
    print(json.dumps({'report': str(args.output), 'eligible_offers': overall['eligible_offers'],
                      'physical_door_slots': overall['physical_door_slots'],
                      'family_pair_counts': overall['family_pair_counts'],
                      'gate_anomalies': {name: len(stats['gate_absent_anomaly_ids'])
                                         for name, stats in overall['stone_gate_diagnostics'].items()}}, indent=2))


if __name__ == '__main__':
    main()
