#include "sfld/batch.hpp"
#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <type_traits>

using namespace sfld;
namespace {
void help() {
    std::cout << R"(sfld 0.3.0 - offline Legendary Dungeon simulation research

Commands: audit, simulate, compare, replay, state-template
  --profile PATH          Explicit scenario (default profiles/synthetic.profile)
  --state PATH            Forecast from existing progress; see docs/PROGRESS.md
  --allow-assumptions     Required for synthetic simulations and replays
  --runs N                Independent runs per policy (default 10000)
  --threads N             Worker threads, 1..256 (default hardware concurrency)
  --seed N                Reproducible master seed (default 42)
  --budget N              Additional mushroom budget from the start state (default 0)
  --deadline-hours H      Hours available from the start state (default 240)
  --run-number N          1 enables first-run rules, 2+ later-run rules
  --barrels POLICY        skip, always, low-hp, adaptive
  --barrel-hp FRACTION    Threshold for low-hp/adaptive (default 0.35)
  --revive-hp FRACTION    Recovery target, 0.2..1; bosses use a higher threshold
  --login-hours H         Return on H-hour grid during free recovery (default 0)
  --rerolls N             Maximum rerolls at each shop (default 0)
  --shop-policy POLICY    adaptive, one-hit, one-hit-8 (default adaptive)
  --recovery-budget N     Cap paid recovery within --budget; 0 spends only on rerolls
  --prefer-gem ID         Prioritize this gem when actually offered
  --sweep KIND           compare: barrels, gems, budgets, revive, rerolls, shops
  --budgets N,N,...       Budget sweep (default 0,10,25,50,100)
  --max-actions N         Abort a stuck run; invalidates deadline statistics
  --output PATH          JSON report (otherwise stdout)
  --trace PATH           simulate --runs 1: write deterministic TSV trace
                          replay: read and verify that trace (starting state embedded)

state-template --output my-run.state writes a commented example for editing.

All shipped scenarios are synthetic. No output is a validated live-game average.
The engine does not connect to game servers or spend real mushrooms.
)";
}
template<class T> T numeric(const std::string& text) {
    T value{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) throw std::invalid_argument("Invalid number: " + text);
    if constexpr (std::is_floating_point_v<T>) if (!std::isfinite(value)) throw std::invalid_argument("Non-finite number");
    return value;
}
std::ofstream output_file(const std::string& path) {
    const auto parent = std::filesystem::path(path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent);
    std::ofstream out(path);
    if (!out) throw std::runtime_error("Cannot write: " + path);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    return out;
}
bool same_file(const std::string& left, const std::string& right) {
    if (std::filesystem::exists(left) && std::filesystem::exists(right))
        return std::filesystem::equivalent(left, right);
    auto a = std::filesystem::weakly_canonical(left).generic_string();
    auto b = std::filesystem::weakly_canonical(right).generic_string();
#ifdef _WIN32
    // Existing aliases/hardlinks are handled above. Also reject ordinary ASCII
    // case variants of new trace/report paths on case-insensitive Windows volumes.
    const auto lower = [](unsigned char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c); };
    std::transform(a.begin(), a.end(), a.begin(), lower);
    std::transform(b.begin(), b.end(), b.begin(), lower);
