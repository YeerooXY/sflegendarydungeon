"""Inventory every source's container evidence without inventing unopened loot draws."""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLANS = (ROOT / 'out/vods/full-review-plan.json', ROOT / 'out/vod-discovery/catalog-plan.json')
ALIASES = {
    'crate': 'wooden_box', 'wooden_box': 'wooden_box', 'chest': 'chest_unknown',
    'bronze': 'bronze', 'bronze_chest': 'bronze', 'silver': 'silver', 'silver_chest': 'silver',
    'epic': 'epic_chest', 'epic_chest': 'epic_chest', 'golden_chest': 'golden_chest',
    'gold_chest': 'golden_chest', 'barrel': 'barrel', 'corpse': 'corpse', 'skeleton': 'corpse',
    'corpse_awakens': 'corpse', 'corpse_then_monster': 'corpse', 'mimic': 'mimic',
    'silver_then_mimic': 'silver', 'sacrifice_chest': 'sacrifice_chest',
    'sated_chest': 'sated_chest', 'cursed_chest': 'cursed_chest', 'locked_chest': 'locked_chest',
    'chained_chest': 'chained_chest',
}
ORDINARY = {'wooden_box', 'bronze', 'silver'}
FLAGS = ('gold', 'nothing', 'persistent_blessing', 'instant_healing', 'item', 'epic_item', 'legendary_item', 'curse')


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_uniform_candidates(rows, count=128, seed='sfld-container-uniform-audit-2026-10-08-v1'):
    """Select before viewing rewards, independently of input order or detector scores."""
    pool = [r for r in rows if r['reward_review_status'] == 'unreviewed']
    if not 0 <= count <= len(pool):
        raise ValueError('Sample size exceeds the remaining candidate population.')
    key = lambda r: hashlib.sha256((seed + '|' + r['observation_id']).encode()).hexdigest()
    selected = copy.deepcopy(sorted(pool, key=key)[:count])
    for row in selected:
        row['sampling_hash'] = key(row)
    return {'schema': 1, 'selection': 'Lowest SHA256(seed|observation_id) among still reward-unreviewed appearance candidates; no outcome-based replacement.',
            'seed': seed, 'sampling_population': len(pool), 'maximum_seconds': 12, 'fps': 8, 'rows': selected}


def public_evidence(evidence):
    keys = ('sha256', 'source_seconds', 'decoded_source_seconds', 'role', 'video_sha256', 'native_height')
    return [{k: e[k] for k in keys if k in e} for e in evidence]


def container_label(observation):
    outcome = observation.get('outcome') or {}
    initial = outcome.get('initial_content')
    following = outcome.get('following_scene_observation')
    detail = outcome.get('initial_detail')
    if initial in ALIASES:
        style = ALIASES[initial]
        if style == 'chest_unknown':
            # A later scene can establish an object's visible style, never the item's rarity.
            named = ALIASES.get(detail, ALIASES.get(following))
            if named in {'bronze', 'silver', 'epic_chest', 'golden_chest', 'sacrifice_chest', 'sated_chest', 'cursed_chest', 'locked_chest', 'chained_chest'}:
                style = named
        verified = 'initial_content' in observation.get('verified_fields', [])
        return style, 'verified_initial_content' if verified else 'unverified_initial_label'
    if following in ALIASES:
        return ALIASES[following], 'following_scene_candidate'
    return None, None


def inventory(plans=DEFAULT_PLANS, additional_appearances=None):
    result, sources, excluded = [], [], collections.Counter()
    seen = set()
    for plan in map(Path, plans):
        for source in json.loads(plan.read_text(encoding='utf-8-sig'))['sources']:
            path = plan.parent / source['observations']
            observations = read_rows(path)
            decisions = sorted((r for r in observations if r.get('status') == 'decision'), key=lambda r: r['source_seconds'])
            source_summary = {'source_id': source['source_id'], 'ledger': path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                              'ledger_sha256': sha256(path), 'records': len(observations), 'decisions': len(decisions), 'candidates': 0,
                              'verified_initial_container': 0, 'entry_unknown': 0, 'complete_temporal_recall_established': False}
            for index, obs in enumerate(decisions):
                ident = obs['id']
                if ident in seen:
                    raise ValueError(f'Duplicate canonical decision: {ident}')
                seen.add(ident)
                style, basis = container_label(obs)
                if style is None and ident in (additional_appearances or {}):
                    style = additional_appearances[ident]
                    basis = 'independent_native_container_audit'
                outcome = obs.get('outcome') or {}
                if style is None:
                    reason = 'known_noncontainer_entry' if outcome.get('initial_content') is not None else 'entry_unclassified'
                    excluded[reason] += 1
                    source_summary['entry_unknown'] += reason == 'entry_unclassified'
                    continue
                source_summary['candidates'] += 1
                source_summary['verified_initial_container'] += basis == 'verified_initial_content'
                end = obs.get('source_end', (obs.get('source_interval') or [obs['source_seconds']] * 2)[-1])
                next_offer = decisions[index + 1]['source_seconds'] if index + 1 < len(decisions) else end + 12
                evidences = (outcome.get('evidence') or []) + obs.get('evidence', [])
                unique = {}
                for ev in evidences:
                    t = ev.get('source_seconds')
                    if ev.get('image') and t is not None and end <= t < next_offer:
                        unique[(ev['image'], t)] = ev
                row = {'schema': 1, 'observation_id': ident, 'source_id': source['source_id'],
                       'source_start': obs['source_seconds'], 'entry_window_start': end, 'entry_window_end': next_offer,
                       'context': copy.deepcopy(obs.get('context') or {}), 'doors': copy.deepcopy(obs.get('doors') or []),
                       'generation': obs.get('generation'), 'initial_style': style, 'appearance_basis': basis,
                       'interaction': 'unknown', 'reward_review_status': 'unreviewed', 'ordinary_loot_eligible': None,
                       'rewards': [], 'reward_flags': {k: None for k in FLAGS}, 'reward_complete': False,
                       'item_rarity': None, 'evidence': public_evidence(unique.values()), 'notes': [],
                       '_base': str(plan.parent), '_cached_evidence': sorted(unique.values(), key=lambda e: e['source_seconds'])}
                result.append(row)
            sources.append(source_summary)
    return result, {'sources': sources, 'excluded_decisions': dict(excluded)}


