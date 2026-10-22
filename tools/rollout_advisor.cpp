// Compare visible-state decisions using independent simulated futures.
#include "sfld/rollout.hpp"
#include "sfld/rule_policy.hpp"
#include <charconv>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>

namespace {
template<class T> T integer(const std::string& text) {
    T result{};
    auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), result);
    if (error != std::errc{} || end != text.data() + text.size()) throw std::invalid_argument("Invalid integer: " + text);
    return result;
}
void action(std::ostream& out, sfld::Action value) {
    out << "{\"kind\":" << sfld::json_string(sfld::name(value.kind)) << ",\"index\":" << value.index
        << ",\"value\":" << value.value << '}';
}
}
int main(int argc, char** argv) try {
    using namespace sfld;
    std::map<std::string, std::string> args;
    const std::set<std::string> accepted{"--profile", "--state", "--rules", "--controller", "--budget", "--hours", "--samples", "--seed", "--planning-seed", "--advance", "--max-work"};
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        if (key == "--help") {
            std::cout << "sfld_rollout --profile FILE [--state FILE] [--rules FILE]\n"
                "  --controller legacy|survival|rules (default: legacy at budget0, survival otherwise)\n"
                "  --budget N --hours N       Remaining gross spending cap and horizon (default0,168)\n"
                "  --samples N               Independent futures per action,2..4096 (default64)\n"
                "  --seed N                  Initial observed-game seed (default42)\n"
                "  --planning-seed N         Separate planner seed (default810001)\n"
                "  --advance N               Follow the continuation for N actions before advising\n"
                "  --max-work N              Maximum simulated actions (default10000000)\n"
                "JSON output contains every legal first action's completion mean and paired uncertainty.\n"
                "Imported states start a new remaining-time/spending forecast. Future RNG is never copied.\n";
            return 0;
        }
        if (!accepted.contains(key) || i + 1 == argc || args.contains(key)) throw std::invalid_argument("Invalid or repeated option: " + key);
        args[key] = argv[++i];
    }
    const auto get = [&](const std::string& key, const std::string& fallback) { return args.contains(key) ? args.at(key) : fallback; };
    const auto profile = Profile::load(get("--profile", "profiles/synthetic.profile"));
    Limits limits; limits.budget = integer<int>(get("--budget", "0")); limits.repeat_runs = true;
    const auto hours = get("--hours", "168"); std::size_t end = 0; limits.deadline_hours = std::stod(hours, &end);
    if (end != hours.size() || !std::isfinite(limits.deadline_hours) || limits.deadline_hours <= 0 || limits.budget < 0)
        throw std::invalid_argument("Invalid remaining budget or horizon");
    const auto seed = integer<std::uint64_t>(get("--seed", "42"));
    const auto kind = get("--controller", limits.budget ? "survival" : "legacy");
    if (kind != "legacy" && kind != "survival" && kind != "rules") throw std::invalid_argument("Unknown controller");
    std::optional<RulePolicy> rules;
    if (args.contains("--rules")) rules = RulePolicy::load(args.at("--rules"));
    if (kind == "rules" && !rules) throw std::invalid_argument("rules controller requires --rules");
    Policy policy; policy.strategy = Strategy::key_stockpile; policy.survival_aware = kind == "survival";
    policy.shop_allowance = limits.budget ? 5 : 0; policy.shop_key_reserve = limits.budget ? 4 : 0;
    policy.validate();
    Controller choose = [&](const Game& game) {
        if (kind == "rules") return rules->choose(game);
        if (rules && game.state().phase == Phase::gems) return choose_rule_gem(game, rules->gem_rules);
        return policy.choose(game);
    };
    std::unique_ptr<Game> game;
    if (args.contains("--state")) game = std::make_unique<Game>(profile, seed, StartState::load(args.at("--state")), limits);
    else game = std::make_unique<Game>(profile, seed, limits);
    const auto advance = integer<std::uint64_t>(get("--advance", "0"));
    for (std::uint64_t i = 0; i < advance; ++i) {
        if (game->finished()) throw std::invalid_argument("Event finished before requested decision");
        game->step(choose(*game));
    }
    RolloutOptions options; options.samples = integer<std::size_t>(get("--samples", "64"));
    options.seed = integer<std::uint64_t>(get("--planning-seed", "810001"));
    options.max_total_actions = integer<std::uint64_t>(get("--max-work", "10000000"));
    const auto report = evaluate_rollouts(*game, choose, options);
    std::cout << std::setprecision(12) << "{\"schema\":1,\"engine\":" << json_string(engine_version)
        << ",\"profile_fingerprint\":" << json_string(profile.fingerprint)
        << ",\"samples_per_action\":" << report.samples << ",\"simulated_actions\":" << report.simulated_actions
        << ",\"planning_seed\":" << json_string(std::to_string(options.seed))
        << ",\"reference_index\":" << report.reference_index << ",\"best_index\":" << report.best_index
        << ",\"interpretation\":\"Finite-sample, one-step policy improvement estimate; follows the continuation thereafter. No optimality guarantee.\""
        << ",\"observation\":" << observation_json(*game) << ",\"actions\":[";
    bool comma = false;
    for (const auto& value : report.actions) {
        if (comma) std::cout << ','; comma = true;
        std::cout << "{\"action\":"; action(std::cout, value.action);
        std::cout << ",\"additional_completions_mean\":" << value.mean << ",\"standard_error\":" << value.standard_error
            << ",\"paired_advantage\":" << value.paired_advantage << ",\"paired_standard_error\":" << value.paired_standard_error << '}';
    }
    std::cout << "]}\n";
    return 0;
} catch (const std::exception& error) { std::cerr << "sfld_rollout: " << error.what() << '\n'; return 1; }
