# Seven-day pilot, 2026-10-05

These are **synthetic simulator results, not live-game estimates**. The pilot
tests the experiment framework and shows that different decision strategies
produce different outcomes under the same declared assumptions. The model is
not calibrated, and the comparison does not establish an optimal strategy.

## Frozen comparison

Each controller played 200 events per budget, starting fresh at run 1. Every
event lasted 168 simulated hours with one shared budget. Master seed 391864 was
unused during practice, training and the earlier exploratory comparison. All
methods used the same event-seed derivation. No action caps were hit.

The profile uses the maintainer's 11/20 first/later stone pools, with Rusty
provisionally later-only; equal eligible-stone weights; full-health restarts;
50% base escape; uniform damage; and synthetic door/room frequencies. Full-refill
pricing, additive escape modifiers and several outcome odds remain provisional.

Numbers after ± are one Monte Carlo standard error of the mean, not model-error
bounds. The two fresh-agent controllers have similar scores; this sample does
not establish that B is better than A.

| Controller | Completions, 0 mushrooms | Completions, 500 budget | Paid mean spend | Mushrooms / completion |
| --- | ---: | ---: | ---: | ---: |
| Hand-written baseline | 3.000 ± 0.064 | 5.180 ± 0.081 | 495.31 | 95.62 |
| Learned linear policy | 2.440 ± 0.062 | 6.915 ± 0.099 | 495.38 | 71.64 |
| Fresh agent A controller | 3.880 ± 0.092 | 8.475 ± 0.119 | 497.69 | 58.73 |
| Fresh agent B controller | 3.935 ± 0.091 | 8.605 ± 0.116 | 498.87 | 57.97 |

Free-to-play first-completion times, paid recovery/reroll spending, per-event
agent outcomes, input hashes and training metadata are in the
[machine-readable results](event-pilot-2026-10-05.json).

## How the controllers were obtained

The 2.440 free-to-play result belongs to the **trained linear policy**. Its
coefficients started randomly, then training selected them for completion count.
It does not choose actions randomly during evaluation. This pilot contains no
uniform-random-action benchmark; none of its rows estimates random play.

The existing baseline is hand-written. Two separate linear policies were trained
from random coefficients, one for each budget, using 64 generations × 64 candidates
× 64 training events (262,144 candidate-event evaluations per budget). Training
seed was 76543. Each also received a separate 1,000-event diagnostic evaluation;
those diagnostics are not mixed into the final 200-event comparison.

Two fresh AI agents received only the action protocol, rules and profile. They
authored Python controllers, rather than making an LLM call for every move. A
completed two practice events and B one paid practice before freezing. These
practices used the earlier common stone pool; after the maintainer's correction,
only handling for the newly included Rusty stone was added without further
practice. They were instructed not to inspect source, other policies, RNG or
evaluation output. The shared filesystem was not a security sandbox.

The small learner improves paid play over the baseline but loses on free play.
Its limited policy representation and finite training are visible limitations;
there is no claim that more simulations necessarily find the optimal policy.

## Damage-distribution sensitivity

Each frozen learned policy was evaluated on 1,000 events per distribution with
master seed 728192. Only the damage distribution changed; no retraining occurred.
Low/high always use the interval endpoint and are stress scenarios. They are
not proven bounds on live outcomes. Uniform and symmetric triangular sampling
share a midpoint mean but different variance.

| Damage sampling | F2P learned-policy completions | Paid learned-policy completions |
| --- | ---: | ---: |
| low | 3.149 | 8.491 |
| uniform | 2.528 | 6.929 |
| triangular | 2.534 | 6.918 |
| high | 2.072 | 5.604 |

This explores only one uncertainty. Door distributions, curse rates, flee
modifier semantics, gem restrictions and special-room behavior can matter more.
Actual play observations are still required to validate them.

## Reproduce

Build engine 0.4.0, then run from the repository root (use `build/sfld` for a
single-config build):

```sh
build/Release/sfld.exe event --allow-assumptions --budget 0 --runs 200 --seed 391864
build/Release/sfld.exe event --allow-assumptions --budget 500 --runs 200 --seed 391864 --policy examples/learned/budget-500.policy
python tools/evaluate_agent.py examples/agents/player_a.py --budget 0 --events 200 --seed 391864 --output out/reproduced-a-f2p.json
python tools/evaluate_agent.py examples/agents/player_b.py --budget 500 --events 200 --seed 391864 --output out/reproduced-b-paid.json
```

Use both budgets for each controller. The zero-budget learned file is
`examples/learned/budget-0.policy`. For sensitivity, copy the profile and change
`damage_distribution`, retaining the same master seed within the comparison.
Recorded runs used Windows x64/MSVC Release; floating-point and training
sampling behavior across compilers are not promised bit-identical.