def apply_review(row, review, origin):
    """Import a visit audit. A partial reward observation cannot establish negative flags."""
    row['reward_review_status'] = 'verified_native_sequence' if origin == 'prior_complete' else 'verified_partial_native_sequence'
    row['review_origin'] = origin
    row['initial_style'] = ALIASES.get(review.get('initial_style'), review.get('initial_style', row['initial_style']))
    row['interaction'] = review.get('interaction', 'opened')
    if row['interaction'] == 'not_container':
        row['appearance_basis'] = 'native_review_rejects_legacy_container_label'
    elif row['interaction'] != 'unknown':
        row['appearance_basis'] = 'verified_native_container_sequence'
    row['rewards'] = copy.deepcopy(review.get('rewards', []))
    row['reward_complete'] = origin == 'prior_complete' and row['interaction'] == 'opened'
    row['ordinary_loot_eligible'] = row['interaction'] == 'opened' and row['initial_style'] in ORDINARY
    row['reward_flags'] = {k: None for k in FLAGS}
    kinds = {e['kind'] for e in row['rewards']}
    if row['reward_complete']:
        for key in ('gold', 'nothing', 'item', 'curse'):
            row['reward_flags'][key] = key in kinds or (key == 'curse' and 'curse_refresh' in kinds)
    else:
        for key in ('gold', 'nothing', 'item', 'curse'):
            if key in kinds:
                row['reward_flags'][key] = True
    if row['interaction'] == 'opened':
        row['reward_flags']['persistent_blessing'] = review.get('persistent_blessing')
        row['reward_flags']['instant_healing'] = review.get('instant_healing')
    # A recognizable ornate chest is not proof of epic/legendary item rarity.
    if row['reward_flags']['item'] is False:
        row['reward_flags']['epic_item'] = False
        row['reward_flags']['legendary_item'] = False
    row['evidence'] += public_evidence(review.get('evidence', []))
    row['notes'] += review.get('notes', [])


def merge_reviews(rows, complete, supplemental, manual=()):
    by_id = {r['observation_id']: r for r in rows}
    seen = set()
    for review in complete:
        ident = review['observation_id']
        if ident in seen:
            raise ValueError('Duplicate prior container visit.')
        seen.add(ident)
        if ident not in by_id:
            # Supplemental evidence can identify a previously unlabeled entry.
            raise ValueError(f'Prior reviewed visit missing from canonical inventory: {ident}')
        apply_review(by_id[ident], review, 'prior_complete')
    for review in supplemental:
        ident = review['observation_id']
        if ident in seen:
            raise ValueError('Supplemental reward duplicates a complete visit; merge explicitly.')
        seen.add(ident)
        if ident not in by_id:
            raise ValueError(f'Supplemental visit missing from canonical inventory: {ident}')
        reward = review['reward']
        rewards = []
        if reward.get('persistent_blessing'):
            rewards.append({'kind': 'persistent_blessing', 'identity': reward['persistent_blessing']})
        if reward.get('item_reward'):
            rewards.append({'kind': 'item', 'rarity': reward.get('item_rarity')})
        normalized = {'initial_style': reward['container'], 'interaction': 'opened', 'rewards': rewards,
                      'persistent_blessing': True if reward.get('persistent_blessing') else None,
                      'instant_healing': None, 'evidence': review.get('evidence', []),
                      'notes': ['Partial native reward observation; unrelated reward absences were not verified.']}
        apply_review(by_id[ident], normalized, 'supplemental_partial')
    for review in manual:
        ident = review['observation_id']
        if ident in seen:
            raise ValueError(f'New review overlaps an imported visit: {ident}')
        seen.add(ident)
        apply_review(by_id[ident], review, 'new_complete' if review.get('complete') else 'new_partial')
        row = by_id[ident]
        row['reward_review_status'] = 'verified_native_sequence' if review.get('complete') else 'verified_partial_native_sequence'
        row['reward_complete'] = bool(review.get('complete')) and row['interaction'] in {'opened', 'mimic_defeated'}
        for key, value in review.get('reward_flags', {}).items():
            if key not in FLAGS or (value is not None and type(value) is not bool):
                raise ValueError('Invalid reviewed reward flag.')
            if not review.get('complete') and value is False:
                raise ValueError('A partial review cannot establish a negative reward flag.')
            row['reward_flags'][key] = value
        row['item_rarity'] = review.get('item_rarity')
        for field in ('item_rarity_basis', 'native_cohort', 'sequence_review', 'linkage_basis',
                      'identity_review', 'stone_context_review'):
            if field in review:
                row[field] = copy.deepcopy(review[field])
    return rows


