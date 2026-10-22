"""Compare generator hypotheses; observations do not reveal server execution order.

Offline analysis uses NumPy/SciPy. Does not change the simulator profile or engine.
"""
from __future__ import annotations

from collections import Counter
import json
import math

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from summarize_vod_cumulative_2026_10_12 import (
    ROOT, NEW, HISTORICAL, build, normalize_offer, ordinary_new, neutral, read,
)
from summarize_vod_review_2026_10_12 import PAIR_ORDER, pair, wilson
from vod_calibration_evidence import trap_fit


# D=physical alternative (including wall), E=visible enemy, U=mystery, G=golden.
TYPES = ['physical', 'monster', 'mystery', 'golden']
PAIR_INDICES = [(0, 2), (0, 1), (2, 2), (0, 3), (1, 2), (3, 2), (3, 1), (3, 3)]


def weights(parameters):
    z = np.append(parameters, 0.)
    z -= z.max()
    w = np.exp(z)
    return w / w.sum()


def sequential_probabilities(parameters):
    """Same weights both draws; forbid DD/EE, renormalize second draw, then shuffle."""
    w = weights(parameters)
    result = []
    for a, b in PAIR_INDICES:
        p = w[a] * w[b] / (max(1e-15, 1 - w[a]) if a < 2 else 1)
        if a != b:
            p += w[b] * w[a] / (max(1e-15, 1 - w[b]) if b < 2 else 1)
        result.append(p)
    return np.asarray(result)


def fit_sequential(counts):
    c = np.asarray(counts, dtype=float)
    def loss(z):
        return -float(np.sum(c * np.log(np.maximum(sequential_probabilities(z), 1e-300))))
    solutions = [minimize(loss, np.asarray(start, dtype=float), method='Nelder-Mead',
                         options={'maxiter': 1000, 'xatol': 1e-8, 'fatol': 1e-8})
                 for start in ([2, 0, 1], [0, 0, 0], [3, -1, 1])]
    result = min(solutions, key=lambda r: r.fun)
    if not result.success:
        raise ValueError('Generator fit did not converge')
    p = sequential_probabilities(result.x)
    assert abs(float(p.sum()) - 1) < 1e-9
    observed = c / c.sum()
    positive = c > 0
    deviance = 2 * float(np.sum(c[positive] * np.log(observed[positive] / p[positive])))
    return {'n': int(c.sum()), 'fitted_weights': dict(zip(TYPES, map(float, weights(result.x)))),
            'observed': dict(zip(PAIR_ORDER, map(int, c))),
            'fitted_probabilities': dict(zip(PAIR_ORDER, map(float, p))),
            'expected_counts': dict(zip(PAIR_ORDER, map(float, c.sum() * p))),
            'multinomial_deviance_vs_joint_table': deviance,
            'parameter_counts': {'sequential_same_weights': 3, 'joint_pair_table': 7},
            'aic_difference_sequential_minus_joint': deviance - 8,
            'caution': 'Fit diagnostic only. Sparse cells, repeated sources and unknown contexts invalidate a simple independent-room significance claim.'}


def joint_as_conditional(probabilities):
    """Any unordered joint pair distribution has an exactly equivalent sequential form."""
    ordered = np.zeros((4, 4))
    for p, (a, b) in zip(probabilities, PAIR_INDICES):
        ordered[a, b] += p if a == b else p / 2
        if a != b:
            ordered[b, a] += p / 2
    first = ordered.sum(axis=1)
    second = np.divide(ordered, first[:, None], out=np.zeros_like(ordered), where=first[:, None] != 0)
    return first, second


def pair_counts(rows):
    c = Counter(pair(r) for r in rows)
    return [c[k] for k in PAIR_ORDER]


def two_pool_probabilities(parameters):
    enemy, mystery, golden = weights(parameters[:2])
    door, other_mystery, other_golden = weights(parameters[2:])
    return np.asarray([mystery * door, enemy * door, mystery * other_mystery,
                       golden * door, enemy * other_mystery,
                       golden * other_mystery + mystery * other_golden,
                       enemy * other_golden, golden * other_golden])


