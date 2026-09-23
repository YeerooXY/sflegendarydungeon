#include "sfld/batch.hpp"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <exception>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace sfld {
Result run_one(const Profile& profile, const Policy& policy, std::uint64_t seed, Limits limits, std::ostream* trace, const StartState* start) {
    if (start) limits.run_number = start->state.run_number;
    Game game = start ? Game(profile, seed, *start, limits) : Game(profile, seed, limits);
    if (trace) {
        *trace << "#sfld-trace-v2\t" << engine_version << '\t' << seed << '\t' << profile.fingerprint << '\t' << limits.budget
            << '\t' << std::setprecision(17) << limits.deadline_hours << '\t' << limits.max_actions << '\t' << limits.run_number << '\n';
        const auto input = start ? start->encode() : std::string{};
        *trace << "#start\t" << (start ? start->fingerprint() : "fresh") << '\t' << std::count(input.begin(), input.end(), '\n')
            << '\n' << input << "#actions\n";
    }
    while (game.state().phase != Phase::complete && game.state().elapsed_hours < limits.deadline_hours && game.state().actions < limits.max_actions) {
        auto action = policy.choose(game);
        const double left = limits.deadline_hours - game.state().elapsed_hours;
        if (action.kind == ActionKind::wait) action.value = std::min(action.value, left);
        else if (profile.action_seconds / 3600 > left) break;
        std::string before;
        if (trace) before = state_digest(game.state());
        game.step(action);
        if (trace) *trace << game.state().actions << '\t' << name(action.kind) << '\t' << action.index << '\t'
            << std::setprecision(17) << action.value << '\t' << before << '\t' << state_digest(game.state()) << '\t'
            << game.state().room << '\t' << game.state().hp << '\t' << game.state().elapsed_hours << '\n';
    }
    const auto& s = game.state();
    Result r;
    r.complete = s.phase == Phase::complete;
    r.action_limit = !r.complete && s.actions >= limits.max_actions;
    r.hours = r.complete || r.action_limit ? s.elapsed_hours : limits.deadline_hours;
    r.active_hours = s.active_hours; r.mushrooms = s.mushrooms; r.recovery_mushrooms = s.recovery_mushrooms;
    r.reroll_mushrooms = s.reroll_mushrooms; r.deaths = s.deaths; r.room = s.room;
    r.barrels_opened = s.barrels_opened; r.barrels_skipped = s.barrels_skipped; r.actions = s.actions; r.gems = s.gems;
    r.gold_units = s.gold_units; r.lucky_coins = s.lucky_coins; r.epics = s.epics; r.legendaries = s.legendaries;
    r.shop_purchases = s.shop_purchases; r.strong_one_hit_purchases = s.strong_one_hit_purchases;
    return r;
}
std::vector<Result> run_batch(const Profile& profile, const Policy& policy, const BatchOptions& options) {
    profile.validate(); policy.validate();
    if (options.start) options.start->validate(profile);
    if (options.runs == 0 || options.runs > 10000000 || options.threads == 0 || options.threads > 256)
        throw std::invalid_argument("Runs must be 1..10000000; threads must be 1..256");
    std::vector<Result> results(options.runs);
    std::atomic<std::size_t> next{0};
    std::atomic<bool> failed{false};
    std::exception_ptr failure;
    std::mutex failure_mutex;
    const auto worker = [&] {
        try {
            while (!failed.load(std::memory_order_relaxed)) {
                const auto start = next.fetch_add(64, std::memory_order_relaxed);
                if (start >= options.runs) break;
                const auto end = std::min(options.runs, start + 64);
                for (auto i = start; i < end; ++i) {
                    const auto seed = Random::mix(options.seed ^ static_cast<std::uint64_t>(i));
                    try { results[i] = run_one(profile, policy, seed, options.limits, nullptr, options.start ? &*options.start : nullptr); }
                    catch (const std::exception& e) {
                        throw std::runtime_error("Run index " + std::to_string(i) + ", derived seed " + std::to_string(seed) + ": " + e.what());
                    }
                }
            }
        } catch (...) {
            failed.store(true, std::memory_order_relaxed);
            std::lock_guard lock(failure_mutex);
            if (!failure) failure = std::current_exception();
        }
    };
    // jthread also joins safely if creation of a later worker fails.
    std::vector<std::jthread> workers;
    const auto threads = std::min<std::size_t>(options.threads, options.runs);
    for (std::size_t i = 1; i < threads; ++i) workers.emplace_back(worker);
    worker();
    for (auto& thread : workers) thread.join();
    if (failure) std::rethrow_exception(failure);
    return results;
}
namespace {
double percentile(const std::vector<double>& sorted, double p) {
    const double rank = p * static_cast<double>(sorted.size() - 1);
    const auto lo = static_cast<std::size_t>(rank);
    const auto hi = std::min(lo + 1, sorted.size() - 1);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - static_cast<double>(lo));
}
std::string number_or_null(double value, bool valid) {
    if (!valid) return "null";
    std::ostringstream out; out << std::setprecision(17) << value; return out.str();
}
}
std::string report_json(const Profile& profile, const Policy& policy, const BatchOptions& options, const std::vector<Result>& results) {
    if (results.empty()) throw std::invalid_argument("Cannot summarize an empty batch");
    std::vector<double> finished;
    double sum_hours = 0, sum_active = 0, spend = 0, recovery_spend = 0, reroll_spend = 0, deaths = 0;
    double opened = 0, skipped = 0;
    double gold_units = 0, lucky_coins = 0, epics = 0, legendaries = 0, purchases = 0, strong_one_hits = 0;
    std::uint64_t actions = 0;
    std::size_t capped = 0;
    std::array<std::size_t, ix(Gem::count)> picked{};
    for (const auto& r : results) {
        if (r.complete) finished.push_back(r.hours);
        sum_hours += r.hours; sum_active += r.active_hours; spend += r.mushrooms;
        recovery_spend += r.recovery_mushrooms; reroll_spend += r.reroll_mushrooms;
        deaths += r.deaths; opened += r.barrels_opened; skipped += r.barrels_skipped; actions += r.actions;
        gold_units += r.gold_units; lucky_coins += r.lucky_coins; epics += r.epics; legendaries += r.legendaries;
        purchases += r.shop_purchases; strong_one_hits += r.strong_one_hit_purchases;
        capped += r.action_limit ? 1 : 0;
        for (std::size_t i = 0; i < picked.size(); ++i)
            picked[i] += r.gems[i] && !(options.start && options.start->state.gems[i]) ? 1 : 0;
    }
    std::sort(finished.begin(), finished.end());
    const double n = static_cast<double>(results.size()), successes = static_cast<double>(finished.size());
    const double p = successes / n, z = 1.959963984540054, z2 = z * z;
    const double center = (p + z2 / (2 * n)) / (1 + z2 / n);
    const double radius = z * std::sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n);
    const double mean = finished.empty() ? 0 : std::accumulate(finished.begin(), finished.end(), 0.0) / successes;
    const double restricted_mean = sum_hours / n;
    double restricted_squared = 0;
    for (const auto& r : results) restricted_squared += (r.hours - restricted_mean) * (r.hours - restricted_mean);
    const double restricted_se = results.size() > 1 ? std::sqrt(restricted_squared / (n - 1) / n) : 0;
    double squared = 0;
    for (double h : finished) squared += (h - mean) * (h - mean);
    const double se = finished.size() > 1 ? std::sqrt(squared / (successes - 1) / successes) : 0;
    std::ostringstream out; out << std::setprecision(17);
    out << "{\n  \"schema_version\": 2,\n  \"engine_version\": " << json_string(engine_version) << ",\n  \"profile\": " << json_string(profile.id)
        << ",\n  \"profile_fingerprint\": " << json_string(profile.fingerprint)
        << ",\n  \"evidence\": \"synthetic\",\n  \"calibrated_to_live_game\": false,\n"
        << "  \"interpretation\": \"Conditional on the supplied assumptions; intervals measure Monte Carlo sampling error only.\",\n"
        << "  \"measurement_origin\": " << json_string(options.start ? "entered progress; additional time and spending" : "start of a new run")
        << ",\n  \"starting_state\": ";
    if (options.start) {
        const auto& start = *options.start;
        out << "{\"fingerprint\":" << json_string(start.fingerprint()) << ",\"room\":" << start.state.room
            << ",\"phase\":" << json_string(name(start.state.phase)) << ",\"hp_percent\":" << start.state.hp * 100
            << ",\"generate_current_decision\":" << (start.generate_current ? "true" : "false")
            << ",\"snapshot\":" << json_string(start.encode()) << '}';
    } else out << "null";
    const int run_number = options.start ? options.start->state.run_number : options.limits.run_number;
    out << ",\n  \"future_gem_pool\": [";
    const auto& pool = profile.gems_for_run(run_number);
    for (std::size_t i = 0; i < pool.size(); ++i) { if (i) out << ','; out << json_string(name(pool[i])); }
    out << "],\n  \"future_gem_pool_source\": " << json_string(&pool == &profile.gem_pool ? "gems (fallback; run-specific split unverified)" :
        (run_number == 1 ? "gems_first_run" : "gems_later_runs"))
        << ",\n  \"policy\": " << json_string(policy.label) << ",\n  \"barrels\": " << json_string(name(policy.barrels))
        << ",\n  \"barrel_hp_threshold\": " << policy.barrel_hp << ",\n  \"revive_hp\": " << policy.revive_hp
        << ",\n  \"login_interval_hours\": " << policy.login_interval_hours << ",\n  \"reroll_limit\": " << policy.reroll_limit
        << ",\n  \"shop_policy\": " << json_string(name(policy.shop))
        << ",\n  \"paid_recovery_budget\": " << (policy.recovery_budget < 0 ? "null" : std::to_string(policy.recovery_budget))
        << ",\n  \"shop_strong_effect_probability\": " << profile.shop_strong_effect
        << ",\n  \"shop_offer_model\": \"distinct identities; weighted without replacement; independent strength draws\""
        << ",\n  \"preferred_gem\": " << (policy.preferred_gem ? json_string(name(*policy.preferred_gem)) : "null")
        << ",\n  \"seed\": " << options.seed << ",\n  \"runs\": " << results.size()
        << ",\n  \"run_number_within_event\": " << run_number
        << ",\n  \"max_actions_per_run\": " << options.limits.max_actions
        << ",\n  \"mushroom_budget_per_run\": " << options.limits.budget << ",\n  \"deadline_hours\": " << options.limits.deadline_hours
        << ",\n  \"completed\": " << finished.size() << ",\n  \"unfinished\": " << results.size() - finished.size()
        << ",\n  \"action_limit_exceeded\": " << capped << ",\n  \"valid_deadline_experiment\": " << (capped == 0 ? "true" : "false")
        << ",\n  \"completion_probability\": " << number_or_null(p, capped == 0)
        << ",\n  \"completion_probability_wilson_95\": "
        << (capped ? "null" : "[" + number_or_null(std::max(0.0, center - radius), true) + "," + number_or_null(std::min(1.0, center + radius), true) + "]")
        << ",\n  \"restricted_mean_hours_at_deadline\": " << number_or_null(restricted_mean, capped == 0)
        << ",\n  \"restricted_mean_standard_error\": " << number_or_null(restricted_se, capped == 0 && results.size() > 1)
        << ",\n  \"completed_only_mean_hours\": " << number_or_null(mean, !finished.empty())
        << ",\n  \"completed_only_mean_standard_error\": " << number_or_null(se, finished.size() > 1)
        << ",\n  \"completed_only_median_hours\": " << number_or_null(finished.empty() ? 0 : percentile(finished, .5), !finished.empty())
        << ",\n  \"completed_only_p90_hours\": " << number_or_null(finished.empty() ? 0 : percentile(finished, .9), !finished.empty())
        << ",\n  \"mean_active_hours_per_started_run\": " << sum_active / n
        << ",\n  \"mean_mushrooms_per_started_run\": " << spend / n
        << ",\n  \"total_mushrooms_divided_by_completions\": " << number_or_null(finished.empty() ? 0 : spend / successes, !finished.empty())
        << ",\n  \"mean_recovery_mushrooms\": " << recovery_spend / n << ",\n  \"mean_reroll_mushrooms\": " << reroll_spend / n
        << ",\n  \"mean_deaths\": " << deaths / n << ",\n  \"mean_barrels_opened\": " << opened / n
        << ",\n  \"mean_barrels_skipped\": " << skipped / n << ",\n  \"total_actions\": " << actions
        << ",\n  \"mean_shop_purchases\": " << purchases / n
        << ",\n  \"mean_strong_one_hit_purchases\": " << strong_one_hits / n
        << ",\n  \"mean_gold_reward_units\": " << gold_units / n
        << ",\n  \"mean_lucky_coins\": " << lucky_coins / n
        << ",\n  \"mean_epics\": " << epics / n << ",\n  \"mean_legendaries\": " << legendaries / n
        << ",\n  \"gem_pick_counts\": {";
    for (std::size_t i = 0; i < picked.size(); ++i) {
        if (i) out << ',';
        out << '\n' << "    " << json_string(name(static_cast<Gem>(i))) << ": " << picked[i];
    }
    out << "\n  }\n}";
    return out.str();
}
std::size_t replay(const Profile& profile, const std::string& path) {
    std::ifstream file(path);
    if (!file) throw std::runtime_error("Cannot read trace: " + path);
    std::string line, magic, fingerprint;
    if (!std::getline(file, line)) throw std::invalid_argument("Empty trace");
    std::istringstream header(line);
    std::uint64_t seed = 0; Limits limits;
    if (!(header >> magic) || (magic != "#sfld-trace-v1" && magic != "#sfld-trace-v2"))
        throw std::invalid_argument("Invalid trace header");
    if (magic == "#sfld-trace-v1") throw std::invalid_argument("Legacy trace: replay with engine 0.2.0; mechanics changed in 0.3.0");
    if (magic == "#sfld-trace-v2") {
        std::string version;
        if (!(header >> version) || version != engine_version) throw std::invalid_argument("Trace engine version does not match; use the version recorded in its header");
    }
    if (!(header >> seed >> fingerprint >> limits.budget >> limits.deadline_hours >> limits.max_actions >> limits.run_number))
        throw std::invalid_argument("Invalid trace limits");
    if (fingerprint != profile.fingerprint) throw std::invalid_argument("Trace profile fingerprint does not match");
    std::optional<StartState> start;
    if (magic == "#sfld-trace-v2") {
        if (!std::getline(file, line)) throw std::invalid_argument("Missing trace start block");
        std::istringstream block(line); std::string marker, expected_fingerprint; std::size_t count = 0;
        if (!(block >> marker >> expected_fingerprint >> count) || marker != "#start" || count > 100)
            throw std::invalid_argument("Invalid trace start block");
        std::string input;
        for (std::size_t i = 0; i < count; ++i) {
            if (!std::getline(file, line)) throw std::invalid_argument("Truncated trace start block");
            input += line + '\n';
            if (input.size() > 65536) throw std::invalid_argument("Oversized trace start block");
        }
        if (count) {
            start = StartState::parse(input); start->validate(profile);
            if (start->fingerprint() != expected_fingerprint || start->state.run_number != limits.run_number)
                throw std::invalid_argument("Trace starting-state fingerprint/run number does not match");
        } else if (expected_fingerprint != "fresh") throw std::invalid_argument("Missing trace starting state");
        if (!std::getline(file, line)) throw std::invalid_argument("Missing trace actions marker");
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line != "#actions") throw std::invalid_argument("Missing trace actions marker");
    }
    Game game = start ? Game(profile, seed, *start, limits) : Game(profile, seed, limits);
    std::size_t lines = 0;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        std::istringstream row(line);
        std::uint64_t step = 0; std::string kind, before, after; Action a{ActionKind::skip};
        if (!(row >> step >> kind >> a.index >> a.value >> before >> after)) throw std::invalid_argument("Malformed trace row");
        if (step != game.state().actions + 1 || before != state_digest(game.state()))
            throw std::runtime_error("Replay pre-state mismatch at action " + std::to_string(step));
        a.kind = action_from(kind); game.step(a);
        if (after != state_digest(game.state())) throw std::runtime_error("Replay outcome mismatch at action " + std::to_string(step));
        ++lines;
    }
    if (!file.eof()) throw std::runtime_error("Trace read failed");
    return lines;
}
}