def augment_entry_links(rows, links):
    """Join the separately reviewed entry supplement without mutating canonical ledgers."""
    by_id = {r['observation_id']: r for r in links}
    for row in rows:
        entry = by_id.get(row['observation_id'])
        if not entry:
            continue
        row['entry_linkage'] = {k: entry.get(k) for k in ('selected_side', 'selected_kind', 'selection_basis', 'other_choice', 'entry_link_status')}
        review = entry.get('new_review')
        if not review:
            continue
        row['evidence'] += public_evidence(review.get('evidence', []))
        if review.get('status') == 'content_conflict':
            row['initial_style'] = 'unknown'
            row['appearance_basis'] = 'native_review_rejects_legacy_container_label'
            row['notes'].append(review['reason'])
        elif entry.get('initial_content') in ALIASES:
            style = ALIASES[entry['initial_content']]
            if style != 'chest_unknown' or row['initial_style'] not in {'bronze', 'silver', 'epic_chest', 'golden_chest'}:
                row['initial_style'] = style
            row['appearance_basis'] = 'verified_native_floor_object'
    return rows


def validate(rows):
    seen = set()
    for r in rows:
        if r['observation_id'] in seen:
            raise ValueError('Duplicate container visit.')
        seen.add(r['observation_id'])
        if r['interaction'] not in {'opened', 'mimic_defeated'} and any(v is not None for v in r['reward_flags'].values()):
            raise ValueError('Unopened, unresolved mimic or unknown interaction cannot be a loot trial.')
        if r['reward_flags']['nothing'] is True:
            if not r['reward_complete'] or r['interaction'] != 'opened':
                raise ValueError('Nothing requires a completed, observed opening.')
            if any(r['reward_flags'][k] is True for k in FLAGS if k != 'nothing'):
                raise ValueError('Nothing conflicts with a positive reward.')
            if any(event['kind'] != 'nothing' for event in r['rewards']):
                raise ValueError('Nothing conflicts with another resource or reward event.')
        if r['reward_flags']['legendary_item'] is True and r.get('item_rarity') != 'legendary':
            raise ValueError('Legendary flag requires an explicit rarity review.')


def describe(rows):
    opened = [r for r in rows if r['interaction'] == 'opened']
    ordinary = [r for r in opened if r['ordinary_loot_eligible']]
    def flags(group):
        return {k: {'positive': sum(r['reward_flags'][k] is True for r in group),
                    'negative': sum(r['reward_flags'][k] is False for r in group),
                    'unknown': sum(r['reward_flags'][k] is None for r in group)} for k in FLAGS}
    return {'appearance_candidates': len(rows), 'initial_style': dict(collections.Counter(r['initial_style'] for r in rows)),
            'appearance_basis': dict(collections.Counter(r['appearance_basis'] for r in rows)),
            'reward_review_status': dict(collections.Counter(r['reward_review_status'] for r in rows)),
            'interaction': dict(collections.Counter(r['interaction'] for r in rows)),
            'opened': len(opened), 'ordinary_opened': len(ordinary), 'opened_reward_flags': flags(opened),
            'ordinary_opened_reward_flags': flags(ordinary),
            'opened_reward_event_kinds': dict(collections.Counter(event['kind'] for r in opened for event in r['rewards']))}


