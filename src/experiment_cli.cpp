#include "sfld/experiment.hpp"
#include <algorithm>
#include <charconv>
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

namespace sfld {
namespace {
template<class T> T number(const std::string& text) {
    T value{}; const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) throw std::invalid_argument("Invalid number: " + text);
    if constexpr (std::is_floating_point_v<T>) if (!std::isfinite(value)) throw std::invalid_argument("Non-finite number");
    return value;
}
void help() {
    std::cout << R"(Event experiments (synthetic, normal LD only):
  sfld event --allow-assumptions --budget 500 --deadline-hours 168 --runs 1000
  sfld train --allow-assumptions --budget 500 --save-policy out/paid.policy
  sfld event --allow-assumptions --policy out/paid.policy --budget 500
  sfld agent --allow-assumptions --budget 500

event: repeated runs sharing one time/mushroom budget; default 1000 events.
train: learn visible-state action scores from scratch using population search;
       --runs is the number of held-out evaluation events, never training events.
agent: JSON observations on stdout. Reply on stdin: ACTION INDEX VALUE (one line).
       For example: choose_door 0 0, buy 1 0, heal_step 0 0, wait 0 4.8.
       Illegal commands return an error and the unchanged observation. EOF stops.

Options:
  --profile PATH        Default profiles/synthetic.profile
  --state PATH          Start from observed progress; remaining event limits apply
  --budget N            Shared event budget, default 500 (use 0 for F2P)
  --deadline-hours H    Default 168 (seven days)
  --restart-health MODE full (player report), carry, empty
  --run-number N        1 for first event run; 2+ enables the later-run gem pool
  --seed N              Default 42; held-out training evaluation uses another stream
  --runs N              Default 1000; independent events
  --threads N           Event workers, default hardware concurrency
  --max-actions N       Event-wide safety cap, default 100000
  --policy PATH         Evaluate a learned policy instead of the baseline
  --barrels POLICY      Baseline only: skip, always, low-hp, adaptive
  --rerolls N           Baseline maximum rerolls per shop (default 0)
  --shop-policy POLICY  Baseline: adaptive, one-hit, one-hit-8
  --revive-hp FRACTION  Baseline recovery target (default .2)
  --generations N       train only, default 12
  --population N        train only, default 32
  --training-events N   Per candidate per generation, default 24
  --save-policy PATH    Required for train; refuses to overwrite existing files
  --output PATH         Event/evaluation report; refuses to overwrite input files
  --trace PATH          agent only: observation/action JSONL audit trail

See docs/EXPERIMENTS.md and docs/AGENT_RULES.md. No game server connections.
)";
}
std::ofstream output(const std::string& path) {
    const auto parent = std::filesystem::path(path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent);
    std::ofstream out(path); if (!out) throw std::runtime_error("Cannot write: " + path);
    out.exceptions(std::ios::badbit | std::ios::failbit); return out;
}
bool same_path(const std::string& a, const std::string& b) {
    if (std::filesystem::exists(a) && std::filesystem::exists(b)) return std::filesystem::equivalent(a, b);
    auto x = std::filesystem::weakly_canonical(a).generic_string(), y = std::filesystem::weakly_canonical(b).generic_string();
#ifdef _WIN32
    const auto lower = [](unsigned char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + 'a' - 'A') : static_cast<char>(c); };
    std::transform(x.begin(), x.end(), x.begin(), lower); std::transform(y.begin(), y.end(), y.begin(), lower);
