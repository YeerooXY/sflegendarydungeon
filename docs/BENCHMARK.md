# Initial performance check

Measured 2026-09-23 on an AMD Ryzen 9 9950X (16 physical cores), Windows x64,
MSVC 19.51.36257, CMake Release build, eight simulation workers. This is one local
measurement, not a portable performance guarantee or a live-game timing estimate.

```powershell
./build/Release/sfld.exe simulate --allow-assumptions --runs 1000000 --threads 8 --seed 42 --output results/benchmark-million.json
```

The default synthetic profile and baseline adaptive-barrel policy executed
**1,000,000 runs, 226,914,138 actions, in 2.796 seconds** as measured by the CLI.
That is approximately 81.2 million simulated actions per second. The timer includes
batch execution, aggregation and report writing; it excludes process startup and
profile loading. Worker count, compiler, machine load and scenario affect this
number. No tracing was enabled.

Separate smoke experiments exercised all five sweeps: 100,000 barrel-policy runs,
200,000 gem-preference runs, 50,000 budget runs, 50,000 recovery-threshold runs and
40,000 reroll runs. These runs test throughput and transition coverage. Their
strategy rankings are deliberately not published as game advice because the
scenario's probabilities are hypothetical.
