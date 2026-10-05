"""Evaluate a frozen Python choose(observation) controller against the C++ engine.

Python is optional tooling; simulation and policy training remain native C++.
This harness is an honor-system research boundary, not a security sandbox.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess


def mix(value):
    mask = (1 << 64) - 1
    value = (value + 0x9E3779B97F4A7C15) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return value ^ (value >> 31)


def play(args, event_index):
    # A new module instance per event prevents cross-event mutable policy state.
    spec = importlib.util.spec_from_file_location("trial_controller", args.controller)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    command = [str(args.exe.resolve()), "agent", "--allow-assumptions", "--profile", str(args.profile.resolve()),
               "--budget", str(args.budget), "--deadline-hours", str(args.hours),
               "--restart-health", args.restart_health, "--seed", str(mix(args.seed ^ event_index))]
    if args.state:
        command += ["--state", str(args.state.resolve())]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1) as process:
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"Engine stopped before terminal state in event {event_index}")
                observation = json.loads(line)
                if "error" in observation:
                    raise RuntimeError(f"Controller error in event {event_index}: {observation['error']}")
                if observation["terminal"]:
                    process.stdin.close()
                    if process.wait(timeout=10) != 0 or observation["action_limit"]:
                        raise RuntimeError(f"Invalid capped event {event_index}")
                    return observation
                action = controller.choose(observation)
                if action not in observation["legal_actions"]:
                    raise RuntimeError(f"Controller selected an action outside the menu in event {event_index}")
                process.stdin.write(f"{action['kind']} {action['index']} {action['value']:.17g}\n")
                process.stdin.flush()
        except BaseException:
            process.kill()
            process.wait()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("controller", type=Path)
    parser.add_argument("--exe", type=Path, default=Path("build/Release/sfld.exe"))
    parser.add_argument("--profile", type=Path, default=Path("profiles/synthetic.profile"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--hours", type=float, default=168)
    parser.add_argument("--restart-health", choices=["full", "carry", "empty"], default="full")
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--seed", type=int, default=246810)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.events < 1 or args.budget < 0 or not math.isfinite(args.hours) or args.hours <= 0 or not 0 <= args.seed < 2**64:
        parser.error("Invalid experiment limits")
    protected = [args.controller, args.exe, args.profile] + ([args.state] if args.state else [])
    if any(args.output.resolve() == p.resolve() or (args.output.exists() and args.output.samefile(p)) for p in protected):
        parser.error("Output must not overwrite an input file")
    controller_hash = hashlib.sha256(args.controller.read_bytes()).hexdigest()
    outcomes = [play(args, i) for i in range(args.events)]
    if hashlib.sha256(args.controller.read_bytes()).hexdigest() != controller_hash:
        raise RuntimeError("Controller changed during evaluation")
    counts = [o["completed_runs"] for o in outcomes]
    total = sum(counts)
    spent = sum(o["mushrooms_spent"] for o in outcomes)
    first = [o["first_completion_hours"] for o in outcomes if o["first_completion_hours"] is not None]
    report = dict(schema=1, engine=outcomes[0]["engine"], evidence="synthetic", valid=True,
                  controller=str(args.controller), controller_sha256=controller_hash,
                  profile_sha256=hashlib.sha256(args.profile.read_bytes()).hexdigest(),
                  events=args.events, master_seed=args.seed, budget_per_event=args.budget,
                  deadline_hours=args.hours, restart_health=args.restart_health,
                  mean_completions=statistics.mean(counts),
                  mean_completions_mc_se=statistics.stdev(counts)/math.sqrt(args.events) if args.events > 1 else None,
                  mean_mushrooms=spent/args.events,
                  mushrooms_per_completion=spent/total if total else None,
                  mean_recovery_mushrooms=statistics.mean(o["recovery_mushrooms"] for o in outcomes),
                  mean_reroll_mushrooms=statistics.mean(o["reroll_mushrooms"] for o in outcomes),
                  probability_at_least_one_completion=len(first)/args.events,
                  conditional_first_completion_hours=statistics.mean(first) if first else None,
                  restricted_first_completion_hours=sum(o["first_completion_hours"] if o["first_completion_hours"] is not None else args.hours for o in outcomes)/args.events,
                  uncertainty="Monte Carlo error only; model uncertainty is not included",
                  outcomes=[{k: o[k] for k in ("completed_runs", "mushrooms_spent", "elapsed_hours", "room", "actions")} for o in outcomes])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "outcomes"}))


if __name__ == "__main__":
    main()
