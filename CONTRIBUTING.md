# Contributing

The purpose is to make repeatable claims about strategies with an auditable model.
Code correctness and agreement with the game are separate requirements.

## Layout

| Path | Responsibility |
| --- | --- |
| `include/sfld/model.hpp`, `src/model.cpp` | Visible state, enums, effects, RNG and digests |
| `src/profile.cpp`, `profiles/` | Strict scenario loading and validation |
| `src/progress.cpp`, `examples/` | Observed starting positions, validation and canonical snapshots |
| `src/engine.cpp` | Legal actions and state transitions |
| `src/policy.cpp` | Baseline decision policy |
| `src/batch.cpp` | Reproducible batches, statistics and trace replay |
| `src/main.cpp` | CLI and experiment sweeps |
| `tests/tests.cpp` | Mechanics, invariants and reproducibility checks |
| `docs/` | Evidence, assumptions and data collection |

Keep policy code separate from transitions. Policies may inspect visible state
and scenario parameters, but not the RNG or unrevealed outcomes. Keep profile
parsing and file I/O outside the per-action loop. A new policy should be evaluated
under more than one plausible profile before drawing a general conclusion.

## Mechanics changes

1. Identify the rule, source URL or anonymized observation, game version and event.
2. Mark evidence as official documentation, community estimate, observation,
   disputed, or assumption. Do not turn missing data into an unexplained constant.
3. Add a deterministic regression test when the change affects a state transition.
4. Update `docs/MODEL.md` and the evidence inventory where applicable.
5. Run CMake/CTest and a relevant CLI sweep. A benchmark is useful for hot-loop
   changes, but is not evidence of model accuracy.

The current loader accepts only `evidence=synthetic`. Lifting this restriction
requires a calibration process and held-out validation data, not a renamed field.
Statistical intervals in reports currently describe simulation sampling error.

## Validation

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

For GCC/Clang diagnostics:

```sh
cmake -S . -B build-sanitize -DCMAKE_BUILD_TYPE=Debug -DSFLD_SANITIZERS=ON
cmake --build build-sanitize --parallel
ctest --test-dir build-sanitize --output-on-failure
```

GitHub Actions builds on Linux and Windows, including a sanitizer job. A failing
batch prints its zero-based run index and derived seed. The library's `run_one`
takes that derived seed directly; the CLI's `--seed` takes a master seed instead.

Please submit your own code and data. Link to external research with attribution;
do not assume another project's code or datasets share this repository's license.
Do not submit credentials, session tokens, account identifiers or unredacted game
traffic. Contributions to this project are provided under the MIT license.