#endif
    return x == y;
}
}
int experiment_cli(int argc, char** argv) {
    const std::string command = argv[1];
    const std::set<std::string> accepted{"--profile", "--state", "--budget", "--deadline-hours", "--restart-health", "--run-number",
        "--seed", "--runs", "--threads", "--max-actions", "--policy", "--barrels", "--rerolls", "--shop-policy", "--revive-hp",
        "--generations", "--population", "--training-events", "--save-policy", "--output", "--trace"};
    std::map<std::string, std::string> args; bool allowed = false;
    for (int i = 2; i < argc; ++i) {
        const std::string key = argv[i];
        if (key == "--help") { help(); return 0; }
        if (key == "--allow-assumptions") { if (allowed) throw std::invalid_argument("Duplicate flag"); allowed = true; continue; }
        if (!accepted.contains(key)) throw std::invalid_argument("Unknown option: " + key);
        if (++i >= argc) throw std::invalid_argument("Missing value for " + key);
        if (!args.emplace(key, argv[i]).second) throw std::invalid_argument("Duplicate option: " + key);
    }
    if (!allowed) throw std::invalid_argument("Synthetic experiment requires --allow-assumptions");
    const auto get = [&](const std::string& key, std::string fallback) { const auto it = args.find(key); return it == args.end() ? fallback : it->second; };
    if (command != "train") for (const auto* key : {"--generations", "--population", "--training-events", "--save-policy"})
        if (args.contains(key)) throw std::invalid_argument(std::string(key) + " requires train");
    if (command != "agent" && args.contains("--trace")) throw std::invalid_argument("Event traces require agent");
    if (command == "agent") for (const auto* key : {"--policy", "--barrels", "--rerolls", "--shop-policy", "--revive-hp", "--output", "--runs", "--threads"})
        if (args.contains(key)) throw std::invalid_argument(std::string(key) + " does not apply to agent");
    if (command == "train" && (args.contains("--policy") || !args.contains("--save-policy")))
        throw std::invalid_argument("train needs --save-policy and starts without --policy");
    if (command == "train" || args.contains("--policy")) for (const auto* key : {"--barrels", "--rerolls", "--shop-policy", "--revive-hp"})
        if (args.contains(key)) throw std::invalid_argument("Baseline options do not apply to learned policies");
    const std::string profile_path = get("--profile", "profiles/synthetic.profile");
    const auto profile = Profile::load(profile_path);
    BatchOptions options;
    options.runs = number<std::size_t>(get("--runs", "1000"));
    options.threads = number<unsigned>(get("--threads", std::to_string(std::clamp(std::thread::hardware_concurrency(), 1U, 256U))));
    options.seed = number<std::uint64_t>(get("--seed", "42"));
    options.limits.budget = number<int>(get("--budget", "500"));
    options.limits.deadline_hours = number<double>(get("--deadline-hours", "168"));
    options.limits.max_actions = number<std::uint64_t>(get("--max-actions", "100000"));
    options.limits.run_number = number<int>(get("--run-number", "1"));
    options.limits.repeat_runs = true; options.limits.restart_health = get("--restart-health", "full");
    if (options.runs < 1 || options.runs > 10000000 || options.threads < 1 || options.threads > 256)
        throw std::invalid_argument("Invalid event count or threads");
    if (args.contains("--state")) {
        options.start = StartState::load(args.at("--state")); options.start->validate(profile);
        if (args.contains("--run-number") && options.limits.run_number != options.start->state.run_number)
            throw std::invalid_argument("--run-number conflicts with progress");
        options.limits.run_number = options.start->state.run_number;
    }
    Game validation = options.start ? Game(profile, options.seed, *options.start, options.limits) : Game(profile, options.seed, options.limits);
    for (const auto* key : {"--output", "--trace", "--save-policy"}) if (args.contains(key)) {
        if (same_path(args.at(key), profile_path)) throw std::invalid_argument("Output would overwrite profile");
        for (const auto* other : {"--state", "--policy", "--output", "--trace", "--save-policy"})
            if (std::string_view(key) != other && args.contains(other) && same_path(args.at(key), args.at(other)))
                throw std::invalid_argument("Input and output paths must be distinct");
    }
    if (args.contains("--save-policy") && std::filesystem::exists(args.at("--save-policy")))
        throw std::invalid_argument("Policy output exists; choose a new path");
    std::cerr << "Synthetic event; damage=" << profile.damage_distribution << ", restart health=" << options.limits.restart_health << ".\n";
    if (command == "agent") {
        std::ofstream trace;
        if (args.contains("--trace")) {
            trace = output(args.at("--trace"));
            trace << "{\"type\":\"manifest\",\"engine\":" << json_string(engine_version) << ",\"seed\":" << options.seed
                  << ",\"profile_fingerprint\":" << json_string(profile.fingerprint) << ",\"budget\":" << options.limits.budget
                  << ",\"deadline_hours\":" << options.limits.deadline_hours << ",\"restart_health\":" << json_string(options.limits.restart_health) << "}\n";
        }
        const auto observe = [&] { const auto line = observation_json(validation); std::cout << line << std::endl; if (trace.is_open()) trace << line << '\n'; };
        observe(); std::string line;
        while (!validation.finished() && std::getline(std::cin, line)) {
            try {
                if (line.size() > 1024) throw std::invalid_argument("Command too long");
                std::istringstream in(line); std::string kind, index, value, extra;
                if (!(in >> kind >> index >> value) || (in >> extra)) throw std::invalid_argument("Expected ACTION INDEX VALUE");
                Action action{action_from(kind), number<int>(index), number<double>(value)};
                if (!validation.legal(action)) throw std::invalid_argument("Action is not legal in this state or exceeds the deadline");
                if (trace.is_open()) trace << "{\"type\":\"action\",\"command\":" << json_string(line) << "}\n";
                validation.step(action);
            } catch (const std::invalid_argument& e) { std::cout << "{\"error\":" << json_string(e.what()) << "}\n"; }
            observe();
        }
        if (trace.is_open()) trace.close();
        return validation.state().actions >= options.limits.max_actions ? 2 : 0;
    }
    Policy baseline; baseline.barrels = barrel_policy_from(get("--barrels", "adaptive"));
    baseline.shop = shop_policy_from(get("--shop-policy", "adaptive")); baseline.reroll_limit = number<int>(get("--rerolls", "0"));
    baseline.revive_hp = number<double>(get("--revive-hp", ".2")); baseline.validate();
    std::optional<LearnedPolicy> learned;
    std::string label = "baseline:barrels=" + std::string(name(baseline.barrels)) + ",shop=" + std::string(name(baseline.shop)) +
        ",rerolls=" + std::to_string(baseline.reroll_limit) + ",revive=" + std::to_string(baseline.revive_hp);
    const auto training_seed = options.seed;
    if (command == "train") {
        TrainingOptions config;
        config.generations = number<unsigned>(get("--generations", "12")); config.population = number<unsigned>(get("--population", "32"));
        config.episodes = number<std::size_t>(get("--training-events", "24"));
        learned = train_policy(profile, options, config, std::cerr);
        const auto parent = std::filesystem::path(args.at("--save-policy")).parent_path();
        if (!parent.empty()) std::filesystem::create_directories(parent);
        learned->save(args.at("--save-policy"));
        options.seed = Random::mix(training_seed ^ UINT64_C(0x686f6c646f7574));
        label = "learned-held-out";
    } else if (args.contains("--policy")) { learned = LearnedPolicy::load(args.at("--policy")); label = args.at("--policy"); }
    const Controller choose = [&](const Game& game) { return learned ? learned->choose(game) : baseline.choose(game); };
    const auto results = event_batch(profile, choose, options);
    std::string report = event_report(profile, options, results, label);
    if (command == "train") {
        report.pop_back();
        report += ",\"training_seed\":" + std::to_string(training_seed) + ",\"training_generations\":" + get("--generations", "12")
            + ",\"training_population\":" + get("--population", "32") + ",\"training_events_per_candidate\":" + get("--training-events", "24") + '}';
    }
    if (args.contains("--output")) { auto out = output(args.at("--output")); out << report << '\n'; out.close(); }
    else std::cout << report << '\n';
    return std::any_of(results.begin(), results.end(), [](const auto& r) { return r.action_limit; }) ? 2 : 0;
}
}
