# Event experiments and independent agents

Version 0.4 adds repeated-run events. Defaults are seven days (168 simulated
hours) and 500 mushrooms. The objective is completed runs, with one budget and
deadline shared across the event. Partial progress does not count as a completion.
Run a separate budget-zero experiment for free-to-play results.

The engine is C++20. No game client or server is required. Python is optional
for external-controller evaluation; training and bulk simulation stay in C++.

## Commands

Use `build/Release/sfld.exe` on Windows or `build/sfld` with a single-config build.

```sh
build/sfld event --allow-assumptions --budget 0 --runs 10000 --output out/f2p.json
build/sfld event --allow-assumptions --budget 500 --runs 10000 --output out/paid.json
build/sfld event --allow-assumptions --budget 500 --rerolls 5 --shop-policy one-hit-8
build/sfld event --allow-assumptions --budget 500 --state examples/shop.state
```

All event counters start from the supplied progress. `run_number=2` means a
previous run was already completed, not that the new experiment receives a free
completion. Time/budget cover only future actions. Existing paid-heal history is
preserved until the current run ends.

`restart` resets dungeon keys, stones, effects, trials and the per-run refill
counter. External resource units carry across runs. Event time, spending and
reward counters persist. Full HP on restart is a **maintainer observation**
(2026-10-05), adjustable with `--restart-health full|carry|empty`. Entry has no
mushroom fee for normal LD; Ultimate fees/rewards are not represented.

## Train a policy

```sh
build/sfld train --allow-assumptions --budget 0 --seed 76543 --generations 64 --population 64 --training-events 64 --runs 1000 --save-policy out/f2p.policy --output out/f2p-learning.json
build/sfld train --allow-assumptions --budget 500 --seed 76543 --generations 64 --population 64 --training-events 64 --runs 1000 --save-policy out/paid.policy --output out/paid-learning.json
build/sfld event --allow-assumptions --budget 500 --policy out/paid.policy --seed 246810 --runs 1000
```

This is evolutionary policy search: sample coefficients for visible-state action
scores, evaluate candidates, retain the top quarter and update the sampling
distribution. Coefficients start from a zero-mean random distribution, without
the hand-written baseline's strategy or stone ranking. Completion count is the
primary selection criterion. Remaining room progress and then lower spending
break exact ties on the training sample.

Each generation uses new training seeds, shared across candidates. `--runs`
specifies evaluation events on a separate derived seed stream after selection.
Training and evaluation counts are reported separately. Do not repeatedly tune
against those evaluation results and continue calling them an untouched test.
Use a fresh evaluation seed for a final frozen comparison.

The learner is intentionally small: a linear scoring policy over action types,
HP, keys, room, budget/time left, several effects, doors, encounters, offers and
stones. It cannot express every strategy or reason over complete history. It
is not proof of optimality and may lose to a hand-written or AI-authored policy.
Population sampling uses the C++ standard library's normal distribution;
training reproducibility is promised only on the same build/toolchain. Saved
coefficient files require an exact compatible engine version.

## Fresh AI participants

Give participants [AGENT_RULES.md](AGENT_RULES.md), the chosen profile, and the
objective. Do not provide existing policy code, training results or hidden
random streams. Launch `sfld agent --allow-assumptions --budget 500`.

The process emits one JSON observation per accepted action; respond with
`ACTION INDEX VALUE` and flush stdin. The `legal_actions` menu contains exact
command values. Observations expose current HP, resources, keys, stones, effects,
prices, time/budget remaining and the current revealed choice. Hidden door
contents, future RNG and the seed are absent. An invalid action emits an error
then an unchanged observation. The terminal state reports event totals.

Agents can reason at each decision or author a controller against the same
interface. Label those experiments separately: an AI-authored Python controller
is not an LLM inference for every move. Thinking time is excluded; the model's
action duration advances the clock, including the ten-second flooded-room timer.

For a frozen Python controller exposing `choose(observation)`, use:

```sh
python tools/evaluate_agent.py controller.py --exe build/Release/sfld.exe --budget 0 --events 100 --seed 246810 --output out/agent-f2p.json
python tools/evaluate_agent.py controller.py --exe build/Release/sfld.exe --budget 500 --events 100 --seed 246810 --output out/agent-paid.json
```

The evaluator starts a fresh controller module for every event, uses the same
seed derivation as C++ batches, verifies actions against the menu, checks terminal
status and records the controller's SHA-256. It does not isolate malicious
controller code from the filesystem. Independent participants follow an explicit
no-inspection instruction; a stronger tournament would need process isolation.
Do not run untrusted Python code just because it implements this interface.

`agent --trace out/session.jsonl` writes a manifest and observation/action trail.
This is an audit log, not the single-run TSV format consumed by `sfld replay`.
Preserve profile, initial-state file, engine version and seed to reproduce it.

## Interpreting comparisons

- Free-to-play: completions per event, chance of at least one completion,
  conditional first-completion time and its restricted mean. Unfinished first
  runs contribute the deadline to the restricted mean.
- Paid: completions per event, average mushrooms spent and aggregate mushrooms
  per completion; healing and reroll spending are separate. Zero completions
  produce `null`, never a divide-by-zero result.
- Monte Carlo standard errors measure simulated sampling variability only.
  They exclude uncertainty about live mechanics. Action-limit events invalidate
  the report (`valid=false`, exit code 2); partial counts are diagnostic.

## Unknown rules and sensitivity

Base escape and barrel blessing are 50% working assumptions. Weak/strong shop
offers also use 50/50, independently of barrel strengths. These are not estimates
from a measured outcome dataset. Escape blessing/curse percentage semantics
remain provisional even if base escape is assumed to be 50%.

Eligible stone offers have equal weight without replacement and exclude held
stones. `gems_first_run` and `gems_later_runs` select separate pools when specified.
The engine automatically changes pools after a completion. The shipped profile
uses the [maintainer's reported split](GEM_POOLS.md), with 11 first-run and 20
later-run stones. Rusty's restriction is tentative; other restrictions could
exist. This is a player report, not a measured server guarantee. Explicitly observed
offers in a progress file override future-pool assumptions.

To measure damage sensitivity, copy the profile and change
`damage_distribution=uniform` to `triangular`, `midpoint`, `low` or `high`.
Range endpoints alone do not determine the mean or distribution. Low/high are
endpoint stress scenarios, not guaranteed bounds on whole-strategy completion
counts: policy decisions and RNG paths can change.

Also vary restart HP, door/room weights, curse rates, spider chances and refill
pricing. All official golden-room guide variants have transitions, but several
client-only entries and event-specific spawn pools remain unresolved. Large
simulations and clever agents cannot turn these assumptions into observed data.
