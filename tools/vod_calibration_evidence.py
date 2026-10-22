#!/usr/bin/env python3
"""Derive explicitly scoped working-profile evidence from reviewed VOD ledgers.

This tool changes no simulator settings. It reports joint loot outcomes (rather
than treating overlapping flags as exclusive rewards), exact mystery alternatives,
and the trap likelihood for the engine's stop-after-first eligible-side algorithm.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


HISTORICAL = 'youtube-DCb80VikGM0'
OFFER_MODIFIERS = {
    'greed', 'explorer', 'pendant', 'spying', 'lodestone', 'devil',
    'blood', 'hick', 'thirsty', 'misadventurer',
}
LOOT_FLAGS = ('gold', 'nothing', 'persistent_blessing', 'instant_healing', 'item', 'curse')
NONPHYSICAL = {'monster', 'mystery', 'golden'}


def read_rows(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len({r['observation_id'] for r in rows}) != len(rows):
        raise ValueError(f'Duplicate visit IDs in {path}')
    return rows


def counted(values):
    return dict(sorted(Counter(values).items()))


def counts_and_fractions(values):
    counts = Counter(values)
    total = sum(counts.values())
    return {'denominator': total, 'counts': dict(sorted(counts.items())),
            'fractions': {k: v / total for k, v in sorted(counts.items())}}


def offer_summary(rows):
    return {
        'offers': len(rows),
        'pair_frequencies': counts_and_fractions(r['pair_family'] for r in rows),
        'physical_styles': counts_and_fractions(
            kind for r in rows for kind in r['offered_kinds'] if kind not in NONPHYSICAL),
        'by_stage': counted(str(r['context'].get('stage')) for r in rows),
        'by_source': counted(r['source_id'] for r in rows),
    }


def mystery_summary(rows):
    result = {}
    for alternative in sorted({kind for r in rows for kind in r['offered_kinds']}):
        offered = [r for r in rows if 'mystery' in r['offered_kinds'] and
                   (alternative in r['offered_kinds'] if alternative != 'mystery'
                    else r['offered_kinds'] == ['mystery', 'mystery'])]
        entered = [r for r in offered if r['selected_kind'] == 'mystery' and
                   r['other_choice'] == alternative and r['initial_content_known']]
        if not offered:
            continue
        result[alternative] = {
            'offered_pairs': len(offered), 'linked_known_interiors': len(entered),
            'initial_contents': counts_and_fractions(r['initial_content'] for r in entered),
            'selection_bases': counted(r['selection_basis'] for r in entered),
        }
    return result


def trap_fit(reviews, entries):
    observations = []
    unknown = []
    for review in reviews:
        ident = review['observation_id']
        if ident not in entries:
            continue
        e = entries[ident]
        sides = review['sides']
        if not (any(s is True for s in sides) or all(s is False for s in sides)):
            unknown.append(ident)
            continue
        n = sum(k in {'monster', 'mystery'} for k in e['offered_kinds'])
        positive = any(s is True for s in sides)
        if positive and n == 0:
            raise ValueError(f'Trap on a pair with no eligible sides: {ident}')
        if 'masochist' in e['context']['gems']:
            raise ValueError('Deterministic Masochist traps need a separate stratum.')
        observations.append((n, positive))

    def log_likelihood(p):
        result = 0.0
        for n, positive in observations:
            q = 1 - (1 - p) ** n
            result += math.log(max(1e-300, q if positive else 1 - q))
        return result

    informative = [x for x in observations if x[0]]
    if informative:
        lo, hi = 0.0, 1.0
        for _ in range(100):
            a, b = (2 * lo + hi) / 3, (lo + 2 * hi) / 3
            if log_likelihood(a) > log_likelihood(b):
                hi = b
            else:
                lo = a
        mle = (lo + hi) / 2
        cutoff = log_likelihood(mle) - 1.920729410347062
        low, high = 0.0, mle
        for _ in range(80):
            mid = (low + high) / 2
            if log_likelihood(mid) < cutoff:
                low = mid
            else:
                high = mid
        lower = (low + high) / 2
        low, high = mle, 1.0
        for _ in range(80):
            mid = (low + high) / 2
            if log_likelihood(mid) < cutoff:
                high = mid
            else:
                low = mid
        interval = [lower, (low + high) / 2]
    else:
        mle, interval = None, None
    positive = sum(y for _, y in observations)
    return {
        'resolved_pairs': len(observations), 'trap_pairs': positive,
        'unresolved_pairs': len(unknown),
        'observed_pair_fraction': positive / len(observations) if observations else None,
        'eligible_side_strata': [
            {'eligible_sides': n, 'pairs': sum(x == n for x, _ in observations),
             'trap_pairs': sum(x == n and y for x, y in observations)} for n in (0, 1, 2)],
        'eligible_side_probability_mle': mle,
        'profile_likelihood_95': interval,
        'model': 'P(any trap | n eligible monster/mystery sides) = 1 - (1-p)^n; at most one is placed.',
        'limitations': 'Intervals are conditional on the model and readable reviewed frames; they do not cover source clustering, missingness or unseen labeling error.',
    }


def loot_summary(rows):
    joint = Counter()
    for r in rows:
        flags = r['reward_flags']
        if any(flags.get(k) is None for k in LOOT_FLAGS):
            joint['unresolved_joint_outcome'] += 1
        else:
            joint['+'.join(k for k in LOOT_FLAGS if flags[k]) or 'no_recorded_flag'] += 1
    return {
        'openings': len(rows), 'joint_outcomes': dict(sorted(joint.items())),
        'flags': {k: {'positive': sum(r['reward_flags'].get(k) is True for r in rows),
                      'negative': sum(r['reward_flags'].get(k) is False for r in rows),
                      'unknown': sum(r['reward_flags'].get(k) is None for r in rows)}
                  for k in (*LOOT_FLAGS, 'epic_item', 'legendary_item')},
        'by_style': counted(r['initial_style'] for r in rows),
        'by_source': counted(r['source_id'] for r in rows),
        'observation_ids': [r['observation_id'] for r in rows],
    }


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / divisor
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return [centre - radius, centre + radius]


def gated_style_evidence(current):
    result = {}
    for gem, kind in (('spying', 'unlocked'), ('lodestone', 'double_locked')):
        rows = [r for r in current if gem in r['context']['gems']
                and not (OFFER_MODIFIERS - {gem}).intersection(r['context']['gems'])]
        styles = Counter(k for r in rows for k in r['offered_kinds'])
        denominator = styles['locked'] + styles[kind]
        result[gem] = {
            'offers': len(rows), 'locked_family': denominator,
            'ordinary_locked': styles['locked'], kind: styles[kind],
            'variant_share_of_locked_family': styles[kind] / denominator if denominator else None,
            'wilson_95': wilson(styles[kind], denominator),
            'other_generation_modifiers_excluded': sorted(OFFER_MODIFIERS - {gem}),
            'by_stage': {str(stage): counted(k for r in rows if r['context']['stage'] == stage
                                         for k in r['offered_kinds'] if k in {'locked', kind})
                         for stage in (1, 2, 3, 4)},
            'interpretation': 'Observed within locked-family offers. Assumes variants replace the same base family; player/source clustering and frame recognition are not covered by Wilson intervals.',
        }
    return result


def working_recommendations(cohorts, container_strata, gates):
    rows = cohorts['current_no_known_offer_modifiers']
    styles = Counter(kind for r in rows for kind in r['offered_kinds'] if kind not in NONPHYSICAL)
    # Reserve the previous 2%-of-all double-mystery mimic placeholder inside the
    # observed aggregate chest share. Use the same conditional split at a wall.
    chest_mimic_fraction = 5 / 42
    converted = {}
    for alternative in ('mystery', 'wall'):
        contents = mystery_summary(rows)[alternative]['initial_contents']['counts']
        chest = contents.get('chest', 0)
        mimic = chest * chest_mimic_fraction
        converted[alternative] = {
            'monster': contents.get('monster', 0), 'barrel': contents.get('barrel', 0),
            'crate': contents.get('wooden_box', 0), 'skeleton': contents.get('corpse', 0),
            'empty': contents.get('empty', 0), 'mimic': mimic,
            'silver': (chest - mimic) * 8 / 13, 'bronze': (chest - mimic) * 5 / 13,
        }
    loot = container_strata['current']['ordinary_no_greed_or_hero']['joint_outcomes']
    masks = [0.0] * 8
    for outcome, count in loot.items():
        flags = outcome.split('+')
        mask = int('persistent_blessing' in flags or 'instant_healing' in flags)
        mask += 2 * int('item' in flags) + 4 * int('gold' in flags)
        masks[mask] += count
    total = sum(masks)
    without_blessing = sum(masks[mask] for mask in (0, 2, 4, 6))
    transfer_fraction = min(1.0, 0.20 * total / without_blessing) if without_blessing else 0.0
    greed = list(masks)
    for mask in (0, 2, 4, 6):
        transfer = masks[mask] * transfer_fraction
        greed[mask] -= transfer
        greed[mask + 1] += transfer
    return {
        'offer_pair_raw_weights': counted(r['pair_family'] for r in rows),
        'physical_style_raw_weights': dict(sorted(styles.items())),
        'physical_style_weights_scaled_to_total_34': {k: v * 34 / sum(styles.values())
                                                     for k, v in sorted(styles.items())},
        'physical_style_scaling_reason': 'Preserve prior absolute scale for the still-placeholder Devil +8 weight; split hungry uniformly among the six resource-demand types as an unmeasured assumption.',
        'mystery_encounter_raw_weights': converted,
        'mystery_chest_split_assumptions': {'silver_to_bronze': '8:5',
            'mimic_fraction_within_aggregate_chests': chest_mimic_fraction,
            'double_mystery_mimic_fraction_of_all': 0.02,
            'wall_mimic_fraction_of_all': chest_mimic_fraction / 63,
            'interpretation': 'Chest subtype and mimic split are retained assumptions; observed monster and total chest shares are unchanged.'},
        'eligible_side_trap_probability': 0.29,
        'trap_fit_cohort': 'All current-event blind reviewed pairs; appearance-modifier exclusions are unnecessary for an eligible-side hazard conditional on the offered pair.',
        'container_joint_bitmask_weights': masks,
        'container_bit_order': 'bit0: blessing including instant healing; bit1: item; bit2: gold',
        'container_greed_joint_bitmask_weights_assumed_plus_20pp': greed,
        'container_greed_interpretation': 'Unmeasured +20 percentage-point uplift transferred proportionally from no-blessing outcomes to the otherwise identical blessing outcome; item/gold marginals preserved. This is a mechanic assumption, not fitted from the seven current Greed openings.',
        'container_no_greed_base_blessing_probability': sum(masks[m] for m in (1, 3, 5, 7)) / total,
        'container_hard_greed_requirement': False,
        'spying_locked_to_unlocked_fraction': round(gates['spying']['variant_share_of_locked_family'], 2),
        'lodestone_recommendation': 'Keep the existing provisional additive multiplier 0.5; eight variants in 36 locked-family offers are insufficient to resolve it reliably. Empirical fraction 8/36 corresponds to additive multiplier 8/28.',
        'retain_existing_pending_data': ['barrel blessing probability', 'corpse awakening and loot',
            'unattributed physical-door interiors', 'golden subtype distribution',
            'first-event-run differences', 'shop strengths and reroll distribution',
            'Greed causal bonus', 'traps by stage and source', 'rare mimic/epic container rates'],
    }


def derive(entries, trap_reviews, containers):
    current = [r for r in entries if r['source_id'] != HISTORICAL]
    cohorts = {
        'current_all_stones_descriptive': current,
        'current_no_active_stones': [r for r in current if not r['context']['gems']],
        'current_no_known_offer_modifiers': [r for r in current
            if not OFFER_MODIFIERS.intersection(r['context']['gems'])],
        'historical_2024_all_stones_descriptive': [r for r in entries if r['source_id'] == HISTORICAL],
    }
    random = [r for r in containers if r.get('native_cohort', {}).get('name') == 'uniform_remaining_128']
    container_strata = {}
    for era in ('current', 'historical_2024'):
        eligible = [r for r in random if (r['source_id'] == HISTORICAL) == (era == 'historical_2024')]
        ordinary = [r for r in eligible if r['ordinary_loot_eligible']]
        container_strata[era] = {
            'ordinary_all_stones_descriptive': loot_summary(ordinary),
            'ordinary_greed_absent': loot_summary([r for r in ordinary if 'greed' not in r['context']['gems']]),
            'ordinary_greed_present': loot_summary([r for r in ordinary if 'greed' in r['context']['gems']]),
            'ordinary_no_greed_or_hero': loot_summary([r for r in ordinary
                if not {'greed', 'hero'}.intersection(r['context']['gems'])]),
            'ordinary_no_active_stones': loot_summary([r for r in ordinary if not r['context']['gems']]),
            'barrel_no_gambler_thirsty_time_traveler': loot_summary([r for r in eligible
                if r['interaction'] == 'opened' and r['initial_style'] == 'barrel'
                and not {'gambler', 'thirsty', 'time_traveler'}.intersection(r['context']['gems'])]),
            'corpse_interactions': counted(r['interaction'] for r in eligible if r['initial_style'] == 'corpse'),
        }
    mystery_contexts = defaultdict(list)
    for r in entries:
        c = r['context']
        key = json.dumps({'source_id': r['source_id'], 'stage': c.get('stage'),
                          'gems': sorted(c['gems']), 'run_class': c.get('run_class'),
                          'version': c.get('version'), 'theme': c.get('theme'), 'mode': c.get('mode')}, sort_keys=True)
        mystery_contexts[key].append(r)
    gates = gated_style_evidence(current)
    return {
        'schema': 1,
        'historical_source': HISTORICAL,
        'excluded_offer_modifier_stones': sorted(OFFER_MODIFIERS),
        'scope': 'Working calibration evidence. Excluding known modifier stones assumes retained stones have no unlisted effects. Current refers to the seven nonhistorical recorded 2026 sources, not a claim about future versions.',
        'offer_cohorts': {k: offer_summary(rows) for k, rows in cohorts.items()},
        'mystery_cohorts': {k: mystery_summary(rows) for k, rows in cohorts.items()},
        'mystery_full_contexts': [{'context': json.loads(k), 'mystery': mystery_summary(rows)}
                                  for k, rows in sorted(mystery_contexts.items())],
        'traps': {k: trap_fit(trap_reviews, {r['observation_id']: r for r in rows})
                  for k, rows in cohorts.items()},
        'uniform_container_cohort': container_strata,
        'stone_enabled_style_cohorts': gates,
        'working_recommendations_separate_from_observations': working_recommendations(cohorts, container_strata, gates),
        'limitations': [
            'No-stone offer data are concentrated in stage 1; later-stage extrapolation is an assumption.',
            'Source windows are a convenience sample, and repeat runs from the same players are clustered.',
            'Physical-door content linkage deliberately targeted objects; it cannot estimate monster probability.',
            'Mystery beside visible monster was attributed largely by object exclusion; its content mixture is selected, not an unbiased frequency.',
            'Mystery/mystery route identity is known regardless of selected side; unknown interiors still remain censored.',
            'Mystery/wall entries are forced, but readable-content selection can still bias the observed mixture.',
            'Uniform container selection was among remaining candidates after earlier reviews, not all in-game containers; openings are also player-selected.',
            'Loot flags overlap. A blessing can occur with gold or an item; summing their marginal rates as exclusive weights is invalid.',
            'Greed/no-Greed and other stone contrasts are observational and confounded; small subgroups cannot identify causal stone bonuses.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entries', type=Path, default=Path('data/vods/full-entry-linkage-2026-10-08.jsonl'))
    parser.add_argument('--trap-reviews', type=Path, default=Path('data/vods/full-trap-blind-2026-10-08.jsonl'))
    parser.add_argument('--containers', type=Path, default=Path('data/vods/full-container-rewards-2026-10-08.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('docs/vod-calibration-evidence-2026-10-08.json'))
    args = parser.parse_args()
    paths = {'entries': args.entries, 'trap_reviews': args.trap_reviews, 'containers': args.containers}
    result = derive(*(read_rows(p) for p in paths.values()))
    result['inputs'] = {name: {'path': str(path).replace('\\', '/'),
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                        for name, path in paths.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Wrote {args.output}')


if __name__ == '__main__':
    main()
