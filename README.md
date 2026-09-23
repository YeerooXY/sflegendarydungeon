# S&F Legendary Dungeon simulator

An independent C++20 research simulator for Shakes & Fidget's Legendary Dungeon.
Compare gem preferences, barrel decisions, shop rerolls, recovery thresholds and
mushroom budgets using reproducible Monte Carlo experiments.

**Version 0.2 is an experimental model, not a validated reconstruction of the live
game.** Public documentation describes many rules, but does not supply the joint
door distribution, complete outcome probabilities or raw validation runs. The
included scenario fills those gaps with explicit assumptions. Its results must
not be presented as measured game averages or an optimal stone ranking.

The project is MIT licensed, has no runtime dependencies and makes no game-server
requests. It contains independently written code and no game artwork or assets.
Shakes & Fidget belongs to its respective owners; this project is unaffiliated.

## What works

- A state machine for 100 rooms, bosses, keys, traps, effects, gem offers, shops,
  containers, special encounters, trials, death and recovery.
- Separate damage models for fighting, failed escape and bosses; ordered effect
  slots with room, fight, trap and door counters.
- Hard mushroom budgets, paid recovery, shop rerolls and a simple login schedule.
- Multithreaded simulations with seeded randomness and identical reports across
  thread counts on the same build.
- JSON summaries, completion-probability intervals, deadline-aware time metrics
  and deterministic action traces with replay verification.
- Five policy sweeps: barrels, gems, budgets, revival thresholds and reroll limits.
- Forecasts from entered progress, including observed doors, walls, shop/gem
  offers, recovery, active effects and previous healing purchases.

Many numerical rules and some special-room transitions remain approximations.
Read [the model and limitations](docs/MODEL.md) before interpreting output.

## Build

Use CMake 3.20+ and a C++20 compiler with `std::jthread` support. GCC, Clang and
MSVC are supported; there are no packages to download during configuration.

```sh
git clone https://github.com/YeerooXY/sflegendarydungeon.git
cd sflegendarydungeon
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

On Linux/macOS the executable is `build/sfld`. Visual Studio generators place it
at `build/Release/sfld.exe`. Run examples from the repository root or provide an
explicit `--profile` path. Windows users with Visual Studio but no CMake on PATH
can run `./tools/build.ps1`, which locates the bundled CMake and runs the tests.

## Continue from your current position

```sh
build/sfld state-template --output my-run.state
# Edit the example to match your current room, HP, keys, stones and effects.
build/sfld audit --state my-run.state
build/sfld simulate --state my-run.state --allow-assumptions --runs 100000 --deadline-hours 48 --budget 0 --output results/from-here.json
```

With `--state`, the deadline and mushroom budget apply **from that position
onward**. Previous healing purchases affect the next price. Known choices stay
fixed; unknown future rooms are sampled. The [progress guide](docs/PROGRESS.md)
includes examples for recovery, shops and the three stones currently offered.
First/later-run stone pools are configurable, but their actual split is not yet
verified in the shipped scenario.

## Free-to-play completion time

Zero mushrooms is an enforced budget, including recovery and shop rerolls.

```sh
build/sfld audit
build/sfld compare --allow-assumptions --sweep barrels --budget 0 --runs 100000 --threads 8 --seed 42 --output results/f2p-barrels.json
build/sfld compare --allow-assumptions --sweep gems --budget 0 --runs 100000 --output results/f2p-gems.json
build/sfld compare --allow-assumptions --sweep revive --budget 0 --login-hours 8 --runs 100000 --output results/f2p-recovery.json
```

`--allow-assumptions` acknowledges the synthetic scenario. Each report records
that status and its profile fingerprint. It does not certify the model.

The default horizon is 240 hours, a chosen experiment setting rather than the
duration of a particular event. Set `--deadline-hours` to your intended horizon.
Unfinished runs contribute the entire horizon to the restricted mean; they are
also counted in the completion probability. Mean, median and p90 among completed
runs are explicitly named `completed_only_*`. Those values alone can make a
failure-prone strategy look deceptively fast.

A gem sweep compares **preferences when a gem is actually offered**, against a
baseline priority list. It does not grant a stone at the start, force offers, or
estimate the isolated causal value of a stone in every decision state.

## Mushroom efficiency

Use a common deadline and compare completion probability, elapsed time and spend.
Minimizing mushrooms without any time constraint simply favors waiting.

```sh
build/sfld compare --allow-assumptions --sweep budgets --budgets 0,10,25,50,100 --deadline-hours 72 --runs 100000 --output results/mushroom-budgets.json
build/sfld compare --allow-assumptions --sweep rerolls --budget 50 --deadline-hours 72 --runs 100000 --output results/mushroom-rerolls.json
```

Recovery and reroll spending are reported separately. The
`total_mushrooms_divided_by_completions` metric includes spending on unsuccessful
attempts. It is a cohort ratio, not the expected expense of an unlimited retry
strategy, and it excludes Ultimate entry fees. The shipped recovery policy is a
baseline heuristic; these sweeps are not a proof of global optimality.

## Inspect a run

```sh
build/sfld simulate --allow-assumptions --runs 1 --seed 42 --trace results/run.tsv --output results/run.json
build/sfld replay --allow-assumptions --trace results/run.tsv
build/sfld --help
```

Replay verifies the simulator's own action sequence and state digests. It does
not yet import or validate recordings from the live game. Trace compatibility is
limited to the same engine version, profile and floating-point environment.

## Research and contributions

[Public-source research](docs/RESEARCH.md) explains what already exists and what
was not found. The most useful next contributions are anonymized observations of
door offers, barrel outcomes, damage, effect timing and actual full-refill
purchases. [Data collection](docs/DATA.md) explains the necessary fields and
sampling limitations. [Contributing](CONTRIBUTING.md) describes the code layout
and validation requirements.

An [initial local benchmark](docs/BENCHMARK.md) completed one million synthetic
runs in about 2.8 seconds with eight workers on a Ryzen 9 9950X.

The [evidence inventory](docs/mechanics-evidence.json) deliberately leaves unknown
live probabilities as `null`. The runnable [synthetic profile](profiles/synthetic.profile)
is a separate hypothetical scenario. Changing either requires preserving that
distinction.
