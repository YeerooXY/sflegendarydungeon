"""Final-stage stone sets, with legal offer histories and shared futures."""
from itertools import combinations
from math import comb
import random

KEYS = [0, 1, 3, 5, 10]
METRICS = ['hours', 'completed', 'deaths', 'mushrooms', 'actions', 'deathless']


def catalog(profile, history_seed, later_histories=8):
    """All final triples; historical menus do not identify which was chosen last.

    First-run triples have exactly seven possible six-rejection sets; cover all.
    Later-run triples use a frozen panel of uniformly sampled legal histories.
    The same random priority orders are used across triples for fair pairing.
    """
    result=[]
    for run, field in ((1, 'gems_first_run'), (2, 'gems_later_runs')):
        pool=profile[field].split(',')
        orders=[]
        for h in range(later_histories):
            order=list(pool)
            random.Random(f'final-stones-history:{history_seed}:{run}:{h}').shuffle(order)
            orders.append(order)
        for triple in combinations(pool, 3):
            remaining=[g for g in pool if g not in triple]
            histories=([list(x) for x in combinations(remaining, 6)] if run==1
                       else [[g for g in order if g not in triple][:6] for order in orders])
            result.append(dict(id=len(result), run=run, stones=list(triple), histories=histories))
    return result


def menu(entry, history, last=None):
    stones=entry['stones'];last=last or stones[-1];rejected=entry['histories'][history]
    if last not in stones or len(set(stones+rejected))!=9:
        raise ValueError('Invalid final set or offer history')
    return dict(run=entry['run'], held=[g for g in stones if g!=last],
                offered=[last]+rejected[-2:], rejected=rejected[:4])


def counts(profile):
    rows=[]
    for run, field in ((1, 'gems_first_run'), (2, 'gems_later_runs')):
        n=len(profile[field].split(','));pairs=comb(n,2)
        rows.append(dict(run=run, stones=n, held_pairs=pairs, final_sets=comb(n,3),
                         potential_additions=n-2, available_after_four_rejections=n-6,
                         pair_key_states=pairs*len(KEYS), candidate_states=pairs*(n-2)*len(KEYS)))
    return rows
