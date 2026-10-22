"""Reproduce the offline baseline/strategic comparison with frozen scenario copies.

Python only orchestrates the experiment; every simulated action runs in C++.
This benchmark is conditional on a synthetic profile, not live-game calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def setting(text: str, key: str, value: str) -> str:
    updated, count = re.subn(rf"^{re.escape(key)}=.*$", lambda _: key + "=" + value, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected one {key} setting")
    return updated


def empty_key_rooms(text: str, percent: float) -> str:
    row = re.search(r"^locked=(.+)$", text, re.MULTILINE)
    if row is None:
        raise ValueError("Missing locked contents")
    weights = dict((kind, float(weight)) for kind, weight in (part.split(":") for part in row[1].split(",")))
    total = sum(weight for kind, weight in weights.items() if kind != "empty")
    if total <= 0:
        raise ValueError("Need some non-empty key-room weight")
    weights = {kind: weight * (100 - percent) / total for kind, weight in weights.items() if kind != "empty"}
    weights["empty"] = percent
    return setting(text, "locked", ",".join(f"{kind}:{weight:.12g}" for kind, weight in weights.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("build/Release/sfld.exe"))
    parser.add_argument("--profile", type=Path, default=Path("profiles/synthetic.profile"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=271828183)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--sensitivity-runs", type=int, default=3000)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--strategies", nargs="+", choices=("baseline","strategic","key-stockpile"), default=["baseline","strategic"])
    parser.add_argument("--base-only", action="store_true", help="Skip sensitivity scenarios")
    parser.add_argument("--first-run-audit", action="store_true", help="Also report F2P first-run recovery and stage key use")
    parser.add_argument("--budgets", nargs="+", type=int, default=[0,100,250,500])
    args = parser.parse_args()
    if any(b < 0 or b > 1000000 for b in args.budgets): parser.error("Invalid budget")
    if args.runs < 1 or args.sensitivity_runs < 1 or not 1 <= args.threads <= 256 or not 0 <= args.seed < 2**64:
        parser.error("Invalid run count, seed or thread count")
    exe = args.exe.resolve(strict=True)
    original = args.profile.read_text(encoding="utf-8")
    version = subprocess.check_output([str(exe), "--version"], text=True).strip()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "profiles").mkdir()
    (root / "results").mkdir()
    variants = {
        "base": original,
        "empty-key-5": empty_key_rooms(original, 5),
        "empty-key-20": empty_key_rooms(original, 20),
        "barrel-35": setting(original, "barrel_blessing", "0.35"),
        "barrel-65": setting(original, "barrel_blessing", "0.65"),
        "evade-40": setting(original, "escape_chance", "0.40"),
        "evade-60": setting(original, "escape_chance", "0.60"),
        "combat-curse-5": setting(original, "combat_curse_chance", "0.05"),
        "combat-curse-20": setting(original, "combat_curse_chance", "0.20"),
    }
    if args.base_only: variants = {"base":original}
    manifest = {
        "schema_version": 1,
        "evidence": "synthetic",
        "calibrated_to_live_game": False,
        "engine": version,
        "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "master_seed": args.seed,
        "deadline_hours": 168,
        "policies": args.strategies,
        "policy_kind": "handwritten; no training on these evaluation seeds",
        "strategy_source_sha256": hashlib.sha256((Path(__file__).resolve().parents[1] / "src/strategy.cpp").read_bytes()).hexdigest(),
        "interpretation": [
            "Common event seeds are used for each policy; different decisions can consume different random draws.",
            "The base scenario compares separate budgets; sensitivity changes one probability at a time.",
            "Mushroom cost is gross spending divided by all completed runs, including those supported by natural regeneration.",
            "Found mushrooms do not increase the hard gross-spending cap.",
            "Agents act around the clock, with two simulated seconds per action in the supplied scenario.",
            "Monte Carlo standard errors do not include model uncertainty or remaining structural gaps.",
        ],
        "profiles": {},
        "cases": [],
    }
    for scenario, text in variants.items():
        if scenario != "base":
            current_id = re.search(r"^id=(.+)$", text, re.MULTILINE)
            if current_id is None:
                raise ValueError("Missing profile id")
            text = setting(text, "id", current_id[1] + "-" + scenario)
        profile = Path("profiles") / (scenario + ".profile")
        (root / profile).write_text(text, encoding="utf-8", newline="\n")
        manifest["profiles"][scenario] = {"path":profile.as_posix(), "text":text,
            "sha256":hashlib.sha256(text.encode("utf-8")).hexdigest()}
        budgets = args.budgets if scenario == "base" else (0, 500)
        runs = args.runs if scenario == "base" else args.sensitivity_runs
        for budget in budgets:
            for strategy in manifest["policies"]:
                identifier = f"{scenario}-{strategy}-{budget}"
                output = Path("results") / (identifier + ".json")
                command = ["event", "--allow-assumptions", "--profile", profile.as_posix(),
                    "--strategy", strategy, "--budget", str(budget), "--deadline-hours", "168",
                    "--runs", str(runs), "--seed", str(args.seed), "--threads", str(args.threads),
                    "--barrels", "adaptive", "--shop-policy", "adaptive", "--rerolls", "0",
                    "--output", output.as_posix()]
                completed = subprocess.run([str(exe), *command], cwd=root, text=True, capture_output=True)
                if completed.returncode:
                    raise RuntimeError(f"{identifier} failed: {completed.stderr}")
                report = json.loads((root / output).read_text(encoding="utf-8"))
                if (not report["valid"] or report["action_limit_events"] or report["events"] != runs
                        or not 0 <= report["mean_mushrooms"] <= budget
                        or not report["policy"].startswith(strategy + ":")):
                    raise RuntimeError(f"Invalid benchmark result: {identifier}")
                manifest["cases"].append({"id":identifier, "scenario":scenario, "strategy":strategy,
                    "arguments":command, "report":report})
                (root / "report.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
                print(f"{identifier}: {report['mean_completions']:.4f} runs, "
                      f"{report['mushrooms_per_completion']:.2f} mushrooms/run", flush=True)
    if args.first_run_audit:
        manifest["first_run_audits"] = []
        for strategy in args.strategies:
            output = Path("results") / f"first-run-{strategy}.json"
            command = ["simulate","--allow-assumptions","--profile","profiles/base.profile",
                "--strategy",strategy,"--budget","0","--deadline-hours","168","--runs",str(args.runs),
                "--seed",str(args.seed),"--threads",str(args.threads),"--output",output.as_posix()]
            completed = subprocess.run([str(exe),*command],cwd=root,text=True,capture_output=True)
            if completed.returncode: raise RuntimeError(completed.stderr)
            report = json.loads((root/output).read_text(encoding="utf-8"))
            if not report["valid_deadline_experiment"]: raise RuntimeError("Invalid first-run audit")
            manifest["first_run_audits"].append({"strategy":strategy,"arguments":command,"report":report})
        manifest["interpretation"].append("First-run audits reuse the event seeds; they are not extra independent event samples.")
        (root/"report.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(f"Saved {len(manifest['cases'])} comparisons to {root / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