def grouped(rows, key):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def report(rows, coverage):
    validate(rows)
    cohort = [r for r in rows if r.get('native_cohort', {}).get('name') == 'source_balanced_82']
    random_cohort = [r for r in rows if r.get('native_cohort', {}).get('name') == 'uniform_remaining_128']
    return {'schema': 1, 'coverage': coverage, 'summary': describe(rows),
            'native_review_methodology': {
                'temporal_review': 'Native 8fps sequences followed continuously from the visible offer transition through the actual first departure; reward animations already visible before entry and later-room footage excluded.',
                'independent_original_identity_audits': sum(bool(r.get('linkage_basis')) for r in random_cohort),
                'identity_audit_scope': 'All five random-sample rejected container candidates compared against original canonical offer and following images using pair, room counter, HP, stones and object appearance.',
                'limitation': 'This is not an independent original-frame identity audit of every sampled row. Source timestamps alone are not identity proof.',
                'amounts': 'Resource icon and readable native amount reviewed separately; sale proceeds excluded from direct gold.',
                'rarity': '2026 readable equipment-name color matched to installed-client evidence; historical item rarity remains unknown without historical mapping.'},
            'uniform_native_cohort': {'selection': '128 lowest SHA256(seed|observation_id) among 1527 still reward-unreviewed appearance candidates; selected independently of detector scores, no outcome-based replacements.',
                'expected_visits': 128, 'reviewed_visits': len(random_cohort), 'summary': describe(random_cohort),
                'by_source': {k: describe(v) for k, v in grouped(random_cohort, lambda r: r['source_id']).items()},
                'by_style': {k: describe(v) for k, v in grouped(random_cohort, lambda r: r['initial_style']).items()},
                'by_greed': {k: describe(v) for k, v in grouped(random_cohort, lambda r: 'unknown' if r['context'].get('stone_status') != 'verified' else str('greed' in r['context'].get('gems', [])).lower()).items()},
                'by_full_stone_set': [{'gems': list(k), **describe(v)} for k, v in grouped(random_cohort, lambda r: tuple(sorted(r['context'].get('gems', []))) if r['context'].get('stone_status') == 'verified' else ('unknown',)).items()],
                'by_run_class': {str(k): describe(v) for k, v in grouped(random_cohort, lambda r: r['context'].get('run_class')).items()},
                'visits': [r['observation_id'] for r in sorted(random_cohort, key=lambda r: r['native_cohort']['index'])]},
            'fixed_native_cohort': {'selection': 'First 12 chronological previously reward-unreviewed initial crate/chest appearances per source, all available if fewer; selected before reviewing outcomes, excluding three incidental positive audits.',
                'expected_visits': 82, 'reviewed_visits': len(cohort), 'summary': describe(cohort),
                'by_source': {k: describe(v) for k, v in grouped(cohort, lambda r: r['source_id']).items()},
                'by_style': {k: describe(v) for k, v in grouped(cohort, lambda r: r['initial_style']).items()},
                'by_greed': {k: describe(v) for k, v in grouped(cohort, lambda r: 'unknown' if r['context'].get('stone_status') != 'verified' else str('greed' in r['context'].get('gems', [])).lower()).items()},
                'by_full_stone_set': [{'gems': list(k), **describe(v)} for k, v in grouped(cohort, lambda r: tuple(sorted(r['context'].get('gems', []))) if r['context'].get('stone_status') == 'verified' else ('unknown',)).items()],
                'by_run_class': {str(k): describe(v) for k, v in grouped(cohort, lambda r: r['context'].get('run_class')).items()},
                'visits': [r['observation_id'] for r in sorted(cohort, key=lambda r: r['native_cohort']['index'])]},
            'mimic_combat_rewards': [{k: r[k] for k in ('observation_id', 'rewards', 'item_rarity', 'reward_flags', 'evidence')} for r in rows if r['interaction'] == 'mimic_defeated'],
            'by_source': {k: describe(v) for k, v in grouped(rows, lambda r: r['source_id']).items()},
            'by_style': {k: describe(v) for k, v in grouped(rows, lambda r: r['initial_style']).items()},
            'by_greed': {k: describe(v) for k, v in grouped(rows, lambda r: 'unknown' if r['context'].get('stone_status') != 'verified' else str('greed' in r['context'].get('gems', [])).lower()).items()},
            'by_full_stone_set': [{'gems': list(k), **describe(v)} for k, v in grouped(rows, lambda r: tuple(sorted(r['context'].get('gems', []))) if r['context'].get('stone_status') == 'verified' else ('unknown',)).items()],
            'by_run_class': {str(k): describe(v) for k, v in grouped(rows, lambda r: r['context'].get('run_class')).items()},
            'limitations': ['All eight canonical ledgers are scanned; full video temporal recall is not established.',
                            'Appearance candidates include following-scene labels. These are not all verified initial objects or completed openings.',
                            'Unknown entry contents may hide additional containers and are retained in coverage counts.',
                            'Reward frequencies among reviewed openings describe a selected subset, not population loot odds.',
                            'Item-sale gold is separate from direct gold; multiple rewards remain events in one visit.',
                            'An epic/golden chest does not establish item rarity. Unreadable items remain unknown, including legendary status.',
                            'Stone context, source, chest style, and unknown first/later event-run class are kept separate.',
                            'The source-balanced 82-visit cohort covers crates and chests; the independent 128-candidate cohort also includes barrels, corpses and false candidates.',
                            'The 82-visit cohort was selected chronologically within source before viewing rewards, not randomly from all play. Visits from one player are correlated.',
                            'New 2026 item rarity uses readable equipment-name color and the installed-client mapping; historical 2024 item rarity remains unknown without historical mapping.']}


