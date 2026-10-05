#include "sfld/experiment.hpp"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace sfld {
std::vector<Action> legal_actions(const Game& game) {
    std::vector<Action> result;
    if (game.finished()) return result;
    const auto add = [&](Action a) { if (game.legal(a)) result.push_back(a); };
    for (auto k : {ActionKind::choose_door, ActionKind::buy, ActionKind::choose_gem, ActionKind::rps})
        for (int i = 0; i < (k == ActionKind::choose_gem || k == ActionKind::rps ? 3 : 2); ++i) add({k, i});
    for (auto k : {ActionKind::fight, ActionKind::flee, ActionKind::interact, ActionKind::skip, ActionKind::reroll,
                   ActionKind::heal_step, ActionKind::heal_full, ActionKind::reenter, ActionKind::restart}) add({k});
    if (game.state().phase == Phase::recovery) {
        double previous = -1;
        for (double target : {.2, .4, .6, .8, 1.0}) {
            const double hours = std::min((target - game.state().hp) * game.profile().recovery_hours,
                                         game.limits().deadline_hours - game.state().elapsed_hours);
            if (hours > 1e-12 && std::abs(hours - previous) > 1e-12) { add({ActionKind::wait, 0, hours}); previous = hours; }
        }
        // If less than one ordinary action fits, allow the clock to expire.
        if (result.empty()) add({ActionKind::wait, 0, game.limits().deadline_hours - game.state().elapsed_hours});
    }
    return result;
}
namespace {
void effect_json(std::ostream& out, const Effect& e) {
    constexpr std::string_view clocks[]{"room", "trap", "door", "fight"};
    out << "{\"kind\":" << json_string(name(e.kind)) << ",\"magnitude\":" << e.magnitude
        << ",\"remaining\":" << e.remaining << ",\"clock\":" << json_string(clocks[ix(e.clock)])
        << ",\"starts_at\":" << e.starts_at << '}';
}
void action_json(std::ostream& out, Action a) {
    out << "{\"kind\":" << json_string(name(a.kind)) << ",\"index\":" << a.index << ",\"value\":" << a.value << '}';
}
}
std::string observation_json(const Game& game) {
    const auto& s = game.state(); const auto& l = game.limits();
    const auto phase = s.phase == Phase::recovery ? game.recovery_destination() : s.phase;
    std::ostringstream out; out << std::setprecision(17) << std::boolalpha;
    out << "{\"schema\":1,\"engine\":" << json_string(engine_version) << ",\"synthetic\":true,\"terminal\":" << game.finished()
        << ",\"action_limit\":" << (s.actions >= l.max_actions) << ",\"phase\":" << json_string(name(s.phase))
        << ",\"resume_phase\":" << json_string(name(phase)) << ",\"room\":" << s.room << ",\"run_number\":" << s.run_number
        << ",\"completed_runs\":" << s.completed_runs << ",\"hp\":" << s.hp << ",\"keys\":" << s.keys
        << ",\"turn\":" << s.turn << ",\"elapsed_hours\":" << s.elapsed_hours
        << ",\"hours_left\":" << std::max(0.0, l.deadline_hours - s.elapsed_hours)
        << ",\"mushrooms_spent\":" << s.mushrooms << ",\"mushrooms_left\":" << l.budget - s.mushrooms
        << ",\"recovery_mushrooms\":" << s.recovery_mushrooms << ",\"reroll_mushrooms\":" << s.reroll_mushrooms
        << ",\"heal_step_price\":" << game.step_price() << ",\"heal_full_price\":" << (s.hp < 1 ? game.full_price() : 0)
        << ",\"paid_heals\":" << s.paid_steps << ",\"shop_rerolls\":" << s.shop_rerolls
        << ",\"trial_depth\":" << s.trial_depth << ",\"trial_seen\":" << s.trial_seen
        << ",\"pending_trial_reward\":" << s.pending_trial_reward
        << ",\"actions\":" << s.actions << ",\"deaths\":" << s.deaths
        << ",\"first_completion_hours\":";
    if (s.first_completion_hours < 0) out << "null"; else out << s.first_completion_hours;
    out << ",\"resources\":[";
    for (std::size_t i = 0; i < s.resources.size(); ++i) { if (i) out << ','; out << s.resources[i]; }
    out << "],\"gems\":["; bool comma = false;
    for (std::size_t i = 0; i < s.gems.size(); ++i) if (s.gems[i]) {
        if (comma) out << ',';
        comma = true; out << json_string(name(static_cast<Gem>(i)));
    }
    out << ']';
    for (const auto& [label, effects] : {std::pair{"blessings", &s.blessings}, std::pair{"curses", &s.curses}}) {
        out << ',' << json_string(label) << ":[";
        for (int i = 0; i < effects->size; ++i) { if (i) out << ','; effect_json(out, effects->slots[static_cast<std::size_t>(i)]); }
        out << ']';
    }
    if (phase == Phase::doors) {
        out << ",\"doors\":[";
        for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            const auto& d = s.doors[i];
            out << "{\"kind\":" << json_string(name(d.kind)) << ",\"trap\":" << d.trap
                << ",\"cursed_trap\":" << d.cursed_trap << ",\"key_cost\":" << game.door_cost(d.kind)
                << ",\"available\":" << game.available(d) << '}';
        }
        out << ']';
    } else if (phase == Phase::encounter) {
        out << ",\"encounter\":" << json_string(name(s.encounter));
        if (s.encounter == Encounter::flooded) out << ",\"flooded_seconds\":" << s.flooded_seconds;
    } else if (phase == Phase::shop || phase == Phase::curse_shop) {
        out << ",\"offers\":[";
        for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            out << "{\"keys\":" << s.offers[i].keys << ",\"effect\":";
            effect_json(out, s.offers[i].effect); out << '}';
        }
        out << ']';
    } else if (phase == Phase::gems) {
        out << ",\"gem_offers\":[";
        for (std::size_t i = 0; i < 3; ++i) { if (i) out << ','; out << json_string(name(s.gem_offers[i])); }
        out << ']';
    }
    out << ",\"legal_actions\":["; comma = false;
    for (auto a : legal_actions(game)) { if (comma) out << ','; comma = true; action_json(out, a); }
    out << "]}"; return out.str();
}
EventResult run_event(const Profile& profile, const Controller& choose, std::uint64_t seed, Limits limits, const StartState* start) {
    limits.repeat_runs = true;
    Game game = start ? Game(profile, seed, *start, limits) : Game(profile, seed, limits);
    while (!game.finished()) {
        auto action = choose(game);
        if (action.kind == ActionKind::wait) action.value = std::min(action.value, limits.deadline_hours - game.state().elapsed_hours);
        game.step(action);
    }
    const auto& s = game.state();
    return {s.completed_runs, s.mushrooms, s.recovery_mushrooms, s.reroll_mushrooms, s.room, s.deaths,
            s.first_completion_hours, s.elapsed_hours, s.actions, s.actions >= limits.max_actions};
}
std::vector<EventResult> event_batch(const Profile& profile, const Controller& choose, const BatchOptions& options) {
    if (options.runs < 1 || options.runs > 10000000 || options.threads < 1 || options.threads > 256)
        throw std::invalid_argument("Invalid event count or thread count");
    std::vector<EventResult> results(options.runs);
    std::atomic<std::size_t> next{0}; std::atomic<bool> failed{false};
    std::exception_ptr failure; std::mutex mutex;
    const auto worker = [&] {
        try {
            while (!failed.load()) {
                const auto i = next.fetch_add(1);
                if (i >= results.size()) break;
                results[i] = run_event(profile, choose, Random::mix(options.seed ^ static_cast<std::uint64_t>(i)), options.limits,
                                       options.start ? &*options.start : nullptr);
            }
        } catch (...) { failed = true; std::lock_guard lock(mutex); if (!failure) failure = std::current_exception(); }
    };
    std::vector<std::jthread> workers;
    for (std::size_t i = 1; i < std::min<std::size_t>(options.threads, options.runs); ++i) workers.emplace_back(worker);
    worker(); for (auto& thread : workers) thread.join(); if (failure) std::rethrow_exception(failure);
    return results;
}
std::string event_report(const Profile& profile, const BatchOptions& options, const std::vector<EventResult>& results, std::string_view label) {
    if (results.empty()) throw std::invalid_argument("No event results");
    double total = 0, squares = 0, spent = 0, healing = 0, rerolls = 0, first = 0, restricted = 0;
    std::size_t completed_events = 0, invalid = 0; std::uint64_t actions = 0;
    for (const auto& r : results) {
        total += r.completions; squares += static_cast<double>(r.completions) * r.completions;
        spent += r.mushrooms; healing += r.recovery_mushrooms; rerolls += r.reroll_mushrooms; actions += r.actions;
        invalid += r.action_limit ? 1 : 0;
        if (r.first_completion_hours >= 0) { ++completed_events; first += r.first_completion_hours; restricted += r.first_completion_hours; }
        else restricted += options.limits.deadline_hours;
    }
    const auto n = static_cast<double>(results.size());
    const double mean = total / n, se = n > 1 ? std::sqrt(std::max(0.0, (squares - n * mean * mean) / (n - 1)) / n) : 0;
    std::ostringstream out; out << std::setprecision(12);
    out << "{\"schema\":1,\"engine\":" << json_string(engine_version) << ",\"evidence\":\"synthetic\",\"profile\":" << json_string(profile.id)
        << ",\"profile_fingerprint\":" << json_string(profile.fingerprint) << ",\"policy\":" << json_string(label)
        << ",\"events\":" << results.size() << ",\"master_seed\":" << options.seed << ",\"budget_per_event\":" << options.limits.budget
        << ",\"deadline_hours\":" << options.limits.deadline_hours << ",\"restart_health\":" << json_string(options.limits.restart_health)
        << ",\"initial_run_number\":" << (options.start ? options.start->state.run_number : options.limits.run_number)
        << ",\"start_fingerprint\":" << (options.start ? json_string(options.start->fingerprint()) : "null")
        << ",\"damage_distribution\":" << json_string(profile.damage_distribution)
        << ",\"valid\":" << (invalid == 0 ? "true" : "false") << ",\"action_limit_events\":" << invalid
        << ",\"mean_completions\":" << mean << ",\"mean_completions_mc_se\":";
    if (n > 1) out << se; else out << "null";
    out << ",\"mean_mushrooms\":" << spent / n << ",\"mean_recovery_mushrooms\":" << healing / n
        << ",\"mean_reroll_mushrooms\":" << rerolls / n << ",\"mushrooms_per_completion\":";
    if (total > 0) out << spent / total; else out << "null";
    out << ",\"probability_at_least_one_completion\":" << static_cast<double>(completed_events) / n
        << ",\"conditional_first_completion_hours\":";
    if (completed_events) out << first / static_cast<double>(completed_events); else out << "null";
    out << ",\"restricted_first_completion_hours\":" << restricted / n << ",\"actions\":" << actions
        << ",\"uncertainty\":\"Monte Carlo error only; model uncertainty is not included\"}";
    return out.str();
}
namespace {
template<class Add> void features(const Game& game, Action a, Add add) {
    const auto& s = game.state(); const auto& l = game.limits();
    const std::array<double, 12> context{1, s.hp, s.hp < .3 ? 1.0 : 0.0, std::min(s.keys, 20) / 20.0,
        (s.room - 1) / 100.0, (l.deadline_hours - s.elapsed_hours) / l.deadline_hours,
        l.budget ? static_cast<double>(l.budget - s.mushrooms) / l.budget : 0,
        s.effect(EffectKind::one_hit) ? 1.0 : 0.0, s.effect(EffectKind::poison) ? 1.0 : 0.0,
        s.effect(EffectKind::recovery) ? 1.0 : 0.0, s.room % 25 == 0 ? 1.0 : 0.0,
        a.kind == ActionKind::wait ? a.value / game.profile().recovery_hours : s.shop_rerolls / 20.0};
    for (std::size_t j = 0; j < context.size(); ++j) add(ix(a.kind) * 12 + j, context[j]);
    constexpr std::size_t door_offset = 15 * 12, encounter_offset = door_offset + ix(Door::count) * 6;
    constexpr std::size_t gem_offset = encounter_offset + ix(Encounter::count) * 2;
    constexpr std::size_t offer_offset = gem_offset + ix(Gem::count);
    if (a.kind == ActionKind::choose_door) {
        const auto& d = s.doors[static_cast<std::size_t>(a.index)]; const auto base = door_offset + ix(d.kind) * 6;
        for (std::size_t j = 0; j < 5; ++j) add(base + j, context[j]);
        add(base + 5, d.trap && !s.effect(EffectKind::disarm) ? 1 : 0);
    }
    if (s.phase == Phase::encounter) {
        add(encounter_offset + ix(s.encounter) * 2, a.kind == ActionKind::interact || a.kind == ActionKind::fight || a.kind == ActionKind::rps ? 1 : 0);
        add(encounter_offset + ix(s.encounter) * 2 + 1, a.kind == ActionKind::skip || a.kind == ActionKind::flee ? 1 : 0);
    }
    if (a.kind == ActionKind::choose_gem) add(gem_offset + ix(s.gem_offers[static_cast<std::size_t>(a.index)]), 1);
    if (a.kind == ActionKind::buy) {
        const auto& o = s.offers[static_cast<std::size_t>(a.index)]; const auto base = offer_offset + ix(o.effect.kind) * 4;
        add(base, 1); add(base + 1, o.effect.remaining / 10.0); add(base + 2, o.effect.magnitude * (1 - s.hp));
        add(base + 3, o.keys / 6.0);
    }
}
}
Action LearnedPolicy::choose(const Game& game) const {
    const auto actions = legal_actions(game);
    if (actions.empty()) throw std::logic_error("No action available to learned policy");
    Action best = actions.front(); double best_score = -1e300;
    for (auto a : actions) {
        double score = 0; features(game, a, [&](std::size_t i, double value) { score += weights.at(i) * value; });
        if (score > best_score) { best_score = score; best = a; }
    }
    return best;
}
void LearnedPolicy::save(const std::string& path) const {
    std::ofstream out(path); if (!out) throw std::runtime_error("Cannot write learned policy");
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out << "sfld-linear-v1 " << engine_version << ' ' << dimensions << '\n' << std::setprecision(17);
    for (double w : weights) out << w << '\n';
    out.close();
}
LearnedPolicy LearnedPolicy::load(const std::string& path) {
    std::ifstream in(path); std::string tag, version; std::size_t count = 0;
    if (!(in >> tag >> version >> count) || tag != "sfld-linear-v1" || version != engine_version || count != dimensions)
        throw std::invalid_argument("Invalid/incompatible learned policy header");
    LearnedPolicy policy;
    for (auto& w : policy.weights) if (!(in >> w) || !std::isfinite(w) || std::abs(w) > 1e6)
        throw std::invalid_argument("Invalid learned policy coefficient");
    std::string extra; if (in >> extra) throw std::invalid_argument("Unexpected data after policy");
    return policy;
}
LearnedPolicy train_policy(const Profile& profile, const BatchOptions& options, TrainingOptions config, std::ostream& progress) {
    if (config.generations < 1 || config.generations > 10000 || config.population < 4 || config.population > 1024 ||
        config.episodes < 1 || config.episodes > 100000) throw std::invalid_argument("Invalid training size");
    std::mt19937_64 random(options.seed); std::normal_distribution<double> normal;
    LearnedPolicy mean, best; std::array<double, LearnedPolicy::dimensions> deviation; deviation.fill(1);
    for (unsigned generation = 0; generation < config.generations; ++generation) {
        struct Candidate { LearnedPolicy policy; long long completions = 0, progress = 0, spent = 0; };
        std::vector<Candidate> candidates(config.population);
        auto training = options; training.runs = config.episodes;
        // A new training stream each generation; all candidates face the same set.
        training.seed = Random::mix(options.seed ^ UINT64_C(0x747261696e) ^ generation);
        for (unsigned c = 0; c < config.population; ++c) {
            auto& candidate = candidates[c];
            candidate.policy = c == 0 && generation > 0 ? best : mean;
            if (c != 0 || generation == 0)
                for (std::size_t w = 0; w < mean.weights.size(); ++w) candidate.policy.weights[w] += deviation[w] * normal(random);
            const auto results = event_batch(profile, [&](const Game& g) { return candidate.policy.choose(g); }, training);
            for (const auto& r : results) {
                if (r.action_limit) throw std::runtime_error("Training reached the action cap; increase --max-actions");
                candidate.completions += r.completions; candidate.progress += r.room; candidate.spent += r.mushrooms;
            }
        }
        std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
            if (a.completions != b.completions) return a.completions > b.completions;
            if (a.progress != b.progress) return a.progress > b.progress;
            return a.spent < b.spent;
        });
        best = candidates.front().policy;
        const auto elite = std::max(2U, config.population / 4);
        for (std::size_t w = 0; w < mean.weights.size(); ++w) {
            double average = 0, variance = 0;
            for (unsigned c = 0; c < elite; ++c) average += candidates[c].policy.weights[w] / elite;
            for (unsigned c = 0; c < elite; ++c) variance += std::pow(candidates[c].policy.weights[w] - average, 2) / elite;
            mean.weights[w] = .3 * mean.weights[w] + .7 * average;
            deviation[w] = std::max(.15, .3 * deviation[w] + .7 * std::sqrt(variance));
        }
        progress << "Generation " << generation + 1 << '/' << config.generations << ": training completions/event "
                 << static_cast<double>(candidates.front().completions) / static_cast<double>(config.episodes) << '\n';
    }
    return best;
}
}
