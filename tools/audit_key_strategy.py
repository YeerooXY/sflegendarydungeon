"""Audit first-run recovery and compare early key stockpiling on fresh seeds.

Only C++ simulates actions. Shop variants are sensitivity cases, not corrected
live rules. Common seeds intentionally couple first-run and event comparisons.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from benchmark_strategies import setting


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("build/Release/sfld.exe"))
    parser.add_argument("--profile", type=Path, default=Path("profiles/synthetic.profile"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=314159267)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--sensitivity-runs", type=int, default=3000)
    parser.add_argument("--threads", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.runs <= 10000000 or not 1 <= args.sensitivity_runs <= 10000000 or not 1 <= args.threads <= 256 or not 0 <= args.seed < 2**64:
        parser.error("Invalid run count, thread count or seed")
    exe = args.exe.resolve(strict=True)
    original = args.profile.read_text(encoding="utf-8")
    if "shop_schedule=one_per_stage" in original:
        parser.error("This historical shop-weight audit needs a weighted-shop profile (e.g. archived v6); use benchmark_strategies.py for the current model")
    half_shops = original.replace("shop:5,", "shop:2.5,")
    if half_shops == original:
        raise ValueError("This experiment expects the base door shop weight to be 5")
    scenarios = {
        "base": original,
        "only-opening-free": setting(original, "free_first_level_shops", "false"),
        "half-shop-weight": half_shops,
    }
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "profiles").mkdir()
    (root / "results").mkdir()
    repo = Path(__file__).resolve().parents[1]
    report = {
        "schema_version": 1, "evidence": "synthetic", "calibrated_to_live_game": False,
        "engine": subprocess.check_output([str(exe), "--version"], text=True).strip(),
        "evaluation_seed": args.seed, "historical_audit_seed": 271828183,
        "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "source_sha256": {str(p): hashlib.sha256((repo / p).read_bytes()).hexdigest()
                          for p in ("src/strategy.cpp", "src/batch.cpp", "tools/audit_key_strategy.py")},
        "interpretation": [
            "All strategies are handwritten; key-stockpile was not tuned on these results.",
            "First-run samples share seeds with event samples and are not additional independent evidence for event counts.",
            "Stage activity is conditional on reaching that stage's exit; inspect its sample count.",
            "Non-boss fight/flee counts are attempts, including deaths; boss fights are excluded from these columns.",
            "First-run elapsed time includes recovery; active decisions take two seconds each in these scenarios.",
            "Shop sensitivities do not enforce a once-per-stage schedule.",
            "Model uncertainty and missing opening/shop/loot rules are not sampling error.",
        ],
        "profiles": {}, "cases": [],
    }

    def run_case(scenario: str, strategy: str, command: str, budget: int, runs: int, seed: int, suffix: str = "") -> None:
        identifier = f"{scenario}-{strategy}-{command}-{budget}{suffix}"
        output = Path("results") / (identifier + ".json")
        arguments = [command, "--allow-assumptions", "--profile", f"profiles/{scenario}.profile",
                     "--strategy", strategy, "--budget", str(budget), "--deadline-hours", "168",
                     "--runs", str(runs), "--seed", str(seed), "--threads", str(args.threads),
                     "--barrels", "adaptive", "--shop-policy", "adaptive", "--rerolls", "0",
                     "--output", output.as_posix()]
        completed = subprocess.run([str(exe), *arguments], cwd=root, text=True, capture_output=True)
        if completed.returncode:
            raise RuntimeError(f"{identifier}: {completed.stderr}")
        result = json.loads((root / output).read_text(encoding="utf-8"))
        if command == "event":
            assert result["valid"] and result["action_limit_events"] == 0 and result["events"] == runs
            assert 0 <= result["mean_mushrooms"] <= budget and result["policy"].startswith(strategy + ":")
            summary = f"{result['mean_completions']:.4f} completions, {result['mushrooms_per_completion']:.2f} mushrooms/completion"
        else:
            assert result["valid_deadline_experiment"] and result["action_limit_exceeded"] == 0 and result["runs"] == runs
            assert 0 <= result["mean_mushrooms_per_started_run"] <= budget and result["strategy"] == strategy
            assert sum(result["completed_run_death_histogram"].values()) == result["completed"]
            summary = f"{result['restricted_mean_hours_at_deadline']:.4f} hours, {result['mean_deaths']:.4f} deaths"
        report["cases"].append({"id": identifier, "scenario": scenario, "strategy": strategy,
                                "command": command, "arguments": arguments, "report": result})
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{identifier}: {summary}", flush=True)

    for scenario, text in scenarios.items():
        if scenario != "base":
            current_id = next(line[3:] for line in text.splitlines() if line.startswith("id="))
            text = setting(text, "id", current_id + "-" + scenario)
        path = Path("profiles") / (scenario + ".profile")
        (root / path).write_text(text, encoding="utf-8", newline="\n")
        report["profiles"][scenario] = {"path": path.as_posix(), "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        if scenario == "base":
            run_case(scenario, "strategic", "simulate", 0, args.runs, 271828183, "-historical-seed")
        runs = args.runs if scenario == "base" else args.sensitivity_runs
        for strategy in ("baseline", "strategic", "key-stockpile"):
            run_case(scenario, strategy, "simulate", 0, runs, args.seed)
            for budget in (0, 500):
                run_case(scenario, strategy, "event", budget, runs, args.seed)
    print(f"Saved {len(report['cases'])} cases to {root / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