#endif
    return a == b;
}
std::string state_template() {
    return R"(# EXAMPLE ONLY: replace these values with your current position.
# room is the current room to resolve; HP uses percent (0..100).
# See docs/PROGRESS.md for recovery, shop and gem-selection examples.
schema=1
room=42
run_number=2
phase=doors
hp_percent=63
keys=4
gems=rabbit
# Effects are ordered oldest first: effect:weak|strong:remaining
blessings=recovery:weak:2
curses=none
# Both positions, left then right. A blocked position is wall.
# Optional trap suffix: monster:trap or golden:cursed_trap.
# Use unknown only if the current pair has not been observed.
doors=golden,wall
# Previous paid healing purchases IN THIS RUN; affects the next price.
paid_heals=1
shop_rerolls=0
trial_seen=false
trial_depth=0
# Abstract donation units: wood,stone,souls,metal,arcane,hourglasses.
# Zero prevents spending unrecorded resources; these are not raw account amounts.
resources=0,0,0,0,0,0
)";
}
}
int main(int argc, char** argv) {
    try {
        if (argc < 2 || std::string_view(argv[1]) == "--help" || std::string_view(argv[1]) == "help") { help(); return 0; }
        const std::string command = argv[1];
        if (command == "--version") { std::cout << "sfld " << engine_version << '\n'; return 0; }
        if (command != "audit" && command != "simulate" && command != "compare" && command != "replay" && command != "state-template")
            throw std::invalid_argument("Unknown command: " + command);
        const std::set<std::string> accepted{"--profile", "--runs", "--threads", "--seed", "--budget", "--deadline-hours",
            "--barrels", "--barrel-hp", "--revive-hp", "--login-hours", "--rerolls", "--prefer-gem", "--sweep", "--budgets",
            "--max-actions", "--output", "--trace", "--run-number", "--state", "--shop-policy", "--recovery-budget"};
        std::map<std::string, std::string> args;
        bool allowed = false;
        for (int i = 2; i < argc; ++i) {
            const std::string key = argv[i];
            if (key == "--help") { help(); return 0; }
            if (key == "--allow-assumptions") { if (allowed) throw std::invalid_argument("Duplicate flag"); allowed = true; continue; }
            if (!accepted.contains(key)) throw std::invalid_argument("Unknown option: " + key);
            if (++i >= argc) throw std::invalid_argument("Missing value for " + key);
            if (!args.emplace(key, argv[i]).second) throw std::invalid_argument("Duplicate option: " + key);
        }
        const auto get = [&args](const std::string& key, std::string fallback) {
            const auto it = args.find(key); return it == args.end() ? fallback : it->second;
        };
        if (command == "state-template") {
            if (args.size() > (args.contains("--output") ? 1U : 0U) || allowed)
                throw std::invalid_argument("state-template accepts only --output PATH");
            if (args.contains("--output")) {
                if (std::filesystem::exists(args.at("--output"))) throw std::invalid_argument("Template output already exists; choose a new file");
                auto file = output_file(args.at("--output")); file << state_template(); file.close();
                std::cout << "Wrote an example. Replace its values with your current progress before simulating.\n";
            } else std::cout << state_template();
            return 0;
        }
        const auto profile_path = get("--profile", "profiles/synthetic.profile");
        const auto profile = Profile::load(profile_path);
        if (command == "replay" && args.contains("--state"))
            throw std::invalid_argument("Replay uses the state embedded in the trace; omit --state");
        std::optional<StartState> start_state;
        if (args.contains("--state")) {
            start_state = StartState::load(args.at("--state"));
            // Check observed affordability too, before opening any output file.
            Game validation(profile, 0, *start_state);
            if (args.contains("--run-number") && numeric<int>(args.at("--run-number")) != start_state->state.run_number)
                throw std::invalid_argument("--run-number conflicts with the progress file");
        }
        if (command == "audit") {
            std::cout << "Profile: " << profile.id << "\nFingerprint: " << profile.fingerprint
                << "\nEvidence: synthetic\nLive calibration: unavailable\n"
                << "Unknown live inputs: room/door weights, barrel odds, damage distributions, escape/key rates,\n"
                << "shop and gem offers, effect timing and full-refill pricing. See docs/MODEL.md.\n"
                << "Optional gems_first_run/gems_later_runs pools fall back to gems when unspecified.\n"
                << "Run commands require --allow-assumptions. This flag is not a calibration claim.\n";
            if (start_state) std::cout << "Progress: room " << start_state->state.room << ", run " << start_state->state.run_number
                << ", " << name(start_state->state.phase) << ", HP " << start_state->state.hp * 100 << "%\n"
                << "Starting-state fingerprint: " << start_state->fingerprint()
                << "\nCurrent choices: " << (start_state->generate_current ? "unknown; will be sampled" : "observed; will be preserved")
                << "\nTime and budget start at this position.\n";
            return 0;
        }
        if (!allowed) throw std::invalid_argument("Synthetic profile: pass --allow-assumptions to run a conditional experiment");
        if (command == "replay") {
            if (!args.contains("--trace")) throw std::invalid_argument("replay requires --trace PATH");
            const auto actions = replay(profile, args.at("--trace"));
            std::cout << "Verified " << actions << " recorded transitions.\n"; return 0;
        }
        if (command != "compare" && (args.contains("--sweep") || args.contains("--budgets")))
            throw std::invalid_argument("Sweep options require compare");
        BatchOptions options;
        options.start = start_state;
        options.runs = numeric<std::size_t>(get("--runs", "10000"));
        options.threads = numeric<unsigned>(get("--threads", std::to_string(std::clamp(std::thread::hardware_concurrency(), 1U, 256U))));
        options.seed = numeric<std::uint64_t>(get("--seed", "42"));
        options.limits.budget = numeric<int>(get("--budget", "0"));
        options.limits.deadline_hours = numeric<double>(get("--deadline-hours", "240"));
        options.limits.max_actions = numeric<std::uint64_t>(get("--max-actions", "100000"));
        options.limits.run_number = start_state ? start_state->state.run_number : numeric<int>(get("--run-number", "1"));
        Policy policy;
        policy.barrels = barrel_policy_from(get("--barrels", "adaptive"));
        policy.barrel_hp = numeric<double>(get("--barrel-hp", ".35"));
        policy.revive_hp = numeric<double>(get("--revive-hp", ".2"));
        policy.login_interval_hours = numeric<double>(get("--login-hours", "0"));
        policy.reroll_limit = numeric<int>(get("--rerolls", "0"));
        policy.shop = shop_policy_from(get("--shop-policy", "adaptive"));
        if (args.contains("--recovery-budget")) {
            policy.recovery_budget = numeric<int>(args.at("--recovery-budget"));
            if (policy.recovery_budget < 0) throw std::invalid_argument("--recovery-budget must be nonnegative");
        }
        if (args.contains("--prefer-gem")) policy.preferred_gem = gem_from(args.at("--prefer-gem"));
        policy.validate();
        // Validate options even for the one-run trace path.
        if (options.runs == 0 || options.runs > 10000000 || options.threads == 0 || options.threads > 256)
            throw std::invalid_argument("Runs must be 1..10000000; threads must be 1..256");
        std::vector<std::pair<Policy, BatchOptions>> experiments;
        if (command == "simulate") experiments.emplace_back(policy, options);
        else {
            if (args.contains("--trace")) throw std::invalid_argument("Use simulate --runs 1 for a trace");
            const auto sweep = get("--sweep", "barrels");
            if (sweep == "barrels") for (int i = 0; i < 4; ++i) {
                auto p = policy; p.barrels = static_cast<BarrelPolicy>(i); p.label = "barrels:" + std::string(name(p.barrels));
                experiments.emplace_back(p, options);
            } else if (sweep == "gems") {
                auto baseline = policy; baseline.preferred_gem.reset(); baseline.label = "baseline";
                experiments.emplace_back(baseline, options);
                auto pool = profile.gems_for_run(options.limits.run_number);
                if (start_state && start_state->decision_phase() == Phase::gems && !start_state->generate_current)
                    pool.assign(start_state->state.gem_offers.begin(), start_state->state.gem_offers.end());
                for (auto g : pool) {
                    if (start_state && start_state->state.has(g)) continue;
                    auto p = policy; p.preferred_gem = g; p.label = "prefer:" + std::string(name(g)); experiments.emplace_back(p, options);
                }
            } else if (sweep == "budgets") {
                std::istringstream input(get("--budgets", "0,10,25,50,100")); std::string value;
                while (std::getline(input, value, ',')) {
                    auto opts = options; opts.limits.budget = numeric<int>(value);
                    auto p = policy; p.label = "budget:" + value; experiments.emplace_back(p, opts);
                }
                if (experiments.empty()) throw std::invalid_argument("Empty budget sweep");
            } else if (sweep == "revive") for (double hp : {.2, .3, .5, .8, 1.0}) {
                auto p = policy; p.revive_hp = hp; p.label = "revive:" + std::to_string(hp); experiments.emplace_back(p, options);
            } else if (sweep == "rerolls") {
                if (options.limits.budget == 0) throw std::invalid_argument("Reroll sweep needs a nonzero mushroom budget");
                for (int limit : {0, 1, 3, 5}) {
                    auto p = policy; p.reroll_limit = limit; p.label = "rerolls:" + std::to_string(limit); experiments.emplace_back(p, options);
                }
            } else if (sweep == "shops") {
                if (options.limits.budget == 0 || policy.reroll_limit == 0)
                    throw std::invalid_argument("Shop-policy sweep needs a nonzero --budget and --rerolls limit");
                auto baseline = policy; baseline.shop = ShopPolicy::adaptive; baseline.reroll_limit = 0; baseline.label = "shop:no-rerolls";
                experiments.emplace_back(baseline, options);
                for (auto shop : {ShopPolicy::adaptive, ShopPolicy::one_hit, ShopPolicy::one_hit_8}) {
                    auto p = policy; p.shop = shop; p.label = "shop:" + std::string(name(shop)); experiments.emplace_back(p, options);
                }
            } else throw std::invalid_argument("Unknown sweep: " + sweep);
        }
        if (args.contains("--trace") && options.runs != 1) throw std::invalid_argument("Tracing requires --runs 1");
        for (const auto* key : {"--trace", "--output"})
            if (args.contains(key) && (same_file(args.at(key), profile_path) ||
                (args.contains("--state") && same_file(args.at(key), args.at("--state")))))
                throw std::invalid_argument("Output must not overwrite the scenario profile or progress file");
        if (args.contains("--trace") && args.contains("--output") && same_file(args.at("--trace"), args.at("--output")))
            throw std::invalid_argument("Trace and JSON report need different paths");
        std::cerr << "SYNTHETIC EXPERIMENT: results are conditional on " << profile.id << ".\n";
        const auto start = std::chrono::steady_clock::now();
        std::uint64_t action_count = 0;
        bool invalid = false;
        std::ostringstream report;
        if (command == "compare") report << "{\"schema_version\":2,\"comparison\":\"paired master seeds; no live-game calibration\",\"experiments\":[\n";
        for (std::size_t i = 0; i < experiments.size(); ++i) {
            const auto& [p, opts] = experiments[i];
            std::vector<Result> results;
            if (args.contains("--trace")) {
                auto trace = output_file(args.at("--trace"));
                results.push_back(run_one(profile, p, Random::mix(opts.seed), opts.limits, &trace, opts.start ? &*opts.start : nullptr)); trace.close();
            } else results = run_batch(profile, p, opts);
            for (const auto& r : results) { action_count += r.actions; invalid = invalid || r.action_limit; }
            if (i) report << ",\n";
            report << report_json(profile, p, opts, results);
            if (command == "compare") std::cerr << "Completed " << p.label << " (" << opts.runs << " runs).\n";
        }
        if (command == "compare") report << "\n]}";
        report << '\n';
        if (args.contains("--output")) { auto file = output_file(args.at("--output")); file << report.str(); file.close(); }
        else std::cout << report.str();
        const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        std::cerr << "Simulated " << experiments.size() * options.runs << " runs and " << action_count << " actions in " << seconds
            << " s (" << static_cast<double>(action_count) / std::max(seconds, 1e-9) << " actions/s).\n";
        if (invalid) { std::cerr << "Action limit reached; deadline statistics invalidated.\n"; return 2; }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n'; return 1;
    }
}
