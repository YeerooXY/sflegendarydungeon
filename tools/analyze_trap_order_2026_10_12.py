"""Trap eligibility, pair dependence and observable consequences of role order.

Offline, descriptive evidence analysis. Does not change the live game or simulator.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math

import numpy as np
from scipy.optimize import minimize_scalar, brentq
from scipy.stats import binom, binomtest, fisher_exact

from summarize_vod_cumulative_2026_10_12 import ROOT, HISTORICAL, ordinary_new, normalize_offer, neutral
from summarize_vod_review_2026_10_12 import wilson

DIR = ROOT / 'data/vods/trap-order-2026-10-12'
CORE = {'monster', 'mystery'}
INPUTS = {}


def read(path):
    if not path.is_absolute():
        path = ROOT / path
    INPUTS[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    value = path.read_text(encoding='utf-8-sig')
    return [json.loads(s) for s in value.splitlines() if s.strip()] if path.suffix == '.jsonl' else json.loads(value)


def exclusion(row):
    if row.get('phase') != 'ordinary':
        return 'nonordinary_phase'
    room = row.get('room')
    if room is not None:
        if room <= 5:
            return 'protected_opening_1_to_5'
        if room >= 100 or room % 25 == 0:
            return 'boss_or_outside_run'
    elif row.get('stage') not in (2, 3, 4):
        return 'opening_boundary_unresolved'
    if any(k in {'boss', 'shop', 'trial', 'exit_trial'} for k in row['kinds']):
        return 'forced_encounter'
    if 'masochist' in (row.get('gems') or []):
        return 'deterministic_trap_modifier'
    return None


def core_flags(row):
    return [v for k, v in zip(row['kinds'], row['sides']) if k in CORE]


def any_trap(flags):
    if any(v is True for v in flags):
        return True
    if flags and all(v is False for v in flags):
        return False
    return None


def stat(values):
    known = [v for v in values if v is not None]
    k, n = sum(v is True for v in known), len(known)
    return {'k': k, 'n': n, 'estimate': k / n if n else None,
            'wilson_95': wilson(k, n), 'unknown': len(values) - n,
            'worst_case_missingness_bounds': [k / len(values), (k + len(values) - n) / len(values)] if values else None}


def probabilities(p, m=.9):
    """Fixed risky-role-first; equal independent attempted rolls; stop on success."""
    if not 0 <= p <= 1 or not 0 <= m <= 1:
        raise ValueError('Probabilities must lie in [0,1].')
    return {'p_one_eligible': p, 'p_any_two_eligible': 2*p-p*p,
            'p_first_role_trap': p, 'p_second_role_trap': (1-p)*p,
            'p_no_trap_two': (1-p)**2, 'p_double_trap_stop_first': 0.,
            'p_double_trap_independent': p*p,
            'first_role_given_trap': 1/(2-p) if p else None,
            'monster_if_choose_trapped': m/(2-p) if p else None,
            'monster_if_choose_untrapped_in_trapped_pair': m*(1-p)/(2-p) if p else None,
            'monster_if_no_trap_and_blind_choice': m/2 if p < 1 else None,
            'random_priority_monster_either_side': m/2 if p else None}


def fit_presence(rows, sequential):
    observations = [(len(core_flags(r)), any_trap(core_flags(r))) for r in rows
                    if core_flags(r) and all(v is not None for v in core_flags(r))]
    if not observations:
        return None
    def ll(p):
        result = 0.
        for n, y in observations:
            q = 1-(1-p)**n if sequential else p
            result += math.log(max(1e-300, q if y else 1-q))
        return result
    optimum = minimize_scalar(lambda p: -ll(p), bounds=(0, 1), method='bounded', options={'xatol': 1e-12})
    candidates = [0., float(optimum.x), 1.]
    p = max(candidates, key=ll)
    cutoff = ll(p) - 1.920729410347062
    low = 0. if ll(0) >= cutoff else brentq(lambda v: ll(v)-cutoff, 0, p)
    high = 1. if ll(1) >= cutoff else brentq(lambda v: ll(v)-cutoff, p, 1)
    return {'p': p, 'n': len(observations), 'log_likelihood': ll(p),
            'aic': 2-2*ll(p), 'profile_likelihood_95': [low, high],
            'predicted_any_one': p, 'predicted_any_two': 1-(1-p)**2 if sequential else p}


def independent_roll_count_fit(rows):
    observations = [core_flags(r) for r in rows if core_flags(r) and all(v is not None for v in core_flags(r))]
    n = sum(map(len, observations))
    k = sum(sum(v is True for v in a) for a in observations)
    if not n:
        return None
    p = k/n
    ll = 0.
    for flags in observations:
        traps, count = sum(flags), len(flags)
        q = math.comb(count, traps) * p**traps * (1-p)**(count-traps)
        ll += math.log(max(1e-300, q))
    return {'p': p, 'eligible_options': n, 'traps': k, 'log_likelihood_of_trap_count': ll,
            'aic': 2-2*ll, 'expected_double_traps': sum(len(a)==2 for a in observations)*p*p}


def source_bootstrap(rows, iterations=5000):
    """Cluster at source; descriptive only, sources may share streamers/characters."""
    counts = defaultdict(lambda: [0, 0])
    for r in rows:
        y = any_trap(core_flags(r))
        if y is not None:
            counts[r['source_id']][0] += int(y)
            counts[r['source_id']][1] += 1
    if len(counts) < 2:
        return None
    a = np.array(list(counts.values()), dtype=float)
    rng = np.random.default_rng(20261012)
    draws = a[rng.integers(0, len(a), size=(iterations, len(a)))].sum(axis=1)
    rates = draws[:, 0] / draws[:, 1]
    return list(map(float, np.quantile(rates, [.025, .975])))


def stratified_presence_fit(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    fits = []
    for label, rs in sorted(groups.items()):
        complete = [r for r in rs if core_flags(r) and all(v is not None for v in core_flags(r))]
        if {len(core_flags(r)) for r in complete} != {1, 2}:
            continue
        a, b = fit_presence(rs, True), fit_presence(rs, False)
        if a is None or b is None:
            continue
        fits.append({'stratum': label, 'n': len(complete), 'stop_first': a, 'pair_coin': b,
                     'delta_aic_stop_first_minus_pair_coin': a['aic']-b['aic']})
    return {'strata': fits, 'delta_aic_sum': sum(f['delta_aic_stop_first_minus_pair_coin'] for f in fits),
            'interpretation': 'Each shared stratum has its own p; this is a fit sensitivity, not a clustered significance test.'}


def gem_label(r):
    prefix = 'known:' if r.get('stones_known') else 'unverified:'
    return prefix + ','.join(sorted(r.get('gems') or []))


def summary(rows):
    strata = {}
    for n in (1, 2):
        rs = [r for r in rows if len(core_flags(r)) == n]
        strata[str(n)] = {'pairs': len(rs), 'any_trap': stat([any_trap(core_flags(r)) for r in rs]),
                         'per_eligible_option': stat([v for r in rs for v in core_flags(r)]),
                         'fully_readable_pairs': sum(all(v is not None for v in core_flags(r)) for r in rs),
                         'sources': dict(Counter(r['source_id'] for r in rs)),
                         'source_bootstrap_95': source_bootstrap(rs)}
    return {'pairs': len(rows), 'strata': strata,
            'zero_core_options_excluded': sum(not core_flags(r) for r in rows),
            'single_eligible_by_kind': {kind: stat([any_trap(core_flags(r)) for r in rows
                if len(core_flags(r))==1 and kind in r['kinds']]) for kind in sorted(CORE)},
            'by_pair': {'/'.join(kinds): stat([any_trap(core_flags(r)) for r in rows if tuple(sorted(r['kinds'])) == kinds])
                        for kinds in sorted({tuple(sorted(r['kinds'])) for r in rows})},
            'by_stage': {str(stage): {str(n): stat([any_trap(core_flags(r)) for r in rows
                if r.get('stage') == stage and len(core_flags(r)) == n]) for n in (1, 2)} for stage in (1, 2, 3, 4)},
            'by_source': {sid: {str(n): stat([any_trap(core_flags(r)) for r in rows
                if r['source_id'] == sid and len(core_flags(r)) == n]) for n in (1, 2)} for sid in sorted({r['source_id'] for r in rows})},
            'by_exact_gems': {g: {str(n): stat([any_trap(core_flags(r)) for r in rows
                if gem_label(r) == g and len(core_flags(r)) == n]) for n in (1, 2)}
                for g in sorted({gem_label(r) for r in rows})}}


def make_row(o, sides, old=False):
    r = normalize_offer(o, old)
    return {**r, 'phase': o['context']['phase'] if old else o['phase'],
            'era': '2024' if r['source_id'] == HISTORICAL else '2026',
            'kinds': [d['kind'] for d in r['doors']], 'sides': sides}


def baseline():
    old = {r['observation_id']: r for r in read(ROOT/'data/vods/full-entry-linkage-2026-10-08.jsonl')}
    new = read(ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl')
    lookup = {(r['source_id'], r['index']): r for r in new}
    identities = {r['observation_id']: r for r in read(ROOT/'data/vods/full-trap-blind-identity-2026-10-08.jsonl')}
    rows = []
    excluded = []
    for a in read(ROOT/'data/vods/full-trap-blind-2026-10-08.jsonl'):
        o = old[a['observation_id']]
        if o['source_id'] == HISTORICAL:
            continue
        r = make_row(o, a['sides'], True)
        if identities[r['id']]['status'] != 'same_visit_verified':
            r['sides'] = [None, None]
        reason = exclusion(r)
        if reason:
            excluded.append({'id': r['id'], 'reason': reason})
        else:
            rows.append(r)
    for a in read(ROOT/'data/vods/latest-event-review-2026-10-12/trap-validation--dense-reviewed.json')['rows']:
        o = lookup[(a['source_id'], a['index'])]
        if not a['eligible'] or not ordinary_new(o):
            continue
        r = make_row(o, [a['trap_left'], a['trap_right']])
        reason = exclusion(r)
        if reason:
            excluded.append({'id': r['id'], 'reason': reason})
        else:
            rows.append(r)
    assert len(rows) == len({r['id'] for r in rows})
    return rows, new, excluded


def deduplicate(rows, aliases=None):
    """Only exact evidence repeats are silently deduplicated; conflicts need review."""
    aliases = aliases or {}
    found = {}
    for r in rows:
        ident = aliases.get(r['id'], r['id'])
        if ident in found and (found[ident]['sides'] != r['sides'] or found[ident]['kinds'] != r['kinds']):
            raise ValueError('Conflicting trap annotations: ' + r['id'])
        if ident not in found or r['id'] == ident:
            found[ident] = r
    return list(found.values())


def roles(rows):
    tested = []
    rows = [r for r in rows if sorted(r['kinds']) == ['monster', 'mystery']]
    for r in rows:
        flags = r['sides']
        if all(v is not None for v in flags) and sum(flags) == 1:
            tested.append(r['kinds'][flags.index(True)] == 'monster')
    s = stat(tested)
    s['all_offers'] = len(rows)
    s['zero_trap_pairs'] = sum(r['sides'] == [False, False] for r in rows)
    s['both_trapped_pairs'] = sum(r['sides'] == [True, True] for r in rows)
    s['unknown_sides'] = sum(any(v is None for v in r['sides']) for r in rows)
    s['two_sided_binomial_p_vs_50pct'] = binomtest(s['k'], s['n'], .5).pvalue if s['n'] else None
    s['one_sided_binomial_p_monster_above_50pct'] = binomtest(s['k'], s['n'], .5, alternative='greater').pvalue if s['n'] else None
    s['trap_on_monster_per_offer'] = stat([r['sides'][r['kinds'].index('monster')] for r in rows])
    s['trap_on_mystery_per_offer'] = stat([r['sides'][r['kinds'].index('mystery')] for r in rows])
    s['left_side_among_exactly_one_trap'] = stat([r['sides'][0] for r in rows
        if all(v is not None for v in r['sides']) and sum(r['sides']) == 1])
    # Sensitivity only: one visible positive + one unreadable side is sufficient
    # to locate the sole trap only if the no-double-trap rule is assumed.
    possible = [r['kinds'][r['sides'].index(True)] == 'monster' for r in rows
                if sum(v is True for v in r['sides']) == 1]
    s['assuming_at_most_one_trap'] = stat(possible)
    s['assuming_at_most_one_trap']['assumption'] = 'Counts visible positive sides even when the companion is obscured; not used by the primary placement test.'
    return s


def approx_role_sample_size(p=.25, power=.8):
    """Exact one-sided binomial test, directional alternative frozen in advance."""
    target = 1/(2-p)
    for n in range(1, 5001):
        critical = int(binom.ppf(.95, n, .5)) + 1
        achieved = float(binom.sf(critical-1, n, target))
        if achieved >= power:
            return {'trapped_role_revealing_pairs': n, 'first_role_probability': target,
                    'one_sided_alpha': .05, 'power': achieved,
                    'approx_total_monster_mystery_offers': math.ceil(n / (2*p-p*p)),
                    'caution': 'Independent observations, complete recognition and constant p; source/account clustering increases the needed sample.'}
    return None


def load_review(filename, lookup, plan_rows=None):
    data = read(DIR/filename)
    rows = data['rows']
    assert len(rows) == len({r['id'] for r in rows}), filename + ': duplicate review'
    if plan_rows is not None:
        expected = {r['id'] for r in plan_rows}
        assert expected == {r['id'] for r in rows}, filename + ': incomplete review'
    assert data.get('review_complete', True), filename + ': review still in progress'
    out = []
    for a in rows:
        o = lookup[a['id']]
        assert a['source_id'] == o['source_id'], filename + ': source identity mismatch'
        r = make_row(o, [a.get('trap_left'), a.get('trap_right')], 'observation_id' in o)
        if a.get('reviewed_kinds') is not None:
            assert len(a['reviewed_kinds']) == 2
            r['kinds'] = a['reviewed_kinds']
            r['doors'] = [{'kind': k} for k in r['kinds']]
        observed = a.get('observed_room', a.get('visible_room'))
        if observed is not None:
            r['room'] = observed
            r['stage'] = (observed-1)//25 + 1
        # Explicit corrections are preferable to silently trusting earlier labels.
        for c in a.get('context_corrections', []):
            field = c.get('field')
            if field in {'room', 'stage', 'gems', 'run_id', 'run_class'} and 'after' in c:
                r[field] = c['after']
        assert all(v is None or isinstance(v, bool) for v in r['sides'])
        r['review'] = a
        r['review_valid'] = a.get('identity_verified') is True and a.get('eligible') is True
        out.append(r)
    return out


def matched_summary(lookup, reviewed_by_id, plan):
    data = read(DIR/'matched-entries-reviewed.json')
    matched = data['rows']
    expected = {r['id'] for r in plan['matched_entry_rows']}
    assert len(matched) == len({r['id'] for r in matched})
    assert {r['id'] for r in matched} <= expected
    selected = {'chosen_trapped': [], 'chosen_untrapped_with_other_trapped': [], 'neither_trapped': []}
    selected_neutral = {k: [] for k in selected}
    unresolved = Counter()
    cross_review_disagreements = []
    for r in matched:
        if r.get('identity_verified') is not True or r.get('eligible') is not True:
            unresolved['identity_or_eligibility_unresolved'] += 1
            continue
        flags = [r.get('trap_left'), r.get('trap_right')]
        if r['id'] in reviewed_by_id and flags != reviewed_by_id[r['id']]['sides']:
            cross_review_disagreements.append({'id': r['id'], 'offer_review': reviewed_by_id[r['id']]['sides'], 'entry_review': flags})
            unresolved['conflicting_trap_reviews'] += 1
            continue
        side = {'left': 0, 'right': 1}.get(r.get('chosen_side'))
        if side is None:
            unresolved['selected_side_unknown'] += 1
            continue
        if any(v is None for v in flags):
            unresolved['trap_status_unknown'] += 1
            continue
        key = ('chosen_trapped' if flags[side] else
               'chosen_untrapped_with_other_trapped' if flags[1-side] else 'neither_trapped')
        selected[key].append(r.get('monster_on_entry'))
        if neutral(normalize_offer(lookup[r['id']])):
            selected_neutral[key].append(r.get('monster_on_entry'))
    return {'planned': len(expected), 'reviewed': len(matched), 'unreviewed': len(expected)-len(matched),
        'status': data.get('status'), 'unresolved': dict(unresolved),
        'monster_rates': {k: stat(v) for k,v in selected.items()},
        'no_generation_modifier_monster_rates': {k: stat(v) for k,v in selected_neutral.items()},
        'cross_review_disagreements': cross_review_disagreements,
        'limitation': 'User deprioritized this audit after 30 reviews. Selected, successfully observed entries; only one interior is visible. Trap avoidance, deaths and choice-recognition missingness can bias rates. No inferred role or choice from contents.'}


def model_comparison(rows):
    seq, coin = fit_presence(rows, True), fit_presence(rows, False)
    single = stat([any_trap(core_flags(r)) for r in rows if len(core_flags(r)) == 1])
    two = stat([any_trap(core_flags(r)) for r in rows if len(core_flags(r)) == 2])
    complete = [r for r in rows if core_flags(r) and all(v is not None for v in core_flags(r))]
    a = stat([any_trap(core_flags(r)) for r in complete if len(core_flags(r)) == 1])
    b = stat([any_trap(core_flags(r)) for r in complete if len(core_flags(r)) == 2])
    return {'stop_first_any_trap': seq, 'single_coin_per_pair': coin,
        'independent_roll_trap_count': independent_roll_count_fit(rows),
        'delta_aic_stop_first_minus_single_coin': seq['aic']-coin['aic'],
        'two_eligible_prediction_using_single_eligible_rate': probabilities(single['estimate']) if single['n'] else None,
        'observed_single': single, 'observed_two': two,
        'fully_readable_comparison': {'one': a, 'two': b,
            'fisher_two_sided_p': float(fisher_exact([[a['k'], a['n']-a['k']], [b['k'], b['n']-b['k']]]).pvalue) if a['n'] and b['n'] else None},
        'source_specific_p': stratified_presence_fit(rows, 'source_id'),
        'stage_specific_p': stratified_presence_fit(rows, 'stage'),
        'identifiability': 'Fixed risky-role-first and random processing order have identical any-trap probabilities. Independent rolls also have the same any-trap probabilities, but allow double traps. Homogeneous p and complete-case likelihood; source/stage/gem effects may confound the comparison. AIC is relative model fit, not a probability that server code uses a model.'}


def acceptable(r):
    return exclusion(r) is None and r.get('review_valid', True)


def public_row(r):
    return {k: r.get(k) for k in ('id', 'source_id', 'source_seconds', 'era', 'batch', 'phase',
        'room', 'stage', 'gems', 'stones_known', 'run_id', 'run_class', 'kinds', 'sides', 'review_valid')}


def build_report():
    integrity = read(DIR/'evidence-validation.json')
    assert not integrity['failures'], 'Evidence integrity check failed'
    assert integrity.get('annotation_sha256'), 'Run validate_trap_order_evidence.py first'
    for filename, expected in integrity['annotation_sha256'].items():
        assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest() == expected, 'Stale integrity check; rerun validate_trap_order_evidence.py: '+filename
    base, offers, excluded = baseline()
    old = read(ROOT/'data/vods/full-entry-linkage-2026-10-08.jsonl')
    lookup = {r['observation_id']: r for r in old} | {r['id']: r for r in offers}
    plan = read(DIR/'frozen-plan.json')
    expanded = read(DIR/'expanded-plan.json')
    mm = load_review('monster-mystery-reviewed.json', lookup, [r for r in plan['rows'] if r['assigned_cohort']=='monster_mystery'])
    uu = load_review('double-mystery-reviewed.json', lookup, [r for r in plan['rows'] if r['assigned_cohort']=='double_mystery'])
    reviewed = mm + uu
    for task, filename in [('one_extra','one-extra-reviewed.json'), ('two_extra','two-extra-reviewed.json'),
                           ('role_extra','role-extra-reviewed.json'), ('role_root','role-root-reviewed.json')]:
        reviewed += load_review(filename, lookup, [r for r in expanded['rows'] if r['task']==task and not r['reuse_existing']])
    duplicates = read(DIR/'duplicates.json')['rows'] if (DIR/'duplicates.json').exists() else []
    assert all(r['verified'] for r in duplicates), 'Unverified aliases cannot collapse observations'
    aliases = {r['alias_id']: r['canonical_id'] for r in duplicates}
    assert not set(aliases) & set(aliases.values()), 'Flatten alias chains explicitly before analysis'
    reserve = read(DIR/'reserve-plan.json')['rows'] if (DIR/'reserve-plan.json').exists() else []
    if reserve:
        reviewed += load_review('reserve-reviewed.json', lookup, reserve)
    assert len(reviewed) == len({r['id'] for r in reviewed})
    byid = {r['id']: r for r in base} | {r['id']: r for r in reviewed}
    cohort_plans = {c: [r for r in expanded['rows'] if r['cohort']==c] + [r for r in reserve if r['cohort']==c]
                    for c in ('one_eligible', 'two_eligible')}
    cohorts = {c: deduplicate([byid[r['id']] for r in rs], aliases) for c,rs in cohort_plans.items()}
    assert all(len(rs)==200 for rs in cohorts.values()), 'Complete the frozen 200 unique-offer cohorts before publishing'
    primary_all = [r for rs in cohorts.values() for r in rs if acceptable(r)]
    primary = [r for r in primary_all if r['stones_known']]
    assert len(primary_all) == len({aliases.get(r['id'],r['id']) for r in primary_all})
    sample = [r for r in cohorts['two_eligible'] if acceptable(r)]
    # Role census overlaps the prevalence study; never append it to prevalence counts.
    role_ids = set(expanded['prior_role_ids']) | {r['id'] for r in expanded['rows'] if r['cohort']=='monster_mystery'}
    role_candidates = deduplicate([byid[i] for i in sorted(role_ids)], aliases)
    role_rows = [r for r in role_candidates if acceptable(r) and sorted(r['kinds'])==['monster','mystery']]
    current_role = [r for r in role_rows if r['era']=='2026']
    role = roles(current_role)
    model = model_comparison(primary)
    p = model['stop_first_any_trap']['p']
    role['predicted_first_role_share_at_fitted_p'] = 1/(2-p)
    role['two_sided_binomial_p_vs_fixed_order_prediction'] = binomtest(role['k'],role['n'],1/(2-p)).pvalue if role['n'] else None
    sample_size = approx_role_sample_size()
    sample_size['approx_total_at_observed_resolved_trapped_fraction'] = math.ceil(sample_size['trapped_role_revealing_pairs']*role['all_offers']/role['n']) if role['n'] else None
    # Earlier probability samples provide sensitivity, not an independent replication.
    combined = {r['id']:r for r in base}
    corrections = []
    for r in primary_all:
        previous = combined.get(r['id'])
        if previous and previous['sides'] != r['sides']:
            corrections.append({'id':r['id'],'before':previous['sides'],'after':r['sides']})
        combined[r['id']] = r
    combined = [r for r in deduplicate(list(combined.values()),aliases) if acceptable(r)]
    fully_two = [r for r in primary_all if len(core_flags(r))==2 and all(v is not None for v in core_flags(r))]
    all_valid_reviewed = deduplicate([r for r in reviewed if acceptable(r)], aliases)
    all_readable_two = [r for r in all_valid_reviewed if len(core_flags(r))==2 and all(v is not None for v in core_flags(r))]
    corrections_kind = []
    for r in reviewed:
        original = normalize_offer(lookup[r['id']], 'observation_id' in lookup[r['id']])
        if r['kinds'] != [d['kind'] for d in original['doors']]:
            corrections_kind.append({'id':r['id'],'before':[d['kind'] for d in original['doors']],'after':r['kinds']})
    report = {'schema': 2, 'date': '2026-10-12',
        'scope': 'Ordinary rooms after room 5, excluding boss/shop/trial/forced encounters. Monster and mystery options form the core eligible set; physical doors/walls do not dilute it. Golden options are monitored separately.',
        'evidence_integrity': integrity,
        'cohort_flow': {c:{'raw_planned_and_reserve':len(cohort_plans[c]),'unique_reviewed':len(rs),
            'valid_scope':sum(acceptable(r) for r in rs),'known_stones':sum(acceptable(r) and r['stones_known'] for r in rs),
            'reviewed_eligible_counts':dict(Counter(str(len(core_flags(r))) for r in rs))} for c,rs in cohorts.items()},
        'sampling': expanded['sampling'], 'verified_duplicate_visits': duplicates,
        'baseline_stricter_scope': summary(base), 'scope_exclusions': excluded,
        'primary_latest': summary(primary),
        'primary_including_unknown_stones': summary(primary_all),
        'primary_no_generation_modifiers': summary([r for r in primary if neutral(r)]),
        'primary_no_stones': summary([r for r in primary if r['stones_known'] and not r['gems']]),
        'new_two_eligible_sample': summary(sample),
        'descriptive_combined_deduplicated': summary(combined),
        'cross_review_corrections': corrections,
        'offer_kind_corrections': corrections_kind,
        'presence_model_comparison': model,
        'mystery_only_presence_model_comparison': model_comparison([r for r in primary if 'monster' not in r['kinds']]),
        'role_census_monster_mystery': role,
        'role_flow': {'candidates':len(role_candidates),'current_candidates':sum(r['era']=='2026' for r in role_candidates),
            'confirmed_current':len(current_role),'confirmed_historical':sum(r['era']=='2024' for r in role_rows),
            'out_of_scope_or_corrected': [{'id':r['id'],'kinds':r['kinds'],'exclusion':exclusion(r),
                'review_valid':r['review_valid']} for r in role_candidates if r not in role_rows]},
        'role_by_era': {era:roles([r for r in role_rows if r['era']==era]) for era in ('2024','2026')},
        'role_current_known_stones': roles([r for r in current_role if r['stones_known']]),
        'role_current_no_generation_modifiers': roles([r for r in current_role if neutral(r)]),
        'role_by_source': {sid:roles([r for r in role_rows if r['source_id']==sid]) for sid in sorted({r['source_id'] for r in role_rows})},
        'role_by_stage': {str(stage):roles([r for r in current_role if r['stage']==stage]) for stage in (1,2,3,4)},
        'role_by_exact_gems': {g:roles([r for r in current_role if gem_label(r)==g]) for g in sorted({gem_label(r) for r in current_role})},
        'double_traps': stat([all(core_flags(r)) for r in fully_two]),
        'double_traps_all_reviewed_descriptive': stat([all(core_flags(r)) for r in all_readable_two]),
        'matched_entry_cohort': matched_summary(lookup,byid,plan),
        'illustration_p25_m90': probabilities(.25, .9),
        'role_test_sample_size': sample_size,
        'simulator_at_current_p29': probabilities(.29),
        'simulator_processing_order': 'Random eligible-side priority after display shuffle, stopping after first success; no monster-role priority. Calibration unchanged by this report.',
        'limitations': ['Observational, availability-based VOD corpus, not a random sample of players.',
            'Single-eligible cohort reuses 97 earlier source-balanced observations and adds 103 outcome-blind ranked observations; source mix differs from two-eligible cohort.',
            'Wilson intervals and likelihood comparisons use iid approximations; source/bootstrap sensitivities do not remove shared streamer, account or run dependence.',
            'Unknown traps remain missing and can be nonrandom due to facecam placement; frame sampling can miss between-frame appearances.',
            'Five historical monster/mystery candidates cannot establish that generations are unchanged across years.',
            'No server generation order, hidden interior or rejected door outcome is directly observed.'],
        'inputs_sha256': INPUTS,
        'primary_observation_ids': [r['id'] for r in primary],
        'deduplicated_prevalence_ids': [r['id'] for r in combined],
        'primary_rows': [public_row(r) for r in primary_all],
        'role_rows': [public_row(r) for r in role_rows]}
    return report


def main():
    report = build_report()
    target = ROOT/'docs/trap-order-2026-10-12.json'
    target.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    from render_trap_order_2026_10_12 import render
    (ROOT/'docs/TRAP_ORDER_2026_10_12.md').write_text(render(report),encoding='utf-8')
    print(json.dumps({'primary':report['primary_latest']['strata'],
        'role':report['role_census_monster_mystery'],
        'delta_aic':report['presence_model_comparison']['delta_aic_stop_first_minus_single_coin'],
        'double_traps':report['double_traps']},indent=2))


if __name__ == '__main__':
    main()
