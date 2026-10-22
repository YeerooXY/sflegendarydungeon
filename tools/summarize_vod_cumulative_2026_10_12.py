"""Cumulative, row-level evidence audit. Never sum overlapping publication totals."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from sfld_vod.container_census_report import eligible, blessing
from summarize_vod_review_2026_10_12 import GENERATION_GEMS, PHYSICAL, PAIR_ORDER, interval, pair, wilson

ROOT = Path(__file__).resolve().parents[1]
NEW = 'data/vods/latest-event-review-2026-10-12/'
HISTORICAL = 'youtube-DCb80VikGM0'
INPUTS = {}


def read(name):
    p = ROOT / name
    INPUTS[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return ([json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
            if p.suffix == '.jsonl' else json.loads(p.read_text(encoding='utf-8-sig')))


def unique(rows, key):
    result = {}
    for r in rows:
        ident = r[key]
        if ident in result:
            raise ValueError(f'Duplicate {key}: {ident}')
        result[ident] = r
    return result


def brief(rows, predicate):
    n = len(rows)
    k = sum(bool(predicate(r)) for r in rows)
    return {'k': k, 'n': n, 'estimate': k / n if n else None, 'wilson_95': wilson(k, n)}


def stat(rows, field, value=True, clustered=True):
    known = [r for r in rows if r.get(field) is not None]
    s = (interval if clustered else brief)(known, lambda r: r[field] == value)
    s['unknown'] = len(rows) - len(known)
    s['eligible'] = len(rows)
    s['missingness_bounds'] = ([s['k'] / len(rows), (s['k'] + s['unknown']) / len(rows)]
                              if rows else None)
    return s


def normalize_offer(r, old=False):
    c = r['context'] if old else r
    return {'id': r['observation_id'] if old else r['id'], 'source_id': r['source_id'],
            'batch': 'earlier' if old else 'new', 'stage': c.get('stage'), 'room': c.get('room'),
            'gems': c.get('gems'), 'stones_known': c.get('stone_status') == 'verified' if old else c['stone_set_complete'],
            'run_class': c.get('run_class'), 'run_id': c.get('run_id'),
            'source_seconds': r.get('source_seconds') if old else r['start'],
            'doors': [{'kind': k} for k in r['offered_kinds']] if old else r['doors']}


def ordinary_new(r):
    return (r['status'] == 'decision' and not r.get('sequence_dedup_pending') and r['phase'] == 'ordinary'
            and ((r.get('room') is not None and 5 < r['room'] < 100 and r['room'] % 25 != 0)
                 or (r.get('room') is None and r.get('stage') in {2, 3, 4}))
            and len(r['doors']) == 2 and all(d['kind'] in PHYSICAL | {'mystery', 'monster', 'golden'} for d in r['doors']))


def neutral(r):
    return r['stones_known'] and not GENERATION_GEMS.intersection(r['gems'])


def normalize_content(value):
    return {'unknown': None, 'unresolved': None, 'crate': 'wooden_box', 'skeleton': 'corpse',
            'bronze_chest': 'chest', 'silver_chest': 'chest', 'bronze': 'chest',
            'silver': 'chest', 'cursed_barrel': 'barrel'}.get(value, value)


def offer_summary(rows, clustered=True):
    f = interval if clustered else brief
    physical = [dict(r, physical=d['kind']) for r in rows for d in r['doors'] if d['kind'] in PHYSICAL]
    return {'n': len(rows), 'sources': dict(Counter(r['source_id'] for r in rows)),
            'stages': dict(Counter(str(r['stage']) for r in rows)),
            'pairs': {k: f(rows, lambda r, k=k: pair(r) == k) for k in PAIR_ORDER},
            'physical_n': len(physical),
            'physical_styles': {k: f(physical, lambda r, k=k: r['physical'] == k) for k in sorted(PHYSICAL)}}


def content_summary(rows, clustered=True):
    known = [r for r in rows if r.get('content') is not None]
    return {'entries': len(rows), 'known_contents': len(known),
            'counts': dict(Counter(r['content'] for r in known)),
            'monster': stat(rows, 'content', 'monster', clustered),
            'categories': {k: stat(rows, 'content', k, clustered) for k in sorted({r['content'] for r in known})}}


def container_summary(rows, clustered=True):
    return {'openings': len(rows), 'styles': dict(Counter(r['style'] for r in rows)),
            'rewards': {k: stat(rows, k, clustered=clustered) for k in ('blessing', 'gold', 'item', 'curse')},
            'by_style': {style: stat([r for r in rows if r['style'] == style], 'blessing', clustered=False)
                         for style in sorted({r['style'] for r in rows})}}


def build():
    old_entries = read('data/vods/full-entry-linkage-2026-10-08.jsonl')
    new_candidates = read('data/vods/reviewed-offers-2026-10-12.jsonl')
    old_lookup = unique(old_entries, 'observation_id')
    new_lookup = unique(new_candidates, 'id')
    new_index = unique([dict(r, key=f"{r['source_id']}:{r['index']}") for r in new_candidates], 'key')
    old_sources = {r['source_id'] for r in old_entries}
    new_sources = {r['source_id'] for r in new_candidates}
    assert not old_sources & new_sources, 'Cross-pass source overlap requires temporal reconciliation'
    old_current = [normalize_offer(r, True) for r in old_entries if r['source_id'] != HISTORICAL]
    new_current = [normalize_offer(r) for r in new_candidates if ordinary_new(r)]
    current = old_current + new_current
    unique(current, 'id')
    historical = [normalize_offer(r, True) for r in old_entries if r['source_id'] == HISTORICAL]
    cohorts = {'all_stones_descriptive': current, 'no_known_generation_modifier': [r for r in current if neutral(r)],
               'no_stones': [r for r in current if r['stones_known'] and not r['gems']]}
    exact_groups = defaultdict(list)
    for r in current:
        exact_groups[','.join(sorted(r['gems'])) if r['stones_known'] else '(unknown)'].append(r)
    walls = [r for r in current if any(d['kind'] == 'wall' for d in r['doors'])]
    wall_types = {k: interval(walls, lambda r, k=k: any(d['kind'] == k for d in r['doors']))
                  for k in ['mystery', 'monster', 'golden']}
    variants = {}
    for gem, styles, target in [('spying', {'locked', 'unlocked'}, 'unlocked'),
                                ('lodestone', {'locked', 'double_locked'}, 'double_locked'),
                                ('devil', PHYSICAL, 'epic')]:
        rs = [r for r in current if r['stones_known'] and GENERATION_GEMS.intersection(r['gems']) == {gem}]
        doors = [dict(r, style=d['kind']) for r in rs for d in r['doors'] if d['kind'] in styles]
        variants[gem] = {'offers': len(rs), 'allowed_denominator_styles': sorted(styles),
                         'variant': stat(doors, 'style', target), 'summary': offer_summary(rs, False)}

    mystery = []
    for r in old_entries:
        if r['selected_kind'] != 'mystery':
            continue
        m = dict(normalize_offer(r, True), other=r['other_choice'], content=r['initial_content'] if r['initial_content_known'] else None,
                 attribution=r['selection_basis'])
        mystery.append(m)
    new_mystery = read(NEW + 'mystery-sequence-review.json')
    for r in new_mystery['rows']:
        if r['status'] != 'verified_mystery_entry':
            continue
        # Audits must refer to the same final corrected offer, not stale pre-review labels.
        offer = new_lookup[r['id']]
        assert [d['kind'] for d in r['reviewed_doors']] == [d['kind'] for d in offer['doors']]
        assert r['reviewed_phase'] == offer['phase']
        mystery.append(dict(normalize_offer(offer), other=r['reviewed_other_door'],
                            content={'bronze': 'chest', 'silver': 'chest'}.get(r['initial_contents'], r['initial_contents']),
                            observed_style=r['initial_contents'], attribution='native_transition'))
    unique(mystery, 'id')
    mystery_current = [r for r in mystery if r['source_id'] != HISTORICAL]
    mystery_groups = {}
    for other in ['wall', 'mystery', 'monster', 'physical_openable']:
        rs = [r for r in mystery_current if (r['other'] in PHYSICAL - {'wall'} if other == 'physical_openable' else r['other'] == other)]
        direct = [r for r in rs if r['attribution'] != 'reviewed_floor_object_excludes_visible_monster']
        mystery_groups[other] = {'all_attributed': content_summary(rs), 'route_verified_without_outcome_exclusion': content_summary(direct),
                                 'no_known_generation_modifier': content_summary([r for r in direct if neutral(r)]),
                                 'no_stones': content_summary([r for r in direct if r['stones_known'] and not r['gems']]),
                                 'by_batch': {b: content_summary([r for r in direct if r['batch'] == b], False) for b in ['earlier', 'new']},
                                 'attribution_counts': dict(Counter(r['attribution'] for r in rs))}
    # Door-content labels in the earlier ledger often resolve ONLY when a nonmonster
    # object is seen. Preserve them descriptively; never manufacture a 0% monster CI.
    physical_contents = {}
    for door in sorted(PHYSICAL - {'wall'}):
        rs = [r for r in old_entries if r['source_id'] != HISTORICAL and r['selected_kind'] == door and r['initial_content_known']]
        physical_contents[door] = {'attributed_interiors': len(rs), 'counts': dict(Counter(r['initial_content'] for r in rs)),
                                  'attribution': dict(Counter(r['selection_basis'] for r in rs)), 'unbiased_probability_not_established': True}

    trap_raw = read('data/vods/full-trap-blind-2026-10-08.jsonl')
    identities = unique(read('data/vods/full-trap-blind-identity-2026-10-08.jsonl'), 'observation_id')
    trap_report = read('docs/vod-full-reanalysis-2026-10-08.json')['traps']['prespecified_blind_review']
    traps = []
    for r in trap_raw:
        offer = old_lookup[r['observation_id']]
        sides = r['sides']
        resolved = any(x is True for x in sides) or all(x is False for x in sides)
        if resolved:
            assert identities[r['observation_id']]['status'] == 'same_visit_verified'
        traps.append(dict(normalize_offer(offer, True), reviewed_sides=sides,
                          trap=any(x is True for x in sides) if resolved else None))
    assert len([r for r in traps if r['trap'] is not None]) == trap_report['pair_presence_resolved']
    assert sum(r['trap'] is True for r in traps) == trap_report['trap_pairs']
    dense = read(NEW + 'trap-validation--dense-reviewed.json')
    assert dense['complete']
    for r in dense['rows']:
        if not r['eligible']:
            continue
        offer = new_index[f"{r['source_id']}:{r['index']}"]
        sides = [r['trap_left'], r['trap_right']]
        resolved = any(x is True for x in sides) or all(x is False for x in sides)
        traps.append(dict(normalize_offer(offer), reviewed_sides=sides,
                          trap=any(x is True for x in sides) if resolved else None))
    unique(traps, 'id')
    traps_current = [r for r in traps if r['source_id'] != HISTORICAL]
    trap_sides = [dict(r, kind=d['kind'], trap=value, side=index)
                  for r in traps_current for index, (d, value) in enumerate(zip(r['doors'], r['reviewed_sides']))]
    core_trap_sides = [r for r in trap_sides if r['kind'] in {'monster', 'mystery'}]

    old_containers = read('data/vods/full-container-rewards-2026-10-09.jsonl')
    unique(old_containers, 'observation_id')
    containers = []
    for r in old_containers:
        if r['source_id'] != HISTORICAL and eligible(r):
            containers.append({'id': r['observation_id'], 'source_id': r['source_id'], 'batch': 'earlier',
                               'gems': r['context']['gems'], 'stones_known': True, 'style': r['initial_style'],
                               'stage': r['context'].get('stage'), 'blessing': blessing(r),
                               **{k: r['reward_flags'].get(k) for k in ('gold', 'item', 'curse')}})
    new_container_audit = read(NEW + 'container-sequence-review.json')
    for r in new_container_audit['rows']:
        if not r['ordinary_opening_denominator']:
            continue
        containers.append({'id': new_index[r['id']]['id'], 'source_id': r['source_id'], 'batch': 'new',
                           'gems': r['context']['gems'], 'stones_known': r['context']['gem_set_complete_in_frozen_offer'],
                           'stage': r['context']['stage'], 'style': 'wooden_box' if r['container'] == 'crate' else r['container'],
                           **{k: r['outcomes'][k] for k in ('blessing', 'gold', 'item', 'curse')}})
    unique(containers, 'id')
    boxes = [r for r in containers if r['style'] in {'wooden_box', 'bronze', 'silver'}]
    container_groups = {}
    for name, filt in [('all_stones_descriptive', lambda r: True), ('without_hero', lambda r: 'hero' not in r['gems'])]:
        known = [r for r in boxes if r['stones_known'] and filt(r)]
        container_groups[name] = {label: container_summary([r for r in known if ('greed' in r['gems']) == greed])
                                  for label, greed in [('without_greed', False), ('with_greed', True)]}
    container_groups['unknown_stones'] = container_summary([r for r in boxes if not r['stones_known']])
    container_groups['new_corpse_searches_separate'] = container_summary([r for r in containers if r['style'] == 'corpse'])

    # Follow-ups replace unreviewed inventory placeholders; they are never added to
    # an already-reviewed version of the same observation.
    barrel_followup = read('data/vods/barrel-followup-2026-10-12.jsonl')
    barrels_old = [r for r in old_containers if r['initial_style'] == 'barrel' and r['reward_review_status'].startswith('verified')]
    unique(barrels_old + barrel_followup, 'observation_id')
    barrels = []
    for r in barrels_old + barrel_followup:
        if (r['source_id'] != HISTORICAL and r['interaction'] == 'opened' and r['reward_complete']
                and r['context'].get('stone_status') == 'verified'):
            barrels.append({'id': r['observation_id'], 'source_id': r['source_id'], 'gems': r['context']['gems'],
                            'blessing': blessing(r), 'curse': r['reward_flags'].get('curse'), 'batch': 'earlier'})
    for r in new_container_audit['rows']:
        if r['barrel_denominator'] and r['context']['gem_set_complete_in_frozen_offer']:
            barrels.append({'id': new_index[r['id']]['id'], 'source_id': r['source_id'], 'gems': r['context']['gems'],
                            'blessing': r['outcomes']['blessing'], 'curse': r['outcomes']['curse'], 'batch': 'new'})
    unique(barrels, 'id')
    barrel_groups = defaultdict(list)
    for r in barrels:
        barrel_groups[','.join(sorted(set(r['gems']) & {'gambler', 'time_traveler', 'thirsty'})) or 'unmodified'].append(r)

    spins = []
    old_spins = read('data/vods/destiny-followup-2026-10-12.jsonl')
    for r in old_spins:
        if r['interaction'] == 'opened' and r['wheel_reward_complete']:
            spins.append({'id': r['observation_id'], 'source_id': r['source_id'], 'reward': r['wheel_reward'],
                          'content': r['initial_contents'], 'batch': 'earlier'})
    new_spins = read(NEW + 'spinner-sequence-review.json')
    for r in new_spins['rows']:
        if r['included_in_primary_rates'] and r['status'] == 'selected':
            spins.append({'id': r['observation_id'], 'source_id': r['source_id'], 'reward': r['spin_reward']['category'],
                          'content': r['initial_contents'], 'batch': 'new'})
    unique(spins, 'id')
    for r in spins:
        r['reward'] = {'coins': 'lucky_coins', 'hourglass': 'hourglasses'}.get(r['reward'], r['reward'])
        r['content'] = normalize_content(r['content'])
    arena = read('data/vods/full-arena-rewards-2026-10-08.jsonl')
    unique(arena, 'id')
    arena_current = [r for r in arena if r['source_id'] != HISTORICAL]
    five = [r for r in arena_current if r['completed_depth'] == 5 and r['reward']['complete'] and r['reward']['state'] == 'observed']
    for r in five:
        cs = r['reward']['components']
        assert len(cs) == 1
        r['reward_kind'] = cs[0].get('rarity') if cs[0]['kind'] == 'item' else cs[0]['kind']

    old_shops = read('docs/vod-shop-timing-2026-10-09.json')
    new_decisions = [r for r in new_candidates if r['status'] == 'decision' and not r.get('sequence_dedup_pending')]
    new_shops = [r for r in new_decisions if r['phase'] == 'shop' or any(d['kind'] == 'shop' for d in r['doors'])]
    shops = {}
    for stage in [1, 2, 3, 4]:
        previous = old_shops['current_event']['by_stage'][str(stage)]
        new = [r for r in new_shops if r.get('stage') == stage]
        hist = Counter({int(k): v for k, v in previous['room_counts'].items()})
        hist.update(r['room'] for r in new if r.get('room') is not None)
        shops[str(stage)] = {'visits': previous['visits'] + len(new), 'exact_room_count': sum(hist.values()),
                             'room_counts': dict(sorted(hist.items())), 'observed_min': min(hist) if hist else None,
                             'observed_max': max(hist) if hist else None}

    # A handful of frozen reference totals catch accidental summing of overlapping
    # older publications, cohort drift, or treating unknowns as failures.
    assert len(old_current) == 4976 and len(new_current) == 1562 and len(historical) == 728
    assert mystery_groups['wall']['route_verified_without_outcome_exclusion']['monster']['n'] == 163
    assert mystery_groups['wall']['route_verified_without_outcome_exclusion']['monster']['k'] == 142
    assert mystery_groups['mystery']['route_verified_without_outcome_exclusion']['monster']['n'] == 545
    assert sum(r['trap'] is True for r in traps_current) == 54
    assert len([r for r in traps_current if r['trap'] is not None]) == 213
    assert len(spins) == 73 and len(five) == 48
    assert sum(r['content'] is not None for r in spins) == 72
    return {'schema': 1, 'date': '2026-10-12', 'scope': 'Cumulative reviewed evidence; September 2026 current event and 2024 historical source separated.',
            'inputs_sha256': INPUTS, 'overlap_audit': {'source_intersection': sorted(old_sources & new_sources),
              'earlier_sources': sorted(old_sources), 'new_reviewed_sources': sorted(new_sources),
              'method': 'One latest canonical ledger per question; unique observation IDs; old/new source sets disjoint; barrel follow-ups do not recount verified openings.'},
            'coverage': {'earlier_current_ordinary_offers': len(old_current), 'added_current_ordinary_offers': len(new_current),
              'current_ordinary_offers': len(current), 'current_sources': len({r['source_id'] for r in current}),
              'current_complete_stones': sum(r['stones_known'] for r in current), 'historical_ordinary_offers': len(historical),
              'new_retained_decisions_all_phases': len(new_decisions), 'new_candidates': len(new_candidates),
              'new_strict_exact_room_and_stones': sum(r['stones_known'] and r['room'] is not None for r in new_current),
              'new_additional_stage_known_room_unknown': sum(r['room'] is None for r in new_current),
              'new_stones_unknown': sum(not r['stones_known'] for r in new_current)},
            'generation_modifier_exclusions': sorted(GENERATION_GEMS),
            'offers': {k: offer_summary(rs) for k, rs in cohorts.items()},
            'offers_by_batch': {k: offer_summary(rs, False) for k, rs in [('earlier', old_current), ('new', new_current)]},
            'offers_by_stage_no_generation_modifiers': {str(s): offer_summary([r for r in cohorts['no_known_generation_modifier'] if r['stage'] == s], False) for s in [1, 2, 3, 4]},
            'exact_stone_strata': {k or '(none)': offer_summary(rs, False) for k, rs in sorted(exact_groups.items())},
            'isolated_generation_variants': variants, 'walls': {'appearances': len(walls), 'other_choice': wall_types},
            'mystery': mystery_groups, 'mystery_new_sample_status': dict(Counter(r['status'] for r in new_mystery['rows'])),
            'physical_contents_earlier_attributed_not_unbiased': physical_contents,
            'traps': {'combined': stat(traps_current, 'trap'),
              'per_individual_monster_or_mystery_option': stat(core_trap_sides, 'trap'),
              'per_individual_offered_position_including_walls': stat(trap_sides, 'trap'),
              'by_room_kind': {kind: stat([r for r in trap_sides if r['kind'] == kind], 'trap')
                               for kind in sorted({r['kind'] for r in trap_sides})},
              'by_batch': {b: stat([r for r in traps_current if r['batch'] == b], 'trap') for b in ['earlier', 'new']},
              'by_pair': {k: stat([r for r in traps_current if pair(r) == k], 'trap', clustered=False) for k in PAIR_ORDER}},
            'containers': container_groups,
            'barrels': {k: {'blessing': stat(rs, 'blessing'), 'curse': stat(rs, 'curse')} for k, rs in sorted(barrel_groups.items())},
            'spinner': {'selected_spins': len(spins), 'rewards': {k: stat(spins, 'reward', k) for k in ['blessing', 'curse', 'lucky_coins', 'gold', 'hourglasses', 'mushrooms']},
                        'contents': content_summary(spins), 'unselected_offers': len(old_spins) + len(new_spins['rows']) - len(spins)},
            'arena': {'current_sequences': len(arena_current), 'five_gate_opened_known': len(five),
                      'rewards': {k: stat(five, 'reward_kind', k) for k in ['gold', 'arcane_splinters', 'epic', 'legendary']},
                      'new_verified_five_gate_payouts': 0},
            'shops': {'by_stage': shops, 'new_visits': len(new_shops), 'earlier_visits': old_shops['current_event']['visits'],
                      'earlier_complete_stage1_passages': old_shops['stage1_counter_complete_summary']},
            'historical_separate': {'source': HISTORICAL, 'offers': offer_summary(historical, False),
                 'mystery': content_summary([r for r in mystery if r['source_id'] == HISTORICAL], False)},
            'limitations': ['95% Wilson intervals are pointwise binomial sampling intervals, not guarantees of exact hidden game constants.',
              'Whole-source bootstrap ranges are descriptive sensitivity checks; sources can share streamers and characters and are not proven independent.',
              'Recorded windows, recognition coverage, player choices, event-run/mode uncertainty and missing outcomes are not corrected by a narrow interval.',
              'Mixed-stone overall estimates describe this footage mix; use stratified cohorts when calibrating an unmodified simulator.',
              'Unknown outcome labels remain unknown. Outcome-inferred entry routes cannot independently verify monster exclusion.',
              'New broad offer census includes ordinary decisions with exact room unknown but stage 2-4 established; three stage-and-room-unknown offers remain excluded.',
              'Sources are disjoint across passes; absolute completeness of every fast room or repeated view is not guaranteed.']}


def pct(x):
    if x is None:
        return 'unknown'
    return f'{100*x:.2f}%' if 0 < x < .01 else f'{100*x:.1f}%'


def ci(s, key='wilson_95'):
    if not s.get(key):
        return 'not estimable'
    digits = 2 if s[key][1] < .01 else 1
    return f'{100*s[key][0]:.{digits}f}-{100*s[key][1]:.{digits}f}%'


def table(lines, title, items, source_range=False):
    lines += ['', '## ' + title, '', '| Outcome / condition | Observed | Estimate | 95% Wilson interval |' + (' Source-bootstrap range |' if source_range else ''),
              '|---|---:|---:|---:|' + ('---:|' if source_range else '')]
    for label, s in items:
        lines.append(f"| {label} | {s['k']}/{s['n']} | {pct(s['estimate'])} | {ci(s)} |" + (f" {ci(s, 'source_cluster_bootstrap_95')} |" if source_range else ''))


def render(r):
    c = r['coverage']; offers = r['offers']; m = r['mystery']
    lines = ['# Cumulative Legendary Dungeon evidence, 12 October 2026', '',
      f"The **24** mystery entries beside a wall in the new-pass report were additions. Earlier current-event footage contained **139** known interiors: together **142 monsters / 163 known interiors (87.1%)**. These are separate from the **61 visible monster + wall offers** and **354 total wall appearances**.", '',
      f"The cumulative current-event offer census contains **{c['current_ordinary_offers']:,} ordinary choices** ({c['earlier_current_ordinary_offers']:,} earlier + {c['added_current_ordinary_offers']:,} added) from **{c['current_sources']} VODs**. **{c['current_complete_stones']:,}** have complete stone context. The **{c['historical_ordinary_offers']}** ordinary choices from the 2024 video remain separate.", '',
      'This publication supersedes new-pass totals when asking how much evidence exists overall. It does not add together overlapping earlier reports. Canonical observation IDs, independent old/new source sets, input SHA-256 hashes, unresolved counts and source-level totals are preserved in [the reproducible JSON](vod-cumulative-2026-10-12.json). No simulator defaults changed in this aggregation.', '',
      '## Confidence and coverage', '',
      'The 95% intervals below quantify binomial sampling uncertainty **within the stated cohort**. They are not a 95% guarantee about a universal game constant. Whole-VOD bootstrap ranges resample sources 10,000 times as a sensitivity check; several VODs can share streamers and characters. Neither method removes player selection, missed animations, unknown run/mode effects, or labeling errors. Intervals are pointwise, not a simultaneous 95% claim across every row.', '',
      f"The previous new-pass headline used {c['new_strict_exact_room_and_stones']:,} offers with exact room and stones known. This cumulative census also includes **{c['new_additional_stage_known_room_unknown']}** ordinary offers with exact room unreadable but stage 2-4 established, plus **{c['new_stones_unknown']}** offers with unreadable stones for mixed-footage counts only. Three offers with both stage and room unknown stay excluded. Opening rooms 1-5, shops, bosses, trial subrounds, repeated views, and pending duplicate resolutions are excluded.", '',
      'A confidence interval uses the number of *eligible, resolved outcomes*, not the number of videos, detected frames, or all room choices. Unknown rewards never become zero rewards.']
    table(lines, 'Overall room-choice pairs: all observed stones', [(k.replace('/', ' + '), s) for k, s in offers['all_stones_descriptive']['pairs'].items()])
    lines += ['', 'Here “door” includes locked, cursed, sacrifice, hungry, destiny, blessing, walls and stone-enabled variants. This table describes the actual mixed-stone footage; it is not the unmodified generation pool.']
    table(lines, 'Wall appearances: the other offered choice', [(k, s) for k, s in r['walls']['other_choice'].items()])
    lines += ['', 'Denominator: all 354 ordinary wall appearances. The 225 wall + mystery offers are opportunities; 163 have a resolved mystery interior. Known entry with unreadable contents and unknown entry routes are not counted as monsters or empty rooms.']
    table(lines, 'Door-pair baseline: no known generation modifier', [(k.replace('/', ' + '), s) for k, s in offers['no_known_generation_modifier']['pairs'].items()])
    lines += ['', f"This cohort contains **{offers['no_known_generation_modifier']['n']:,}** offers. Excluded stone codes: {', '.join(r['generation_modifier_exclusions'])}. Greasy is conservatively excluded for its chest-generation effect; this is stricter than the October 8 baseline. Other combat/effect stones may remain. Exact stone sets and stage strata are in the JSON."]
    table(lines, 'Physical door styles in that baseline', [(k, s) for k, s in offers['no_known_generation_modifier']['physical_styles'].items() if k not in {'unlocked', 'double_locked', 'epic'}])
    lines += ['', 'Denominator: physical alternatives including walls, not all screen positions. Hungry-resource subtypes are combined. Unlocked/double-locked/epic variants are excluded from this table and measured only in their stone cohorts.']
    table(lines, 'Stone-enabled styles, isolated generation modifier', [(k, v['variant']) for k, v in r['isolated_generation_variants'].items()])
    lines += ['', 'Spying: unlocked / (locked + unlocked). Lodestone: double-locked / (locked + double-locked). Devil: epic / all physical alternatives. Other generation-modifying stones are absent; other stones may remain. Empty strata have no estimate.']
    table(lines, 'Monsters behind a mystery choice: verified routes', [(k, v['route_verified_without_outcome_exclusion']['monster']) for k, v in m.items()], True)
    lines += ['', '“physical_openable” means a mystery beside a locked or special physical door, excluding a wall. It is a small, selected entry sample. The prior 86 mystery/visible-monster object attributions relied on the revealed nonmonster object to resolve the route; they are preserved in JSON but **excluded from the independent 0/3 test**. Counting them as evidence for zero monsters would be circular.', '',
      'The wall row also has two earlier known entries with unreadable contents. Its 163 resolved interiors are drawn from 165 known entries. Mystery/mystery does not require knowing left versus right, since either choice is a mystery.']
    table(lines, 'Mystery contents: double mystery', list(m['mystery']['route_verified_without_outcome_exclusion']['categories'].items()))
    table(lines, 'Mystery contents: beside a wall', list(m['wall']['route_verified_without_outcome_exclusion']['categories'].items()))
    table(lines, 'Mystery monster odds without known generation modifiers', [(k, m[k]['no_known_generation_modifier']['monster']) for k in ['mystery', 'wall']], True)
    lines += ['', 'Chest combines bronze/silver/unresolved chest styles so the older and newer taxonomies agree. A sleeping corpse is separate from a living monster on entry; later awakening does not relabel the initial contents.']
    table(lines, 'Traps per individual room option',
          [('monster or mystery option', r['traps']['per_individual_monster_or_mystery_option']),
           ('monster option', r['traps']['by_room_kind']['monster']),
           ('mystery option', r['traps']['by_room_kind']['mystery']),
           ('all positions, including physical doors and walls', r['traps']['per_individual_offered_position_including_walls'])], True)
    lines += ['', 'The useful per-room denominator is readable individual monster/mystery options: 54/242, with eight additional unreadable options. Counting all screen positions gives a lower rate because that includes physical doors and walls. These are offered-option frequencies, not the rate among rooms a player chose to enter. A pair with one unreadable side can still supply one individually readable option; therefore do not simply divide the old pair percentage by two.', '',
              'Golden options stay separate (0/7 readable, five unreadable). Official help lists golden-door traps, which conflicts with the current working rule; seven negatives cannot establish a zero chance. This is an unresolved eligibility question, not a reason to relabel any observation. See the [generator hypothesis analysis](ROOM_GENERATION_2026_10_12.md).']
    table(lines, 'Traps per offered pair: secondary diagnostic', [('all current-event verified samples', r['traps']['combined'])] + [(k + ' batch', s) for k, s in r['traps']['by_batch'].items()], True)
    lines += ['', f"There are **{r['traps']['combined']['unknown']} unresolved earlier sampled pairs**, retained outside the resolved denominator. New negatives were checked across dense temporal sequences. The earlier sample was hash-random over its offer inventory; the new sample balanced sources. The pooled result describes that combined design, not an equal-chance census of all 6,538 offers. This is a *per-pair presence rate*, not the simulator's per-eligible-door `trap_chance=0.29`."]
    table(lines, 'Crate / bronze / silver chest blessings', [(label.replace('_', ' '), s['rewards']['blessing']) for label, s in r['containers']['without_hero'].items()], True)
    lines += ['', 'Hero is excluded from this comparison; known stone sets are required. Corpse searches and barrels are separate. Greed groups have different players/stages/choices: the difference is descriptive, not a randomized estimate of an additive versus relative modifier. One blessing outcome remains unknown in each older Greed group.']
    for label, s in r['containers']['without_hero'].items():
        table(lines, 'Container blessings by style: ' + label.replace('_', ' '), list(s['by_style'].items()))
    table(lines, 'Other container outcomes, Hero excluded', [(label.replace('_', ' ') + ': ' + key, group['rewards'][key]) for label, group in r['containers']['without_hero'].items() for key in ['gold', 'item', 'curse']])
    lines += ['', 'These are overlapping binary reward types: one opening can yield more than one. Gold/item negatives require readable post-opening evidence; denominator differences reflect missing labels. The new “none of four audited types” label is not equivalent to the older “nothing” label, so they are not pooled. Ordinary epic/legendary rates remain unestablished because some item rarities are unreadable.']
    table(lines, 'Barrel blessings, by modifier', [(k, v['blessing']) for k, v in r['barrels'].items()], True)
    lines += ['', 'Unmodified excludes Gambler, Time Traveler and Thirsty. The old barrel inventory plus the earlier follow-up is counted once; the new 18 openings are additions. Two successful Gambler openings do not establish a 100% chance.']
    table(lines, 'Destiny spinner rewards', list(r['spinner']['rewards'].items()), True)
    table(lines, 'Rooms behind the spinner', list(r['spinner']['contents']['categories'].items()))
    lines += ['', '73 primary selected spins: 12 earlier + 61 new. Unselected offers and incidental targeted reward examples are excluded. One post-spin interior is unreadable. These spinner probabilities should not be substituted for ordinary mystery or locked-door interiors.']
    table(lines, 'Five-gate trial chest rewards', list(r['arena']['rewards'].items()), True)
    lines += ['', '48 readable current-event final-tier chests, unchanged by the new pass: 19 gold, 20 arcane splinters, 6 epic, 3 legendary. These are **conditional on reaching/opening the final chest**, not per trial attempt. A 10% legendary chance remains compatible; the exact chance is not established.', '',
      '## Shops and remaining gaps', '', '| Stage | Observed shops | Exact room readable | Observed room span |', '|---|---:|---:|---|']
    for stage, s in r['shops']['by_stage'].items():
        lines.append(f"| {stage} | {s['visits']} | {s['exact_room_count']} | {s['observed_min']}-{s['observed_max']} |")
    lines += ['', 'Room spans are observed extrema, not a 95% guarantee of the generator bounds. Complete first/later event-run denominators are still missing; stage-one shop presence must not be inferred from a video starting at room one.', '',
      '- **Other doors:** the JSON preserves all older attributed interiors for locked, cursed, sacrifice, hungry, unlocked and other styles. Many routes resolve only when a distinctive object appears; a monster frequency or narrow confidence range from those selected objects would be misleading. They need outcome-independent click/transition review.',
      '- **Golden subtypes:** pair appearances are counted above, but all fountain/spider/lantern/trader/trial outcomes do not yet have a comparable complete denominator.',
      '- **Run and mode:** most first-event-run versus later-run and regular versus golden-mode labels remain unknown. Do not fit their container or room probabilities together as if equivalent.',
      '- **Effect details:** isolated stone modifiers, shop strength/reroll distribution, corpse awakening, fountain curse removal and exact spinner reward amounts need more verified sequences.', '',
      'The cumulative evidence continues to support different content pools for double mystery and mystery beside a wall. Container blessing frequency is higher with Greed in these observations, but the exact modifier formula remains uncertain. An equal six-way spinner reward distribution fits these observations poorly: blessings and curses dominate. A 50% unmodified barrel blessing chance remains compatible with the cumulative interval. These are evidence assessments, not automatic profile changes.', '',
      '## Reproduction', '', '`python tools/summarize_vod_cumulative_2026_10_12.py`', '',
      'Inputs are the latest canonical row ledgers and saved completed-pass audits, with hashes in the JSON. Older intermediate reports are never added as new observations. The 2024 video remains a separate comparison. [The preceding report](VOD_REVIEW_2026_10_12.md) describes only the latest collection/review increment.']
    return '\n'.join(lines) + '\n'


def main():
    report = build()
    (ROOT / 'docs/vod-cumulative-2026-10-12.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'docs/VOD_CUMULATIVE_2026_10_12.md').write_text(render(report), encoding='utf-8')
    print(json.dumps({'coverage': report['coverage'], 'wall_mystery': report['mystery']['wall']['route_verified_without_outcome_exclusion']['monster'],
                      'traps': report['traps']['combined']}, indent=2))


if __name__ == '__main__':
    main()