def markdown(result):
    s = result['summary']
    lines = ['# Full-source container reward inventory', '',
             f"Scanned every canonical decision from all {len(result['coverage']['sources'])} sources. Identified **{s['appearance_candidates']} container/corpse/barrel appearance candidates**, including later-scene candidates. This is a coverage inventory, not a claim that every opening or reward is identified.", '',
             '| Source | Decisions scanned | Appearance candidates | Verified initial container | Entry unclassified |', '|---|---:|---:|---:|---:|']
    for source in result['coverage']['sources']:
        lines.append(f"| {source['source_id']} | {source['decisions']} | {source['candidates']} | {source['verified_initial_container']} | {source['entry_unknown']} |")
    lines += ['', f"There are {s['opened']} verified opened visits ({s['ordinary_opened']} ordinary wooden/bronze/silver), with known rewards merged by visit ID. **{s['reward_review_status'].get('unreviewed', 0)} candidates still have no reward audit.** Existing bounded cohorts are preserved separately. Unknown, skipped and mimic appearances never become 'nothing' results.", '']
    uniform = result['uniform_native_cohort']
    u = uniform['summary']
    if u['appearance_candidates']:
        lines += ['## Uniform sample of remaining candidates', '', uniform['selection'], '',
                  f"Reviewed {uniform['reviewed_visits']}/{uniform['expected_visits']} selected candidates. Interaction outcomes: " + ', '.join(f'{k}={v}' for k, v in u['interaction'].items()) + '.', '',
                  'False candidate labels, unopened objects and missing/censored sequences stay in the sample; none is replaced with a convenient opening. This samples the remaining recognized candidates, not every room in the videos. Conditional reward counts can still be affected by missing footage and player choices.', '',
                  '| Style | Opened interactions | Direct gold | Nothing | Persistent blessing | Instant healing | Item |', '|---|---:|---:|---:|---:|---:|---:|']
        for style, value in uniform['by_style'].items():
            flags = value['opened_reward_flags']
            lines.append(f"| {style} | {value['opened']} | " + ' | '.join(str(flags[k]['positive']) for k in ('gold', 'nothing', 'persistent_blessing', 'instant_healing', 'item')) + ' |')
        lines += ['', f"Ordinary wooden/bronze/silver openings: {u['ordinary_opened']}. Their reward flags remain three-valued:", '',
                  '| Ordinary opening reward | Positive | Negative | Unknown |', '|---|---:|---:|---:|']
        for flag, counts in u['ordinary_opened_reward_flags'].items():
            lines.append(f"| {flag} | {counts['positive']} | {counts['negative']} | {counts['unknown']} |")
        lines += ['', 'The frozen population and hash-ranked IDs are in `data/vods/full-container-random-sample-2026-10-08.json`. Source, Greed, full stone set and event-run strata are in the JSON report. Corpse and barrel interaction draws are separate styles; waking a corpse or mimic is excluded from ordinary container openings.', '']
        lines += ['For visit linkage, reviewers followed each native offer-to-entry transition through its actual first departure and excluded later-room animations. All five rejected candidates received an additional comparison against original canonical offer and following-room proof, matching pair, counter, HP, stones and scene. They are four empty corridors with a foreground statue and one living werewolf on entry. This extra independent identity audit does not cover every sampled row; timing alone is not treated as proof.', '']
    cohort = result['fixed_native_cohort']
    c = cohort['summary']
    if c['appearance_candidates']:
        lines += ['## New fixed native cohort', '', cohort['selection'], '',
                  f"Reviewed {cohort['reviewed_visits']}/{cohort['expected_visits']} visits at 8 frames/second through opening and departure, with native detail checks. These contain **{c['ordinary_opened']} ordinary container openings**, {c['opened'] - c['ordinary_opened']} special chest openings, {c['interaction'].get('skipped', 0)} skipped chests and {c['interaction'].get('awakened_mimic', 0) + c['interaction'].get('mimic_defeated', 0)} mimics. These frequencies describe this selected cohort; they are not game-wide drop probabilities.", '',
                  '| Ordinary opening reward | Observed | Share of reviewed ordinary openings | Unknown |', '|---|---:|---:|---:|']
        for flag in ('gold', 'nothing', 'persistent_blessing', 'instant_healing', 'item'):
            counts = c['ordinary_opened_reward_flags'][flag]
            lines.append(f"| {flag} | {counts['positive']}/{c['ordinary_opened']} | {100 * counts['positive'] / c['ordinary_opened']:.1f}% | {counts['unknown']} |")
        lines += ['', '| Container style | Opened | Gold | Nothing | Persistent blessing | Instant healing | Item |', '|---|---:|---:|---:|---:|---:|---:|']
        for style, value in cohort['by_style'].items():
            counts = value['opened_reward_flags']
            lines.append(f"| {style} | {value['opened']} | " + ' | '.join(str(counts[k]['positive']) for k in ('gold', 'nothing', 'persistent_blessing', 'instant_healing', 'item')) + ' |')
        lines += ['', '| Boulder of Greed context | Ordinary openings | Persistent blessing | Instant healing |', '|---|---:|---:|---:|']
        for greed, value in cohort['by_greed'].items():
            counts = value['ordinary_opened_reward_flags']
            lines.append(f"| {greed} | {value['ordinary_opened']} | {counts['persistent_blessing']['positive']} | {counts['instant_healing']['positive']} |")
        lines += ['', 'The small Greed group does not support an effect estimate. Full stone combinations, source and first/later/unknown event-run groups remain separate in the JSON report. Blessings and gold can drop together, so the reward shares do not add to 100%.', '',
                  'A defeated mimic yielded an epic helmet; that combat reward is excluded from ordinary chest openings. A readable equipment name is the rarity evidence, not the chest style or an ULTIMATE banner. Two sated chests yielded fortress stone and arcane splinters. Item-sale proceeds remain separate from direct gold.', '',
                  '## Combined evidence inventory', '']
    lines += [
              '| Reward in ordinary opened visits | Positive | Negative | Unknown |', '|---|---:|---:|---:|']
    for flag, counts in s['ordinary_opened_reward_flags'].items():
        lines.append(f"| {flag} | {counts['positive']} | {counts['negative']} | {counts['unknown']} |")
    lines += ['', 'The combined table includes earlier bounded audits and incidental positive discoveries. Use the fixed cohort above for a denominator selected before outcome review. Item rarity requires separate positive evidence; ornate chest art alone is insufficient.', '']
    scan = result.get('cached_frame_proposal_scan')
    if scan:
        lines += [f"The resumable automated pass inspected {scan['cached_frames']} cached frames across {scan['candidates']} candidates. Its icon/HUD proposals are **unvalidated review aids** and added zero verified reward labels. Sparse cached frames miss animations; no absence or legendary result is inferred from them.", '']
    lines += ['## Limitations', '']
    lines += ['- ' + item for item in result['limitations']]
    lines += ['', 'Reproduce the inventory and manual joins: `python tools/vod_container_reanalysis.py`. Rescan local cached footage with `--scan-cached`; resume native opening extraction with `--prepare-openings PATH`. The checked-in native review ledger is loaded by default; use `--reviews PATH` to select another one. Evidence images remain local; source times and hashes enter the public dataset.', '']
    return '\n'.join(lines)