def fit_two_pools(counts):
    c = np.asarray(counts, dtype=float)
    def loss(z):
        return -float(np.sum(c * np.log(np.maximum(two_pool_probabilities(z), 1e-300))))
    solutions = [minimize(loss, np.asarray(start, dtype=float), method='Nelder-Mead',
                         options={'maxiter': 2000, 'xatol': 1e-8, 'fatol': 1e-8})
                 for start in ([2, 3, 3, 2], [1, 1, 2, 1])]
    result = min(solutions, key=lambda r: r.fun)
    if not result.success:
        raise ValueError('Two-pool fit did not converge')
    p = two_pool_probabilities(result.x)
    assert abs(float(p.sum()) - 1) < 1e-9
    positive = c > 0
    deviance = 2 * float(np.sum(c[positive] * np.log((c / c.sum())[positive] / p[positive])))
    return {'n': int(c.sum()), 'first_pool': dict(zip(['monster', 'mystery', 'golden'], map(float, weights(result.x[:2])))),
            'second_pool': dict(zip(['physical', 'mystery', 'golden'], map(float, weights(result.x[2:])))),
            'fitted_probabilities': dict(zip(PAIR_ORDER, map(float, p))),
            'expected_counts': dict(zip(PAIR_ORDER, map(float, p * c.sum()))),
            'multinomial_deviance_vs_joint_table': deviance,
            'parameter_counts': {'two_pool': 4, 'joint_pair_table': 7},
            'aic_difference_two_pool_minus_joint': deviance - 6}


def fit_shared_monster_probability(wall, double):
    """A first-pool mystery has monster probability p; second-pool mystery zero.

    A randomly chosen double-mystery option then has probability p/2. This is a
    model-conditional fit; the unchosen interior is never observed in these VODs.
    """
    def ll(p):
        total = 0.
        for s, scale in [(wall, 1.), (double, .5)]:
            q = min(1 - 1e-15, max(1e-15, p * scale))
            total += s['k'] * math.log(q) + (s['n'] - s['k']) * math.log1p(-q)
        return total
    p = float(minimize_scalar(lambda p: -ll(p), bounds=(1e-12, 1 - 1e-12), method='bounded',
                              options={'xatol': 1e-12}).x)
    cutoff = ll(p) - 1.920729410347062
    a, b = 0., p
    for _ in range(80):
        mid = (a + b) / 2
        if ll(mid) < cutoff: a = mid
        else: b = mid
    low = (a + b) / 2
    a, b = p, 1.
    for _ in range(80):
        mid = (a + b) / 2
        if ll(mid) < cutoff: b = mid
        else: a = mid
    return {'p': p, 'profile_likelihood_95': [low, (a + b) / 2],
            'predicted_monster_beside_wall': p, 'predicted_monster_double_mystery': p / 2,
            'wall': {'k': wall['k'], 'n': wall['n']}, 'double_mystery': {'k': double['k'], 'n': double['n']},
            'limitations': 'Requires a fair choice between the hidden roles and comparable contexts. Good marginal fit cannot establish that both hidden interiors were generated or that at most one contains a monster.'}


