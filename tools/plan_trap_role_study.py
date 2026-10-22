"""Reproducible fixed-sample planning for monster/mystery trap placement.

This is a prospective binomial calculation, not a fit to unreviewed videos.
The significance level, detection power and confidence-interval width are
different quantities. A finite sample cannot establish an exact server value.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def rejection_bounds(n, alpha=.05, sides=2):
    from scipy.stats import binom
    if type(n) is not int or n < 1 or not 0 < alpha < 1 or sides not in (1,2):
        raise ValueError('Expected a positive integer sample, alpha in (0,1), and one or two sides.')
    upper=int(binom.isf(alpha/sides,n,.5))+1
    return (n-upper if sides==2 else -1),upper


def power(n, alternative=4/7, alpha=.05, sides=2):
    from scipy.stats import binom
    lower,upper=rejection_bounds(n,alpha,sides)
    return float(binom.cdf(lower,n,alternative)+binom.sf(upper-1,n,alternative))


def sample_size(target_power, alternative=4/7, alpha=.05, sides=2, maximum=20000):
    import numpy as np
    from scipy.stats import binom
    if not 0 < target_power < 1 or not .5 < alternative < 1 or not 0 < alpha < 1 or sides not in (1,2):
        raise ValueError('Invalid power, directional alternative or test specification.')
    n=np.arange(1,maximum+1)
    upper=binom.isf(alpha/sides,n,.5).astype(int)+1
    lower=n-upper if sides==2 else np.full(n.shape,-1)
    values=binom.cdf(lower,n,alternative)+binom.sf(upper-1,n,alternative)
    hits=np.flatnonzero(values>=target_power)
    if not len(hits):raise ValueError('Required sample exceeds the search limit.')
    index=int(hits[0]);count=int(n[index])
    return {'n_trapped_pairs':count,'actual_power':float(values[index]),
            'rejection_bounds':list(rejection_bounds(count,alpha,sides)),
            'actual_type_one_error':power(count,.5,alpha,sides)}


def build_plan():
    from scipy.stats import norm
    p=4/7
    tables=[]
    for alpha in (.05,.01):
        for sides in (1,2):
            for target in (.8,.95,.99):
                item=sample_size(target,p,alpha,sides)
                item.update(alpha=alpha,sides=sides,target_power=target,
                            total_monster_mystery_at_pair_trap_25=item['n_trapped_pairs']*4,
                            total_monster_mystery_at_prior_readable_rate=math.ceil(item['n_trapped_pairs']*214/43))
                tables.append(item)
    return {'schema':1,'date':'2026-10-12','status':'prospective planning, not new outcome evidence',
            'working_pair_trap_probability':.25,
            'working_value_evidence':'Prior ordinary-room audit: pooled shared-pair model MLE 0.2467866, conditional 95% interval approximately 0.2057–0.2912. Rounded 25% is provisional.',
            'simulator_changed_by_this_tool':False,
            'null_monster_share':.5,'alternative_monster_share':p,
            'models':{'sequential_p25':{'one_eligible_any_trap':.25,'two_eligible_any_trap':.4375,'monster_share':p},
                      'shared_coin_p25_uniform_placement':{'one_eligible_any_trap':.25,'two_eligible_any_trap':.25,'monster_share':.5},
                      'shared_coin_p25_biased_placement':{'one_eligible_any_trap':.25,'two_eligible_any_trap':.25,'monster_share':p,
                          'note':'This additional model is possible, but 25% does not imply its placement bias.'}},
            'prior_reviewed_current_roles':{'trapped_complete':43,'monster':24,'mystery':19,'all_pairs':214,
                                           'wilson95_monster_share':[.411076,.695667]},
            'power_table':tables,
            'approximate_precision95':[{'half_width':e,'n_trapped_pairs':math.ceil(norm.ppf(.975)**2*p*(1-p)/(e*e))} for e in (.05,.03,.02,.01)],
            'collection_rules':[
                'Select footage and monster/mystery candidates without conditioning on trap presence or placement.',
                'Treat detector candidates as unverified; inspect stable native frames plus entry/exit context.',
                'Deduplicate room-choice visits using counter, health, keys, effects and stones; repeated frame matches are not independent observations.',
                'Exclude rooms 1–5 on every run, bosses, shops, stone selections, trials and forced encounters from the ordinary eligible-room study.',
                'Record Standard/Ultimate mode as a stratum. Playa describes identical gameplay with larger Ultimate rewards, so Ultimate offers can inform trap generation; do not count a triple reward as three encounters. Keep source, event/year, run class and stones separate; unknown context remains unknown.',
                'Both sides must be readable for the primary placement comparison. Show partially readable cases only as a sensitivity analysis.',
                'Choose the fixed sample/test before inspecting the new placement counts; do not stop as soon as p<0.05.',
                'Nominal power assumes independent, identically distributed encounters. Cluster by source/run and check era heterogeneity before pooling older footage.',
                'The review queue is targeted and does not estimate the prevalence of monster/mystery pairs among all room choices.'
            ]}


def main():
    data=build_plan()
    target=ROOT/'data/vods/trap-role-study-plan-2026-10-12.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([r for r in data['power_table'] if r['sides']==2 and r['alpha']==.05],indent=2))


if __name__=='__main__':main()