class RewardProposals:
    """Find review candidates in cached frames; never produce verified rewards.

    The gold-animation proposal deliberately cannot distinguish direct loot from
    item-sale proceeds. Missing hits never mean nothing. The reference corner
    detects a reward panel, not item identity or rarity.
    """
    def __init__(self, gold_reference, panel_reference):
        import cv2
        self.cv = cv2
        self.templates = []
        for name, path, box in [('gold_animation', gold_reference, (370, 232, 29, 20)),
                                ('item_reward_panel', panel_reference, (251, 134, 30, 29))]:
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f'Missing reward reference: {path}')
            frame = cv2.resize(frame, (684, 480))
            x, y, w, h = box
            patch = frame[y:y+h, x:x+w]
            for scale in (.8, .9, 1., 1.1, 1.2):
                self.templates.append((name, cv2.resize(patch, (round(w*scale), round(h*scale)))))
        self.references = [{'sha256': sha256(p), 'purpose': k} for k, p in [('gold_animation', gold_reference), ('item_reward_panel', panel_reference)]]

    def inspect(self, frame):
        cv = self.cv
        game = cv.resize(frame, (684, 480), interpolation=cv.INTER_AREA)
        result = {}
        for name, template in self.templates:
            box = (180, 130, 390, 210) if name == 'gold_animation' else (120, 95, 445, 115)
            x, y, w, h = box
            area = game[y:y+h, x:x+w]
            _, score, _, pos = cv.minMaxLoc(cv.matchTemplate(area, template, cv.TM_CCOEFF_NORMED))
            if score > result.get(name, {}).get('score', -1):
                result[name] = {'score': round(float(score), 5), 'box': [x+pos[0], y+pos[1], template.shape[1], template.shape[0]]}
        return result, game[401:449, 210:465]