def main():
    cumulative = build()
    old = read('data/vods/full-entry-linkage-2026-10-08.jsonl')
    new = read('data/vods/reviewed-offers-2026-10-12.jsonl')
    offers = [normalize_offer(r, True) for r in old if r['source_id'] != HISTORICAL]
    offers += [normalize_offer(r) for r in new if ordinary_new(r)]
    baseline = [r for r in offers if neutral(r)]
    counts = pair_counts(baseline)
    fit = fit_sequential(counts)
    two_pool = fit_two_pools(counts)
    first, second = joint_as_conditional(np.asarray(counts) / sum(counts))
    stages = {str(s): fit_sequential(pair_counts([r for r in baseline if r['stage'] == s])) for s in [1, 2, 3, 4]}

    # Every source is withheld in turn; the empirical model is smoothed to avoid
    # declaring a never-before-seen rare pair impossible on a held-out source.
    heldout = []
    for sid in sorted({r['source_id'] for r in baseline}):
        train = np.asarray(pair_counts([r for r in baseline if r['source_id'] != sid]))
        test = np.asarray(pair_counts([r for r in baseline if r['source_id'] == sid]))
        f = fit_sequential(train)
        role_fit = fit_two_pools(train)
        p = np.asarray([f['fitted_probabilities'][k] for k in PAIR_ORDER])
        role_p = np.asarray([role_fit['fitted_probabilities'][k] for k in PAIR_ORDER])
        joint = (train + .5) / (train.sum() + 4)
        heldout.append({'source_id': sid, 'n': int(test.sum()),
                        'sequential_negative_log_likelihood': -float(np.sum(test * np.log(p))),
                        'two_pool_negative_log_likelihood': -float(np.sum(test * np.log(role_p))),
                        'joint_negative_log_likelihood': -float(np.sum(test * np.log(joint)))})

    old_lookup = {r['observation_id']: r for r in old}
    new_lookup = {(r['source_id'], r['index']): r for r in new}
    entries, reviews = {}, []
    for r in read('data/vods/full-trap-blind-2026-10-08.jsonl'):
        o = old_lookup[r['observation_id']]
        if o['source_id'] != HISTORICAL:
            entries[r['observation_id']] = o
            reviews.append(r)
    for r in read(NEW + 'trap-validation--dense-reviewed.json')['rows']:
        if r['eligible']:
            o = new_lookup[(r['source_id'], r['index'])]
            entries[o['id']] = {'offered_kinds': [d['kind'] for d in r['reviewed_doors']], 'context': {'gems': r['gems']}}
            reviews.append({'observation_id': o['id'], 'sides': [r['trap_left'], r['trap_right']]})
    trap_model = trap_fit(reviews, entries)
    strata = trap_model['eligible_side_strata']
    one_or_more = sum(s['pairs'] for s in strata if s['eligible_sides'])
    trap_pair_coin = trap_model['trap_pairs'] / one_or_more
    eligible_intervals = [{**s, 'estimate': s['trap_pairs'] / s['pairs'] if s['pairs'] else None,
                           'wilson_95': wilson(s['trap_pairs'], s['pairs'])} for s in strata]
    complete_two = [r for r in reviews if sum(k in {'monster', 'mystery'} for k in entries[r['observation_id']]['offered_kinds']) == 2
                    and all(v is not None for v in r['sides'])]
    doubles = sum(all(v is True for v in r['sides']) for r in complete_two)
    shared_monster = {name: fit_shared_monster_probability(cumulative['mystery']['wall'][name]['monster'],
                                                         cumulative['mystery']['mystery'][name]['monster'])
                      for name in ['route_verified_without_outcome_exclusion', 'no_known_generation_modifier']}
    monster_mystery_traps = [r for r in reviews if sorted(entries[r['observation_id']]['offered_kinds']) == ['monster', 'mystery']]
    role_trap = {'pairs': len(monster_mystery_traps),
                 'fully_readable_pairs': sum(all(v is not None for v in r['sides']) for r in monster_mystery_traps),
                 'trap_on_monster': sum(k == 'monster' and v is True for r in monster_mystery_traps for k, v in zip(entries[r['observation_id']]['offered_kinds'], r['sides'])),
                 'trap_on_mystery': sum(k == 'mystery' and v is True for r in monster_mystery_traps for k, v in zip(entries[r['observation_id']]['offered_kinds'], r['sides']))}
    counterexamples = read('data/vods/monster-mystery-trap-counterexamples-2026-10-12.json')
    assert {r['verified_positive_trap_kind'] for r in counterexamples['rows']} == {'monster', 'mystery'}
    report = {'schema': 1, 'date': '2026-10-12', 'scope': 'Hypothesis comparison, not recovered server source code.',
              'per_option_traps': cumulative['traps'], 'stop_after_first_trap_fit': trap_model,
              'eligible_count_strata': eligible_intervals,
              'one_trap_coin_per_pair_fit': {'p': trap_pair_coin, 'condition': 'at least one monster/mystery option'},
              'double_traps': {'k': doubles, 'n': len(complete_two), 'wilson_95': wilson(doubles, len(complete_two))},
              'sequential_same_weights': fit, 'sequential_by_stage': stages,
              'two_distinct_pools': two_pool,
              'two_pools_by_stage': {str(s): fit_two_pools(pair_counts([r for r in baseline if r['stage'] == s])) for s in [1, 2, 3, 4]},
              'shared_monster_hypothesis': shared_monster, 'trap_on_first_pool_hypothesis_discriminator': role_trap,
              'first_pool_only_traps_status': 'contradicted_by_direct_native_counterexample',
              'trap_side_counterexamples': counterexamples,
              'no_stones_only': fit_sequential(pair_counts([r for r in offers if r['stones_known'] and not r['gems']])),
              'source_held_out_comparison': heldout,
              'conditional_representation_of_empirical_pair_table': {
                  'first_type_probability': dict(zip(TYPES, map(float, first))),
                  'second_type_given_first': {k: dict(zip(TYPES, map(float, second[i]))) for i, k in enumerate(TYPES)},
                  'interpretation': 'Constructed equivalent representation, not identification of the real first-generated type. Both have the same observed pair distribution.'},
              'official_documentation_conflict': {
                  'url': 'https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/282-legendary-dungeon---doors/',
                  'checked': '2026-10-12', 'finding': 'Official help includes golden-door traps; working engine excludes them. Seven readable negative golden options cannot resolve the conflict.'},
              'inputs_sha256': cumulative['inputs_sha256']}
    output = ROOT / 'docs/room-generation-2026-10-12.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Room-generation hypotheses and per-option traps', '',
             'This analysis compares observable consequences of possible generators. It does not identify the actual server code or change the simulator.', '',
             '## Trap denominator', '', '| Individual offered option | Traps / readable options | Rate | 95% Wilson interval |', '|---|---:|---:|---:|']
    for label, s in [('monster or mystery', cumulative['traps']['per_individual_monster_or_mystery_option']),
                     ('monster', cumulative['traps']['by_room_kind']['monster']),
                     ('mystery', cumulative['traps']['by_room_kind']['mystery']),
                     ('all positions, including physical doors and walls', cumulative['traps']['per_individual_offered_position_including_walls'])]:
        lo, hi = s['wilson_95']
        lines.append(f"| {label} | {s['k']}/{s['n']} | {s['estimate']:.1%} | {lo:.1%}-{hi:.1%} |")
    lo, hi = trap_model['profile_likelihood_95']
    lines += ['', 'These are marginal rates among *offered* options. They do not measure the hazard among player-selected entries, since players can avoid visible traps. Eight monster/mystery labels remain unreadable. Source sensitivity and missingness are in the JSON; a narrow binomial interval does not remove recognition or clustering error.', '',
              f"The engine currently rolls each eligible option in random order and stops at the first trap. Fitting that specific model gives an underlying roll probability **{trap_model['eligible_side_probability_mle']:.1%}**, with a model-conditional profile-likelihood interval **{lo:.1%}-{hi:.1%}**. That latent roll probability is not the observed per-option frequency.", '',
              'If the underlying roll probability is p and two choices are eligible, this algorithm gives P(any trap)=1-(1-p)^2 and marginal P(trap on a particular shuffled choice)=p-p^2/2. With only one eligible choice its chance is p. An alternative is one trap coin per pair, followed by choosing one eligible slot. The pair-count strata below can distinguish these hypotheses with more data.', '',
              '| Eligible monster/mystery options | Pairs with a trap / resolved pairs | Rate | 95% interval |', '|---|---:|---:|---:|']
    for s in eligible_intervals:
        lo, hi = s['wilson_95']
        lines.append(f"| {s['eligible_sides']} | {s['trap_pairs']}/{s['pairs']} | {s['estimate']:.1%} | {lo:.1%}-{hi:.1%} |")
    lines += ['', f"Two traps are seen in {doubles}/{len(complete_two)} fully readable pairs with two eligible choices. This sample is too small to establish an absolute one-trap maximum from zero sightings alone. That maximum remains a working rule.", '',
              'Golden rooms are unresolved: [official door documentation](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/282-legendary-dungeon---doors/) includes golden-door traps, while our working engine excludes them. We have 0/7 readable golden options (five more unreadable). Keep them separate and prioritize footage review; do not silently change the engine or infer zero from this sample.', '',
              '## Sequential generator: same weights, then exclusions', '',
              'Tested hypothesis: draw a first family from fixed weights for physical door, monster, mystery and golden; exclude another physical door after a physical door, or another monster after a monster; renormalize the same weights for the second draw; then randomly swap left/right. Double mystery and double golden remain allowed.', '',
              f"Fit to {fit['n']} offers without known generation modifiers. This three-parameter model has visible discrepancies even after fitting its best weights:", '',
              '| Pair | Observed | Same-weight sequential prediction | Two distinct pools prediction |', '|---|---:|---:|---:|']
    for k in PAIR_ORDER:
        lines.append(f"| {k} | {fit['observed'][k]} ({fit['observed'][k]/fit['n']:.1%}) | {fit['expected_counts'][k]:.1f} ({fit['fitted_probabilities'][k]:.1%}) | {two_pool['expected_counts'][k]:.1f} ({two_pool['fitted_probabilities'][k]:.1%}) |")
    joint_loss = sum(r['joint_negative_log_likelihood'] for r in heldout)
    sequential_loss = sum(r['sequential_negative_log_likelihood'] for r in heldout)
    two_pool_loss = sum(r['two_pool_negative_log_likelihood'] for r in heldout)
    lines += ['', f"Leaving each video source out in turn, the joint pair table has a negative log likelihood of {joint_loss:.2f}, versus {sequential_loss:.2f} for the fixed-weight sequential model (lower is better). The joint table uses a 0.5-count smoothing prior per pair. Sources can share streamers/characters; this checks prediction on held-out source IDs, not independent future-event performance.", '',
              'Per-stage and no-stone fits are also saved. Important discrepancies persist in the first three stages, so merely mixing stage-specific base weights is not an adequate explanation of all the mismatches. Unknown event-run/mode differences and detection errors remain possible confounders. This weakens the simplest same-weights model, not every sequential generator.', '',
              '## A more promising two-pool hypothesis', '',
              'Draw one option from **monster / mystery / golden** and the other from **physical door / mystery / golden**, then shuffle. This automatically excludes physical/physical and monster/monster while allowing double mystery and double golden. The four fitted parameters reproduce the ordinary pair frequencies closely:', '',
              '| Pool | Fitted family probabilities |', '|---|---|',
              '| First | ' + ', '.join(f'{k}: {v:.2%}' for k, v in two_pool['first_pool'].items()) + ' |',
              '| Second | ' + ', '.join(f'{k}: {v:.2%}' for k, v in two_pool['second_pool'].items()) + ' |', '',
              f"The multinomial deviance against a fully flexible seven-parameter pair table is {two_pool['multinomial_deviance_vs_joint_table']:.2f}, versus {fit['multinomial_deviance_vs_joint_table']:.2f} for the same-weight sequential model. Held-out-source negative log likelihood is **{two_pool_loss:.2f}** for two pools, **{joint_loss:.2f}** for the pair table and **{sequential_loss:.2f}** for same-weight sequential draws. Lower is better. This hypothesis was developed while looking at the full corpus, so this comparison is exploratory; freeze it and test on previously unreviewed sources before claiming confirmation.", '',
              'The hidden-content version says: a mystery from the first pool can contain an ordinary monster; a mystery from the second cannot start with one. With a first-pool monster probability near 90%, this predicts about 90% beside a physical door/wall, about 45% for a fairly chosen double mystery, and zero beside a visible monster. Special doors that independently contain enemies and modifier stones require separate handling.', '',
              f"For the no-generation-modifier cohort, mystery/wall is 56/63 = 88.9% and double mystery is 114/257 = 44.4%. A shared-parameter fit gives p={shared_monster['no_known_generation_modifier']['p']:.1%}. This is a compact explanation of the observed marginals, **not proof of a one-monster limit across unobserved interiors**. The recorded player sees only the chosen interior; trap-aware choices could bias which latent role was entered.", '',
              'A useful new prediction is mystery beside golden: with the fitted pool mixture and p=90%, around ' +
              f"{.9 * two_pool['first_pool']['mystery'] * two_pool['second_pool']['golden'] / two_pool['fitted_probabilities']['golden/mystery']:.1%}" +
              ' would contain a monster. This is a prediction to test, not an observed rate.', '',
              f"The strict first-pool-only trap rule is now **contradicted**. The original blind monster/mystery sample had only {role_trap['fully_readable_pairs']} fully readable pairs, with {role_trap['trap_on_monster']} trapped monsters and {role_trap['trap_on_mystery']} trapped mysteries; that small sample missed a counterexample already present elsewhere in the reviewed footage.", '',
              '- [03:00:55 in VOD 2878439002](https://www.twitch.tv/videos/2878439002?t=10855s): left mystery has a hanging spike trap; right alternative is a visible monster. Room 45, Time Traveler active.',
              '- [03:09:10 in the same VOD](https://www.twitch.tv/videos/2878439002?t=11350s): left visible monster has an axe trap; right alternative is a mystery. Room 23, no stones.', '',
              'Both were directly reinspected in native images, whose hashes were checked. These targeted existence examples do not add new independent observations to the trap-rate denominator. Their source IDs and proof hashes are in `data/vods/monster-mystery-trap-counterexamples-2026-10-12.json`.', '',
              'Within the two-pool hypothesis, either role must be eligible for traps. This preserves the possible content-role distinction but rules out using a trap as a perfect role label. Unequal trap probabilities or correlations with hidden content remain possible; neither equality nor independence has been established.', '',
              'Even if an unshuffled role mapping could be recovered, it would identify a monster-capable mystery (about 90% monster in this hypothesis) and a mystery that starts without a monster. It would not identify a certain monster. The role model and a recoverable pre-presentation mapping both need independent validation. Left/right appearance alone cannot recover a fair hidden shuffle.', '',
              '## A realistic simulator structure', '',
              '1. Establish event, run class, room/stage, selected stones, active effects and progression flags.',
              '2. Resolve special scheduling: opening protection, compulsory shops, trial state and bosses. A shop can be sampled once within a stage window; alternatively use a matching conditional hazard. Ordinary independent shop rolls cannot guarantee a quota.',
              '3. Generate a compatible ordinary pair. Test the two-pool model against the observed joint pair table and a conditional second-family table. Enforce supported exclusions, preserve observed double mystery/golden and apply stone constraints.',
              '4. Choose physical subtypes (locked, cursed, sacrifice, wall, hungry, destiny, blessing and stone-enabled variants) with context-specific weights. Recheck any subtype-dependent constraints; availability safeguards are hypotheses, not known code.',
              '5. Determine hidden interiors conditional on both alternatives, stone set and run context. A universal mystery pool does not reproduce wall/mystery versus double-mystery observations.',
              '6. Attach traps using an explicit eligible-set and shared-pair rule. Trap placement can happen after both choices unless the presence of a first trap changes the second family probabilities.',
              '7. Randomize presentation order if supported by left/right balance. Generate rewards on interaction from their own conditional tables, permitting overlapping reward types where observed.', '',
              'The conditional table in the JSON exactly reproduces the empirical unordered pair table. This is a mathematical equivalence: final screenshots cannot distinguish those implementations. A 50/50 swap is observationally indistinguishable from a symmetric generator unless there are other order-dependent effects.', '',
              '## What to measure next', '',
              '- Trap frequency by each option type, its companion type, stone set and stage; count all eligible options before seeing the selected action.',
              '- Complete offer-to-entry transitions to separate mystery content pools without inferring a choice from the result.',
              '- Left/right balance and trap side versus companion type; test whether randomization removes presentation bias.',
              '- Consecutive rooms within the same character/run, recording shop-seen/trial-seen and deaths. Test dependence on previous offers after controlling stage, stones and scheduled rooms.',
              '- Hold out entire sources and future events; do not choose a generator just because it fits the same sample used to estimate its weights.', '',
              'For barrel blessing versus curse, 50/50 remains a reasonable working hypothesis: 31/54 blessings, Wilson 95% interval 44.2-69.7%. [Official chest documentation](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/283-legendary-dungeon---chests/) lists blessing and curse outcomes but does not establish equal probability. Ordinary containers are a different question: no-Greed/no-Hero blessing incidence is 121/569, 21.3% (18.1-24.8%).', '',
              'Reproduce with `python tools/analyze_room_generation_2026_10_12.py`. Requires NumPy and SciPy for this offline fit only. Full counts, fitted parameters, held-out scores and source hashes: `docs/room-generation-2026-10-12.json`.']
    (ROOT / 'docs/ROOM_GENERATION_2026_10_12.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'trap_fit': trap_model, 'two_pools': two_pool, 'shared_monster': shared_monster,
                      'heldout_two_pool_nll': two_pool_loss, 'heldout_joint_nll': joint_loss,
                      'heldout_sequential_nll': sequential_loss}, indent=2))


if __name__ == '__main__':
    main()
