"""Focused CLI regressions; pass --exe PATH to a freshly built sfld_rule_search.

Uses small deterministic offline events, and a private temporary output directory.
No benchmark or existing research output is overwritten.
"""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GEMS = "rabbit moonstone spying pendant greasy gambler greed hero time_traveler thirsty pearl blood explorer misadventurer devil deceit lodestone bull hick rusty".split()
CONTEXTS = "escape_pair_bonus greasy_traveler_bonus gambler_traveler_adjustment greasy_with_perfect_escape_adjustment greasy_second_pick_adjustment greasy_third_pick_adjustment spying_pendant_adjustment gambler_moonstone_adjustment pearl_with_perfect_escape_adjustment".split()


def points(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    args = parser.parse_args()
    exe = args.exe.resolve()
    assert exe.is_file(), exe
    (ROOT / "out").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rule-constraints-test-", dir=ROOT / "out") as tmp:
        work = Path(tmp).resolve()
        assert work.parent == (ROOT / "out").resolve()

        def run(name, options, error=None):
            output = work / (name + ".json")
            command = [str(exe), "--profile", str(ROOT / "profiles/synthetic.profile"),
                       "--threads", "1", "--runs", "12", "--training-events", "8",
                       "--validation-events", "8", "--seed", "884421",
                       "--output", str(output), *map(str, options)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
            if error is not None:
                assert result.returncode != 0, (name, result.stdout, result.stderr)
                assert error in result.stderr, (name, result.stderr)
                assert not output.exists(), name
                return None
            assert result.returncode == 0, (name, result.stdout, result.stderr)
            return json.loads(output.read_text(encoding="utf-8"))

        winner = work / "winner.rules"
        search = run("search", ["--mode", "search", "--anchor-gem", "rabbit",
            "--avoid-gems", "bull,hick", "--fixed-revive-hp", ".2", "--selection", "mean",
            "--unique-candidates", "6", "--generations", "1", "--population", "2",
            "--finalists", "3", "--save-rules", winner])
        assert len(search["candidates"]) == 6
        assert len(search["generations"]) == 3  # The exact target overrides one generation.
        settings = search["settings"]
        assert settings["selection_policy"] == "mean"
        assert settings["finalist_budget"] == "includes_initial_rule"
        assert len(search["validation_finalists"]) == 3
        assert any(x["id"] == "candidate-0" for x in search["validation_finalists"])
        assert settings["unique_candidates"] == 6 and settings["fixed_revive_hp"] == .2
        assert settings["gem_constraints"]["anchor_gem"] == "rabbit"
        assert settings["gem_constraints"]["avoid_gems"] == ["bull", "hick"]
        assert settings["gem_constraints"]["offer_pools_unchanged"] is True
        assert settings["gem_constraints"]["controls_constrained"] is False
        assert set(settings["eligible_gem_score_mutations"]) == set(GEMS) - {"rabbit", "bull", "hick"}
        total_avoided_offers = 0
        for candidate in search["candidates"]:
            rule = points(candidate["rules_text"])
            assert float(rule["gem.rabbit"]) == 1_000_000
            assert float(rule["gem.bull"]) == float(rule["gem.hick"]) == -1_000_000
            assert float(rule["revive_hp"]) == .2
            for budget in candidate["training"]["budgets"]:
                offers, picks = budget["gem_offers"], budget["gem_picks"]
                assert offers["rabbit"] == picks["rabbit"] > 0
                assert picks["bull"] == picks["hick"] == 0
                assert sum(offers.values()) == 3 * budget["gem_decisions"]
                total_avoided_offers += offers["bull"] + offers["hick"]
        assert total_avoided_offers > 0  # Avoided stones remain in generated offers.
        best = min(search["validation_finalists"], key=lambda x: (
            -x["evaluation"]["normalized_fitness"]["mean"], int(x["id"].split("-")[1])))
        assert search["winner_id"] == search["validation_highest_mean_id"] == best["id"]
        assert winner.read_text(encoding="utf-8") == search["winner_rules_text"]

        plain_csv, explicit_csv = work / "plain.csv", work / "explicit.csv"
        base = ["--mode", "evaluate", "--rules", winner, "--budgets", "0,20"]
        reloaded = run("reloaded", [*base, "--event-completions", plain_csv])
        explicit = run("explicit", [*base, "--anchor-gem", "rabbit", "--avoid-gems", "bull,hick",
            "--fixed-revive-hp", ".2", "--event-completions", explicit_csv])
        assert reloaded["evaluation"] == explicit["evaluation"]
        assert reloaded["controls"] == explicit["controls"]
        assert plain_csv.read_bytes() == explicit_csv.read_bytes()
        controls = run("controls", ["--mode", "evaluate", "--budgets", "0,20"])
        assert explicit["controls"] == controls["controls"]
        with plain_csv.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == 24
        assert set(rows[0]) == {"budget", "event_index", "completions"}
        for budget in reloaded["evaluation"]["budgets"]:
            chosen = [row for row in rows if int(row["budget"]) == budget["budget"]]
            assert [int(row["event_index"]) for row in chosen] == list(range(12))
            assert abs(sum(int(row["completions"]) for row in chosen) / 12 - budget["completions"]["mean"]) < 1e-10

        # Extreme but valid loaded contexts must not defeat fixed score priority.
        extreme = points(winner.read_text(encoding="utf-8"))
        for gem in GEMS:
            extreme["gem." + gem] = "1000000"
        for key in CONTEXTS:
            extreme["gem." + key] = "-1000000"
        extreme_path = work / "extreme.rules"
        extreme_path.write_text("".join(f"{key}={value}\n" for key, value in extreme.items()), encoding="utf-8")
        extreme_result = run("extreme", ["--mode", "evaluate", "--rules", extreme_path,
            "--anchor-gem", "pearl", "--avoid-gems", "rabbit,moonstone"])
        bounded = points(extreme_result["rules_text"])
        context_bound = sum(abs(float(bounded["gem." + key])) for key in CONTEXTS)
        base_bound = max(abs(float(bounded["gem." + gem])) for gem in GEMS if gem not in {"pearl", "rabbit", "moonstone"})
        assert base_bound + context_bound <= 100000 + 1e-8
        budget = extreme_result["evaluation"]["budgets"][0]
        assert budget["gem_offers"]["pearl"] == budget["gem_picks"]["pearl"] > 0
        assert budget["gem_picks"]["rabbit"] == budget["gem_picks"]["moonstone"] == 0

        invalid = [
            (["--anchor-gem", "imaginary"], "Unknown"),
            (["--anchor-gem", "kidney"], "absent from both profile pools"),
            (["--avoid-gems", "kidney"], "absent from both profile pools"),
            (["--avoid-gems", "bull,bull"], "Duplicate --avoid-gems"),
            (["--avoid-gems", "bull,"], "Empty --avoid-gems"),
            (["--avoid-gems", "bull,,hick"], "Unknown"),
            (["--avoid-gems", ""], "Empty --avoid-gems"),
            (["--anchor-gem", "bull", "--avoid-gems", "bull,hick"], "cannot also be avoided"),
            (["--fixed-revive-hp", ".19"], "fraction in [0.2,1]"),
            (["--fixed-revive-hp", "1.01"], "fraction in [0.2,1]"),
            (["--fixed-revive-hp", ".2", "--controller", "legacy"], "requires the rules controller"),
            (["--unique-candidates", "4"], "requires search mode"),
            (["--mode", "search", "--unique-candidates", "1"], "count in 2..1000000"),
            (["--selection", "unrecognized"], "Invalid --selection"),
            (["--mode", "search", "--save-rules", work / "bad-csv.rules", "--event-completions", work / "bad.csv"], "requires evaluate mode"),
            (["--event-completions", plain_csv], "Event CSV already exists"),
        ]
        for i, (options, error) in enumerate(invalid):
            run(f"invalid-{i}", options, error)
        run("same-output", ["--event-completions", work / "same-output.json"], "paths must differ")
        # No mutable score/context/action remains; fail instead of silently
        # evaluating fewer candidates than the requested balanced allocation.
        run("impossible", ["--mode", "search", "--scope", "gems", "--gem-search", "ranks",
            "--avoid-gems", ",".join(GEMS), "--unique-candidates", "2",
            "--save-rules", work / "impossible.rules"], "10000 consecutive duplicate mutations")
    print("Rule-search constraint, selection, persistence and CSV checks passed.")


if __name__ == "__main__":
    main()