def scan_cached(rows, output):
    """Scan all candidate evidence, resumably, with bounded memory and one worker."""
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    refs = ROOT / 'out/vods/container-audit-2026-10-08/containers'
    reader = RewardProposals(refs / 'O83-009.jpg', refs / 'O129-011.jpg')
    predictions = []
    checkpoint = output / 'checkpoint.jsonl'
    cached_predictions = {}
    if checkpoint.exists():
        for previous in read_rows(checkpoint):
            cached_predictions[previous['observation_id']] = previous
    cache = {}
    for index, row in enumerate(rows):
        ident = row['observation_id']
        signature = hashlib.sha256(json.dumps({'evidence': row['_cached_evidence'], 'references': reader.references}, sort_keys=True).encode()).hexdigest()
        previous = cached_predictions.get(ident)
        if previous and previous.get('input_signature') == signature:
            predictions.append(previous)
            continue
        best = {}
        frames = []
        missing, unsuitable, changed = 0, 0, 0
        for ev in row['_cached_evidence']:
            path = Path(row['_base']) / ev['image']
            if not path.exists():
                missing += 1
                continue
            digest = sha256(path)
            if ev.get('sha256') and digest != ev['sha256']:
                changed += 1
                continue
            if digest in cache:
                scores, hud = cache[digest]
            else:
                frame = cv2.imread(str(path))
                if frame is None or frame.shape[0] < 200 or not 1.2 < frame.shape[1]/frame.shape[0] < 1.7:
                    unsuitable += 1
                    continue
                scores, hud = reader.inspect(frame)
                cache[digest] = scores, hud
            proof = {**ev, 'sha256': digest, 'path': str(path)}
            frames.append((proof, hud))
            for kind, score in scores.items():
                if score['score'] > best.get(kind, {}).get('score', -1):
                    best[kind] = {**score, 'evidence': proof}
        if len(cache) > 2000:
            cache.clear()
        stamps = [e['source_seconds'] for e, _ in frames]
        gap = max((b-a for a, b in zip(stamps, stamps[1:])), default=None)
        hud_delta = float(np.mean(np.abs(frames[-1][1].astype(float)-frames[0][1].astype(float)))) if len(frames) > 1 else None
        predictions.append({'observation_id': ident, 'source_id': row['source_id'], 'input_signature': signature, 'review_status': 'unreviewed_automation_proposals',
                            'inspected_cached_frames': len(frames), 'missing_frames': missing, 'changed_hash_frames': changed,
                            'unsuitable_geometry_frames': unsuitable, 'sample_max_gap_seconds': gap,
                            'reward_candidates': {k: {**v, 'above_proposal_threshold': v['score'] >= .80} for k, v in best.items()},
                            'hud_pixel_change': round(hud_delta, 3) if hud_delta is not None else None,
                            'first_frame': frames[0][0] if frames else None, 'last_frame': frames[-1][0] if frames else None,
                            'negative_reward_status': 'unknown', 'legendary_status': 'unknown'})
        with checkpoint.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(predictions[-1]) + '\n')
        if (index+1) % 100 == 0:
            print(f'Cached reward proposals: {index+1}/{len(rows)}', flush=True)
    payload = {'schema': 1, 'references': reader.references, 'rows': predictions,
               'limitations': ['Proposal thresholds are not validated classifiers.', 'No absence, complete opening, direct gold, blessing identity or item rarity is inferred.',
                               'HUD changes can be caused by entry costs, expiration or combat, not only container blessings.']}
    (output / 'proposals.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    # One source-balanced overview of highest scoring unreviewed panels helps
    # native follow-up; the complete proposal table remains available.
    groups = grouped(predictions, lambda r: r['source_id'])
    for source, group in groups.items():
        ordered = sorted(group, key=lambda r: max((v['score'] for v in r['reward_candidates'].values()), default=0), reverse=True)
        selected = ordered[:12]
        canvas = np.zeros((len(selected)*175, 1000, 3), np.uint8)
        for i, pred in enumerate(selected):
            cv2.putText(canvas, pred['observation_id'], (4, i*175+18), 0, .45, (255,255,255), 1)
            proof_frames = [pred['first_frame']] + [v['evidence'] for v in pred['reward_candidates'].values()] + [pred['last_frame']]
            for j, ev in enumerate(proof_frames[:4]):
                if not ev:
                    continue
                frame = cv2.imread(ev['path'])
                if frame is None:
                    continue
                canvas[i*175+24:i*175+174, j*250:j*250+214] = cv2.resize(frame, (214,150))
                cv2.putText(canvas, str(ev['source_seconds']), (j*250+2, i*175+36), 0, .36, (0,255,255), 1)
        cv2.imwrite(str(output / f'{source}-candidates.jpg'), canvas)
    return {'candidates': len(predictions), 'cached_frames': sum(r['inspected_cached_frames'] for r in predictions),
            'without_cached_frames': sum(r['inspected_cached_frames'] == 0 for r in predictions),
            'gold_animation_candidates': sum(r['reward_candidates'].get('gold_animation', {}).get('above_proposal_threshold', False) for r in predictions),
            'item_panel_candidates': sum(r['reward_candidates'].get('item_reward_panel', {}).get('above_proposal_threshold', False) for r in predictions),
            'verified_reward_labels_added': 0}


def prepare_opening_cohort(manifest_path, output):
    """Extract every prespecified visit at native resolution with explicit censoring."""
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    extracted = []
    source_cache = {}
    cap = None
    current = None
    for index, row in enumerate(manifest['rows']):
        dest = output / f'{index:03}'
        dest.mkdir(exist_ok=True)
        saved = dest / 'index.json'
        if saved.exists():
            extracted.append(json.loads(saved.read_text()))
            continue
        base = Path(row['_base'])
        source = row['source_id']
        if source not in source_cache:
            # Native audits may have overlapping targeted downloads. Select exactly
            # one clip per timestamp, preferring the longest clip, and retain its
            # hash in each frame's evidence. This does not duplicate observations.
            clips = []
            for marker in (base / source / '720p').glob('*.json'):
                clip = json.loads(marker.read_text(encoding='utf-8'))
                if clip.get('schema') == 1 and 'file' in clip:
                    clips.append((marker, clip))
            clips.sort(key=lambda pc: (-(pc[1]['source_end'] - pc[1]['source_start']), pc[1]['source_start'], str(pc[0])))
            layout = json.loads((base / 'layouts' / f'{source}.json').read_text())['crop']
            source_cache[source] = clips, layout
        clips, layout = source_cache[source]
        start = row['entry_window_start'] - .125
        end = min(row['entry_window_end'] + .25, start + manifest['maximum_seconds'])
        times = np.arange(start, end + .001, 1 / manifest['fps'])
        frames, missing = [], []
        for j, stamp in enumerate(times):
            found = next(((p, c) for p, c in clips if c['source_start'] <= stamp < c['source_end']), None)
            if found is None:
                missing.append(round(float(stamp), 6))
                continue
            marker, clip = found
            path = marker.parent / clip['file']
            if path != current:
                if cap:
                    cap.release()
                cap = cv2.VideoCapture(str(path))
                current = path
            cap.set(cv2.CAP_PROP_POS_MSEC, (stamp - clip['source_start']) * 1000)
            ok, raw = cap.read()
            if not ok:
                missing.append(round(float(stamp), 6))
                continue
            h, w = raw.shape[:2]
            x, y, cw, ch = [round(v * d) for v, d in zip(layout, [w, h, w, h])]
            frame = raw[y:y+ch, x:x+cw]
            path = dest / f'{j:03}.jpg'
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 98])
            frames.append({'image': path.relative_to(ROOT / 'out/vods').as_posix(), 'source_seconds': round(float(stamp), 6),
                           'sha256': sha256(path), 'video_sha256': clip['sha256'], 'native_height': h, 'role': 'native_container_visit_sequence'})
        result = {'observation_id': row['observation_id'], 'source_id': source, 'context': row['context'],
                  'index': index, 'source_start': float(start), 'source_end': float(end),
                  'expected_departure': row['entry_window_end'], 'censored_by_duration': end < row['entry_window_end'] + .25,
                  'missing_timestamps': missing, 'frames': frames}
        saved.write_text(json.dumps(result, indent=2) + '\n')
        for page, offset in enumerate(range(0, len(frames), 48)):
            group = frames[offset:offset+48]
            width, tile_h, cols = 250, 195, 6
            canvas = np.zeros((int(np.ceil(len(group)/cols))*tile_h, width*cols, 3), np.uint8)
            for k, ev in enumerate(group):
                yy, xx = (k//cols)*tile_h, (k%cols)*width
                frame = cv2.imread(str(ROOT / 'out/vods' / ev['image']))
                canvas[yy+20:yy+195, xx:xx+250] = cv2.resize(frame, (250,175))
                cv2.putText(canvas, f"{index} f{offset+k} {ev['source_seconds']:.3f}", (xx+2, yy+14), 0, .4, (255,255,255), 1)
            cv2.imwrite(str(dest / f'sheet-{page}.jpg'), canvas, [cv2.IMWRITE_JPEG_QUALITY, 98])
        extracted.append(result)
        (output / 'index.json').write_text(json.dumps(extracted, indent=2) + '\n')
        print(f"Native opening cohort {index+1}/{len(manifest['rows'])}: {row['observation_id']}, {len(frames)} frames", flush=True)
    if cap:
        cap.release()
    return extracted


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan', action='append', type=Path)
    ap.add_argument('--reviews', type=Path, default=ROOT / 'data/vods/full-container-native-reviews-2026-10-08.jsonl')
    ap.add_argument('--entry-links', type=Path, default=ROOT / 'data/vods/full-entry-linkage-2026-10-08.jsonl')
    ap.add_argument('--random-reviews', type=Path, default=ROOT / 'data/vods/full-container-random-reviews-2026-10-08.jsonl')
    ap.add_argument('--output', type=Path, default=ROOT / 'docs/vod-full-container-audit-2026-10-08.json')
    ap.add_argument('--dataset', type=Path, default=ROOT / 'data/vods/full-container-rewards-2026-10-08.jsonl')
    ap.add_argument('--work', type=Path, default=ROOT / 'out/vods/full-reanalysis-2026-10-08/containers')
    ap.add_argument('--scan-cached', action='store_true', help='Produce temporal/icon review proposals from every inventoried cached frame; never accepts labels.')
    ap.add_argument('--prepare-openings', type=Path, help='Prespecified visit manifest to extract, then exit.')
    ap.add_argument('--select-random-cohort', type=Path, help='Write a deterministic 128-candidate native extraction manifest before viewing outcomes, then exit.')
    args = ap.parse_args(argv)
    if args.prepare_openings:
        prepare_opening_cohort(args.prepare_openings, args.work / 'opening-cohort')
        return
    complete = read_rows(ROOT / 'data/vods/container-interactions-2026-10-08.jsonl')
    supplemental = read_rows(ROOT / 'data/vods/supplemental-container-rewards-2026-10-08.jsonl')
    manual = read_rows(args.reviews) if args.reviews and args.reviews.exists() else []
    if args.random_reviews.exists():
        manual += read_rows(args.random_reviews)
    additional = {r['observation_id']: ALIASES.get(r['initial_style'], r['initial_style']) for r in complete + manual}
    additional.update({r['observation_id']: r['reward']['container'] for r in supplemental})
    rows, coverage = inventory(args.plan or DEFAULT_PLANS, additional)
    if args.entry_links.exists():
        augment_entry_links(rows, read_rows(args.entry_links))
        coverage['entry_linkage_supplement_sha256'] = sha256(args.entry_links)
    rows = merge_reviews(rows, complete, supplemental, manual)
    if args.select_random_cohort:
        selected = select_uniform_candidates(rows)
        args.select_random_cohort.parent.mkdir(parents=True, exist_ok=True)
        args.select_random_cohort.write_text(json.dumps(selected, indent=2) + '\n', encoding='utf-8')
        return
    result = report(rows, coverage)
    if args.scan_cached:
        result['cached_frame_proposal_scan'] = scan_cached(rows, args.work / 'cached-scan')
    for path in (args.output, args.dataset, args.work / 'inventory.json'):
        path.parent.mkdir(parents=True, exist_ok=True)
    (args.work / 'inventory.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    args.dataset.write_text(''.join(json.dumps({k: v for k, v in r.items() if not k.startswith('_')}, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    result['dataset'] = {'path': args.dataset.relative_to(ROOT).as_posix() if args.dataset.is_relative_to(ROOT) else str(args.dataset), 'sha256': sha256(args.dataset)}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps({'summary': result['summary'], 'sources': len(coverage['sources'])}, indent=2))


if __name__ == '__main__':
    main()
