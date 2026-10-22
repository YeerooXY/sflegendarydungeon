// Offline search over readable rules. Training, selection and final evaluation
// use disjoint event seeds; policy selection never accesses future randomness.
#include "sfld/experiment.hpp"
#include "sfld/rule_policy.hpp"
#include "sfld/rule_gems.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <optional>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>

namespace {
using namespace sfld;
struct Options {
    std::string mode = "evaluate", profile = "profiles/synthetic.profile";
    std::string output = "out/rule-evaluation.json", rules, save_rules, event_completions;
    std::string scope = "all", controller = "rules", baseline = "both", gem_search = "both";
    std::string selection = "simple";
    Gem anchor_gem = Gem::count;
    std::vector<Gem> avoid_gems;
    std::optional<double> fixed_revive_hp;
    std::size_t unique_candidates = 0;
    std::vector<int> budgets{0};
    std::size_t runs = 10000, training_events = 128, validation_events = 1024;
    unsigned generations = 8, population = 24, finalists = 8, threads = 3;
    std::uint64_t seed = 42;
    double hours = 168, login_hours = 0;
    bool login_specified = false;
    std::vector<std::size_t> eligible_gems;
};

template<class T> T number(const std::string& s) {
    T v{};
    const auto [end, error] = std::from_chars(s.data(), s.data() + s.size(), v);
    if (error != std::errc{} || end != s.data() + s.size()) throw std::invalid_argument("Invalid number: " + s);
    if constexpr (std::is_floating_point_v<T>) if (!std::isfinite(v)) throw std::invalid_argument("Nonfinite number");
    return v;
}
void help() {
    std::cout << R"(Readable-rule experiments (offline synthetic model; no global-optimum claim).
  sfld_rule_search --mode search --budget 50 --scope all --controller rules --output out/search.json
  sfld_rule_search --mode search --budgets 0,20,50,100 --scope gems --controller survival --output out/gems.json
  sfld_rule_search --mode evaluate --rules out/winner.rules --budgets 0,50,100,500 --runs 10000 --output out/test.json

  --profile PATH           Default profiles/synthetic.profile
  --mode search|evaluate   Search selects on validation; evaluate is a separate final cohort
  --rules PATH             Starting rules for search, or evaluated rules
  --save-rules PATH        Search winner, default winner.rules beside output
  --budget N / --budgets N,N,...   Shared gross event budgets, default 0
  --deadline-hours H       Default 168
  --login-hours H          Fixed availability grid (0..168), never optimized;
                           defaults to loaded rule value, otherwise zero
  --runs N                 Evaluate event count, default 10000
  --seed N                 Unsigned root seed, default 42
  --threads N              Bounded event workers, 1..4; default 3
  --baseline none|legacy|survival|both   Report controls, default both
  --controller rules|legacy|survival    Non-gem actions, default rules
  --scope all|gems          Search parameters, default all; all requires rules controller
  --gem-search ranks|context|both       Gem mutation kinds, default both
  --anchor-gem TOKEN       Always prefer this stone when legally offered
  --avoid-gems TOKEN,...   Prefer every nonavoided legal offer over these stones
                           Both constraints persist as scores; offer pools stay unchanged
  --fixed-revive-hp N      Fix the rules controller's recovery target (0.2..1)
  --selection mean|simple Highest validation mean, or simplest within one paired SE;
                           default simple preserves the original search procedure
  --unique-candidates N   Search only: evaluate exactly N distinct candidates including
                           the initial one; overrides generations, retains population batches
                           The finalist count also includes one reserved initial-rule slot
  --generations N          Default 8, maximum 1000
  --population N           Default 24, maximum 1000
  --training-events N      Common training events/candidate/budget, default 128
  --validation-events N    Independent selection events/finalist/budget, default 1024
  --finalists N            Training shortlist size, default 8
  --output PATH            JSON report, default out/rule-evaluation.json
  --event-completions PATH Evaluate only: candidate CSV budget,event_index,completions

Legacy/survival continuations use key-stockpile, allowance 5 and minimum 4
keys for paid refreshes; only survival enables survival-aware combat. Gem
rules override their gem choices. Controls retain their original gem choices.
For multiple budgets, fitness averages completions/control mean equally;
the normalizer is survival when selected, otherwise legacy. This affects
selection only; reports retain every budget's raw results. All candidates
share training seeds; finalists share separate validation seeds. Final
evaluate seeds have another namespace. A saved winner must be evaluated
with the same controller. Existing output files are never overwritten.
)";
}
Options parse(int argc, char** argv) {
    Options o;
    const std::set<std::string> accepted{"--mode", "--profile", "--output", "--rules", "--save-rules", "--scope", "--controller", "--baseline", "--gem-search", "--budget", "--budgets", "--runs", "--training-events", "--validation-events", "--generations", "--population", "--finalists", "--threads", "--seed", "--deadline-hours", "--login-hours", "--anchor-gem", "--avoid-gems", "--fixed-revive-hp", "--unique-candidates", "--selection", "--event-completions"};
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        if (key == "--help") { help(); std::exit(0); }
        if (!accepted.contains(key) || i + 1 == argc || args.contains(key)) throw std::invalid_argument("Unknown, duplicate or incomplete option: " + key);
        args.emplace(key, argv[++i]);
    }
    const auto str = [&](const char* key, std::string& value) { if (args.contains(key)) value = args.at(key); };
    str("--mode", o.mode); str("--profile", o.profile); str("--output", o.output); str("--rules", o.rules); str("--save-rules", o.save_rules);
    str("--event-completions", o.event_completions);
    str("--scope", o.scope); str("--controller", o.controller); str("--baseline", o.baseline); str("--gem-search", o.gem_search);
    str("--selection", o.selection);
    if (args.contains("--anchor-gem")) o.anchor_gem = gem_from(args.at("--anchor-gem"));
    if (args.contains("--avoid-gems")) {
        const auto& value = args.at("--avoid-gems");
        if (value.empty() || value.back() == ',') throw std::invalid_argument("Empty --avoid-gems token");
        std::istringstream in(value); std::string item; std::set<Gem> seen_gems;
        while (std::getline(in, item, ',')) {
            const auto gem = gem_from(item);
            if (!seen_gems.insert(gem).second) throw std::invalid_argument("Duplicate --avoid-gems token: " + item);
            if (gem == o.anchor_gem) throw std::invalid_argument("Anchor stone cannot also be avoided");
            o.avoid_gems.push_back(gem);
        }
    }
    const auto size = [&](const char* key, std::size_t& value) { if (args.contains(key)) value = number<std::size_t>(args.at(key)); };
    const auto integer = [&](const char* key, unsigned& value) { if (args.contains(key)) value = number<unsigned>(args.at(key)); };
    size("--runs", o.runs); size("--training-events", o.training_events); size("--validation-events", o.validation_events);
    size("--unique-candidates", o.unique_candidates);
    integer("--generations", o.generations); integer("--population", o.population); integer("--finalists", o.finalists); integer("--threads", o.threads);
    if (args.contains("--seed")) o.seed = number<std::uint64_t>(args.at("--seed"));
    if (args.contains("--deadline-hours")) o.hours = number<double>(args.at("--deadline-hours"));
    if (args.contains("--login-hours")) { o.login_hours = number<double>(args.at("--login-hours")); o.login_specified = true; }
    if (args.contains("--fixed-revive-hp")) o.fixed_revive_hp = number<double>(args.at("--fixed-revive-hp"));
    if (args.contains("--budget") && args.contains("--budgets")) throw std::invalid_argument("Use --budget or --budgets, not both");
    if (args.contains("--budget")) o.budgets = {number<int>(args.at("--budget"))};
    if (args.contains("--budgets")) {
        o.budgets.clear(); std::istringstream in(args.at("--budgets")); std::string item;
        while (std::getline(in, item, ',')) o.budgets.push_back(number<int>(item));
        if (args.at("--budgets").empty() || args.at("--budgets").back() == ',') throw std::invalid_argument("Empty budget");
    }
    if (o.mode != "search" && o.mode != "evaluate") throw std::invalid_argument("Invalid --mode");
    if (o.scope != "all" && o.scope != "gems") throw std::invalid_argument("Invalid --scope");
    if (o.controller != "rules" && o.controller != "legacy" && o.controller != "survival") throw std::invalid_argument("Invalid --controller");
    if (o.mode == "search" && o.scope == "all" && o.controller != "rules") throw std::invalid_argument("--scope all requires --controller rules");
    if (o.baseline != "none" && o.baseline != "legacy" && o.baseline != "survival" && o.baseline != "both") throw std::invalid_argument("Invalid --baseline");
    if (o.gem_search != "ranks" && o.gem_search != "context" && o.gem_search != "both") throw std::invalid_argument("Invalid --gem-search");
    if (o.selection != "mean" && o.selection != "simple") throw std::invalid_argument("Invalid --selection");
    if (args.contains("--unique-candidates") && (o.mode != "search" || o.unique_candidates < 2 || o.unique_candidates > 1000000))
        throw std::invalid_argument("--unique-candidates requires search mode and a count in 2..1000000");
    if (o.fixed_revive_hp && (*o.fixed_revive_hp < .2 || *o.fixed_revive_hp > 1 || o.controller != "rules"))
        throw std::invalid_argument("--fixed-revive-hp requires the rules controller and a fraction in [0.2,1]");
    if (o.runs < 2 || o.runs > 10000000 || o.training_events < 2 || o.training_events > 10000000 || o.validation_events < 2 || o.validation_events > 10000000) throw std::invalid_argument("Event counts must be 2..10000000");
    if (o.threads < 1 || o.threads > 4 || o.generations < 1 || o.generations > 1000 || o.population < 2 || o.population > 1000 || o.finalists < 1 || o.finalists > 1000 || !(o.hours > 0 && o.hours <= 100000)) throw std::invalid_argument("Invalid search size, threads or deadline");
    if (o.login_hours < 0 || o.login_hours > 168) throw std::invalid_argument("--login-hours must be 0..168");
    std::set<int> seen;
    for (const int b : o.budgets) if (b < 0 || b > 1000000 || !seen.insert(b).second) throw std::invalid_argument("Budgets must be distinct integers 0..1000000");
    if (o.budgets.empty() || o.budgets.size() > 32) throw std::invalid_argument("Need 1..32 budgets");
    if (o.save_rules.empty()) o.save_rules = (std::filesystem::path(o.output).parent_path() / "winner.rules").string();
    if (std::filesystem::exists(o.output)) throw std::invalid_argument("Output already exists: " + o.output);
    if (o.mode == "search" && std::filesystem::exists(o.save_rules)) throw std::invalid_argument("Winner file already exists: " + o.save_rules);
    if (std::filesystem::weakly_canonical(o.output) == std::filesystem::weakly_canonical(o.save_rules)) throw std::invalid_argument("Report and rules output paths must differ");
    if (args.contains("--event-completions")) {
        if (o.mode != "evaluate" || o.event_completions.empty()) throw std::invalid_argument("--event-completions requires evaluate mode and a path");
        if (std::filesystem::exists(o.event_completions)) throw std::invalid_argument("Event CSV already exists: " + o.event_completions);
        const auto csv = std::filesystem::weakly_canonical(o.event_completions);
        if (csv == std::filesystem::weakly_canonical(o.output) || csv == std::filesystem::weakly_canonical(o.save_rules) ||
            (!o.rules.empty() && csv == std::filesystem::weakly_canonical(o.rules)))
            throw std::invalid_argument("Event CSV, report and rule paths must differ");
    }
    return o;
}

// Event_batch mixes master XOR event_index. Place its variable index in the
// low 24 bits and split/root identity above it, avoiding nearby-XOR cohorts.
struct Cohort { std::string name; std::uint64_t master; std::size_t count; };
Cohort cohort(std::string name, std::uint64_t root, unsigned domain, std::size_t count) {
    const auto identity = Random::mix(root) & 0xffffffffULL;
    return {std::move(name), (static_cast<std::uint64_t>(domain) << 56) | (identity << 24), count};
}
void check_disjoint(const std::vector<Cohort>& cohorts) {
    // Compare exactly the seeds passed to run_event, not merely master values.
    std::unordered_set<std::uint64_t> seeds;
    std::size_t count = 0; for (const auto& c : cohorts) count += c.count;
    seeds.reserve(count);
    for (const auto& c : cohorts) for (std::size_t i = 0; i < c.count; ++i)
        if (!seeds.insert(Random::mix(c.master ^ static_cast<std::uint64_t>(i))).second)
            throw std::runtime_error("Overlapping event seeds in " + c.name);
}
struct Moments {
    double mean = 0, se = 0;
};
Moments moments(const std::vector<double>& v) {
    if (v.empty()) throw std::runtime_error("Empty score cohort");
    const double n = static_cast<double>(v.size());
    const double mean = std::accumulate(v.begin(), v.end(), 0.0) / n;
    double sum = 0; for (const double x : v) sum += (x - mean) * (x - mean);
    return {mean, v.size() > 1 ? std::sqrt(sum / (n * (n - 1))) : 0};
}
struct Summary {
    Moments completions;
    double mushrooms = 0, healing = 0, rerolls = 0, deaths = 0, room = 0;
    double shop_visits = 0, purchases = 0, strong_one_hit = 0;
    double first_completion_hours = 0;
    std::size_t completed_events = 0, action_limits = 0;
    std::uint64_t gem_decisions = 0, gem_changes_vs_flat = 0, gem_changes_vs_original = 0;
    std::array<std::uint64_t, ix(Gem::count)> gem_picks{};
    std::array<std::uint64_t, ix(Gem::count)> gem_offers{};
};
struct Evaluation {
    std::vector<Summary> budgets;
    std::vector<std::vector<EventResult>> results;
    std::vector<double> event_fitness;
    Moments fitness;
};
Policy continuation(std::string_view kind, int budget, double login_hours) {
    Policy p; p.strategy = Strategy::key_stockpile; p.survival_aware = kind == "survival";
    p.shop_allowance = budget ? 5 : 0; p.shop_key_reserve = budget ? 4 : 0;
    p.login_interval_hours = login_hours;
    p.validate(); return p;
}
Evaluation evaluate(const Profile& profile, const Options& options, const Cohort& c,
                    const RulePolicy* rules, std::string_view controller,
                    const std::vector<double>& normalizers) {
    Evaluation e; e.event_fitness.assign(c.count, 0);
    for (std::size_t b = 0; b < options.budgets.size(); ++b) {
        BatchOptions batch; batch.runs = c.count; batch.threads = options.threads; batch.seed = c.master;
        batch.limits.budget = options.budgets[b]; batch.limits.deadline_hours = options.hours;
        const auto policy = continuation(controller, batch.limits.budget, options.login_hours);
        std::array<std::atomic<std::uint64_t>, ix(Gem::count)> picks{};
        std::array<std::atomic<std::uint64_t>, ix(Gem::count)> offers{};
        std::atomic<std::uint64_t> gem_decisions{0}, changes_flat{0}, changes_original{0};
        const GemRules original;
        GemRules flat;
        if (rules) flat.scores = rules->gem_rules.scores;
        const auto choose = [&](const Game& g) {
            Action a = rules && controller == "rules" ? rules->choose(g)
                : rules && g.state().phase == Phase::gems ? choose_rule_gem(g, rules->gem_rules) : policy.choose(g);
            if (g.state().phase == Phase::gems && a.kind == ActionKind::choose_gem && a.index >= 0 && a.index < 3) {
                for (int i = 0; i < 3; ++i) {
                    const auto gem = g.state().gem_offers[static_cast<std::size_t>(i)];
                    if (g.legal({ActionKind::choose_gem, i}) && ix(gem) < ix(Gem::count) && !g.state().has(gem))
                        offers[ix(gem)].fetch_add(1, std::memory_order_relaxed);
                }
                picks[ix(g.state().gem_offers[static_cast<std::size_t>(a.index)])].fetch_add(1, std::memory_order_relaxed);
                gem_decisions.fetch_add(1, std::memory_order_relaxed);
                if (a.index != choose_rule_gem(g, flat).index) changes_flat.fetch_add(1, std::memory_order_relaxed);
                if (a.index != choose_rule_gem(g, original).index) changes_original.fetch_add(1, std::memory_order_relaxed);
            }
            return a;
        };
        auto results = event_batch(profile, choose, batch);
        Summary s; std::vector<double> values; values.reserve(c.count);
        for (std::size_t i = 0; i < results.size(); ++i) {
            const auto& r = results[i]; values.push_back(r.completions);
            s.mushrooms += r.mushrooms; s.healing += r.recovery_mushrooms; s.rerolls += r.reroll_mushrooms;
            s.deaths += r.deaths; s.room += r.room; s.shop_visits += r.reroll_shop_visits;
            s.purchases += r.shop_purchases; s.strong_one_hit += r.strong_one_hit_purchases;
            if (r.first_completion_hours >= 0) { ++s.completed_events; s.first_completion_hours += r.first_completion_hours; }
            s.action_limits += r.action_limit ? 1 : 0;
            if (r.mushrooms > batch.limits.budget) throw std::runtime_error("Event exceeded its gross budget");
            e.event_fitness[i] += static_cast<double>(r.completions) / normalizers[b] / static_cast<double>(options.budgets.size());
        }
        if (s.action_limits) throw std::runtime_error("Policy reached the action safety limit; refusing a misleading search score");
        const auto n = static_cast<double>(c.count);
        s.completions = moments(values); s.mushrooms /= n; s.healing /= n; s.rerolls /= n;
        s.deaths /= n; s.room /= n; s.shop_visits /= n; s.purchases /= n; s.strong_one_hit /= n;
        if (s.completed_events) s.first_completion_hours /= static_cast<double>(s.completed_events);
        for (std::size_t i = 0; i < picks.size(); ++i) { s.gem_picks[i] = picks[i].load(); s.gem_offers[i] = offers[i].load(); }
        s.gem_decisions = gem_decisions.load(); s.gem_changes_vs_flat = changes_flat.load(); s.gem_changes_vs_original = changes_original.load();
        e.budgets.push_back(s); e.results.push_back(std::move(results));
    }
    e.fitness = moments(e.event_fitness); return e;
}
Moments difference(const Evaluation& a, const Evaluation& b) {
    std::vector<double> d(a.event_fitness.size());
    if (d.size() != b.event_fitness.size()) throw std::runtime_error("Cannot pair different cohorts");
    for (std::size_t i = 0; i < d.size(); ++i) d[i] = a.event_fitness[i] - b.event_fitness[i];
    return moments(d);
}
void rescore(Evaluation& e, const std::vector<double>& scales) {
    std::fill(e.event_fitness.begin(), e.event_fitness.end(), 0);
    for (std::size_t b = 0; b < e.results.size(); ++b) for (std::size_t i = 0; i < e.results[b].size(); ++i)
        e.event_fitness[i] += static_cast<double>(e.results[b][i].completions) / scales[b] / static_cast<double>(scales.size());
    e.fitness = moments(e.event_fitness);
}

using GemField = double GemRules::*;
const std::array<GemField, 9> gem_fields{&GemRules::escape_pair_bonus, &GemRules::greasy_traveler_bonus,
    &GemRules::gambler_traveler_adjustment, &GemRules::greasy_with_perfect_escape_adjustment,
    &GemRules::greasy_second_pick_adjustment, &GemRules::greasy_third_pick_adjustment,
    &GemRules::spying_pendant_adjustment, &GemRules::gambler_moonstone_adjustment,
    &GemRules::pearl_with_perfect_escape_adjustment};

bool fixed_gem(const Options& o, Gem gem) {
    return gem == o.anchor_gem || std::find(o.avoid_gems.begin(), o.avoid_gems.end(), gem) != o.avoid_gems.end();
}
void constrain_rules(RulePolicy& p, const Options& o) {
    if (o.fixed_revive_hp) p.revive_hp = *o.fixed_revive_hp;
    if (o.anchor_gem != Gem::count || !o.avoid_gems.empty()) {
        // Reserve a strict score gap for fixed choices. A common positive scale
        // preserves every comparison between unconstrained stones, including
        // their context adjustments. Typical charts never need this scaling.
        // Sum all adjustment magnitudes: even impossible simultaneous contexts
        // cannot bridge the gap, and reloaded .rules need no special chooser.
        auto& rules = p.gem_rules;
        double base_bound = 0, context_bound = 0;
        for (std::size_t i = 0; i < rules.scores.size(); ++i)
            if (!fixed_gem(o, static_cast<Gem>(i))) base_bound = std::max(base_bound, std::abs(rules.scores[i]));
        for (const auto field : gem_fields) context_bound += std::abs(rules.*field);
        const double bound = base_bound + context_bound;
        if (bound > 100000) {
            // Leave headroom so floating-point rounding cannot cause repeated
            // normalization to manufacture distinct cached candidates.
            const double scale = 50000 / bound;
            for (std::size_t i = 0; i < rules.scores.size(); ++i)
                if (!fixed_gem(o, static_cast<Gem>(i))) rules.scores[i] *= scale;
            for (const auto field : gem_fields) rules.*field *= scale;
        }
        if (o.anchor_gem != Gem::count) rules.scores[ix(o.anchor_gem)] = 1000000;
        for (const auto gem : o.avoid_gems) rules.scores[ix(gem)] = -1000000;
    }
    p.validate();
}
using IntField = int RulePolicy::*;
const std::array<IntField, 7> int_fields{&RulePolicy::door_mode, &RulePolicy::shop_mode,
    &RulePolicy::barrel_mode, &RulePolicy::paid_refill_mode, &RulePolicy::shop_lookahead,
    &RulePolicy::shop_key_reserve, &RulePolicy::shop_allowance};
using DoubleField = double RulePolicy::*;
const std::array<DoubleField, 8> double_fields{&RulePolicy::max_fight_death_risk, &RulePolicy::min_fight_hp,
    &RulePolicy::fight_escape_cutoff, &RulePolicy::trap_hp, &RulePolicy::barrel_hp,
    &RulePolicy::revive_hp, &RulePolicy::shop_reserve_fraction, &RulePolicy::shop_purchase_floor};
std::size_t pick(std::mt19937_64& rng, std::size_t n) { return static_cast<std::size_t>(rng() % n); }
template<std::size_t N> double grid(std::mt19937_64& rng, const std::array<double, N>& values) { return values[pick(rng, N)]; }
void mutate_gems(RulePolicy& p, const Options& o, std::mt19937_64& rng) {
    const bool rank = o.gem_search == "ranks" || (o.gem_search == "both" && pick(rng, 100) < 65);
    if (rank) {
        if (o.eligible_gems.size() < 2) return;
        const auto i = pick(rng, o.eligible_gems.size());
        auto j = pick(rng, o.eligible_gems.size() - 1); if (j >= i) ++j;
        std::swap(p.gem_rules.scores[o.eligible_gems[i]], p.gem_rules.scores[o.eligible_gems[j]]);
    } else {
        const auto field = gem_fields[pick(rng, gem_fields.size())];
        // Zero stays prominent so conditional rules can disappear again.
        p.gem_rules.*field = grid(rng, std::array{-12., -8., -4., -2., 0., 0., 2., 4., 8., 12.});
    }
}
RulePolicy mutate(RulePolicy p, const Options& o, std::mt19937_64& rng, unsigned changes) {
    for (unsigned n = 0; n < changes; ++n) {
        const auto kind = o.scope == "gems" ? 0 : pick(rng, 6);
        if (kind == 0) { mutate_gems(p, o, rng); continue; }
        if (kind == 1 || kind == 2) {
            auto& a = kind == 1 ? p.fight_keys : p.door_reserve;
            const std::array<int, 10> values{0, 1, 2, 4, 6, 8, 10, 12, 16, 20};
            a[pick(rng, a.size())] = values[pick(rng, values.size())];
        } else if (kind == 3) {
            const auto field = int_fields[pick(rng, int_fields.size())];
            if (field == &RulePolicy::shop_lookahead) {
                const std::array<int, 7> values{0, 2, 4, 6, 8, 12, 24}; p.*field = values[pick(rng, values.size())];
            } else if (field == &RulePolicy::shop_key_reserve) {
                const std::array<int, 3> values{4, 6, 8}; p.*field = values[pick(rng, values.size())];
            } else if (field == &RulePolicy::shop_allowance) {
                const std::array<int, 7> values{0, 1, 2, 3, 5, 8, 12}; p.*field = values[pick(rng, values.size())];
            } else if (field == &RulePolicy::paid_refill_mode) {
                p.*field = static_cast<int>(pick(rng, 4));
            } else p.*field = static_cast<int>(pick(rng, 3));
        } else if (kind == 4) {
            const auto field = double_fields[pick(rng, double_fields.size())];
            if (field == &RulePolicy::max_fight_death_risk) p.*field = grid(rng, std::array{0., .02, .05, .1, .2, .5, 1.});
            else if (field == &RulePolicy::revive_hp) p.*field = grid(rng, std::array{.2, .25, .3, .4, .5, .75, 1.});
            else if (field == &RulePolicy::shop_reserve_fraction) p.*field = grid(rng, std::array{0., .05, .1, .2, .3, .5, 1.});
            else if (field == &RulePolicy::shop_purchase_floor) p.*field = grid(rng, std::array{0., 40., 60., 80., 100.});
            else p.*field = grid(rng, std::array{0., .1, .2, .3, .4, .5, .6, .8, 1.});
        } else if (pick(rng, 2)) p.guaranteed_escape_door_preference = !p.guaranteed_escape_door_preference;
        else p.fight_when_cheaper = !p.fight_when_cheaper;
    }
    constrain_rules(p, o); return p;
}
unsigned complexity(const RulePolicy& p, const Options& o) {
    const RulePolicy d; unsigned score = 0;
    for (std::size_t i = 0; i < p.gem_rules.scores.size(); ++i) score += p.gem_rules.scores[i] != d.gem_rules.scores[i] ? 1U : 0U;
    for (const auto f : gem_fields) score += p.gem_rules.*f != 0 ? 2U : 0U;
    if (o.controller == "rules") {
        for (std::size_t i = 0; i < 4; ++i) { score += p.fight_keys[i] != d.fight_keys[i] ? 1U : 0U; score += p.door_reserve[i] != d.door_reserve[i] ? 1U : 0U; }
        for (const auto f : int_fields) score += p.*f != d.*f ? 1U : 0U;
        for (const auto f : double_fields) score += p.*f != d.*f ? 1U : 0U;
        score += p.guaranteed_escape_door_preference != d.guaranteed_escape_door_preference ? 1U : 0U;
        score += p.fight_when_cheaper != d.fight_when_cheaper ? 1U : 0U;
    }
    return score;
}
std::string canonical(RulePolicy p) { p.id = "candidate"; return p.encode(); }

void moment_json(std::ostream& out, Moments m) {
    out << "{\"mean\":" << m.mean << ",\"standard_error\":" << m.se
        << ",\"normal_95_ci\":[" << m.mean - 1.96 * m.se << ',' << m.mean + 1.96 * m.se << "]}";
}
void evaluation_json(std::ostream& out, const Evaluation& e, const Options& o) {
    out << "{\"normalized_fitness\":"; moment_json(out, e.fitness); out << ",\"budgets\":[";
    for (std::size_t b = 0; b < e.budgets.size(); ++b) {
        if (b) out << ','; const auto& s = e.budgets[b];
        out << "{\"budget\":" << o.budgets[b] << ",\"completions\":"; moment_json(out, s.completions);
        out << ",\"mean_mushrooms\":" << s.mushrooms << ",\"mean_recovery_mushrooms\":" << s.healing
            << ",\"mean_reroll_mushrooms\":" << s.rerolls << ",\"mean_deaths\":" << s.deaths
            << ",\"mean_final_room\":" << s.room << ",\"mean_eligible_shop_visits\":" << s.shop_visits
            << ",\"mean_shop_purchases\":" << s.purchases << ",\"mean_strong_one_hit_purchases\":" << s.strong_one_hit
            << ",\"events_with_completion\":" << s.completed_events << ",\"conditional_first_completion_hours\":";
        if (s.completed_events) out << s.first_completion_hours; else out << "null";
        out << ",\"action_limit_events\":" << s.action_limits << ",\"gem_decisions\":" << s.gem_decisions
            << ",\"gem_choices_changed_vs_same_scores_zero_context\":" << s.gem_changes_vs_flat
            << ",\"gem_choices_changed_vs_original_chart\":" << s.gem_changes_vs_original << ",\"gem_picks\":{";
        for (std::size_t i = 0; i < s.gem_picks.size(); ++i) { if (i) out << ','; out << json_string(name(static_cast<Gem>(i))) << ':' << s.gem_picks[i]; }
        out << "},\"gem_offers\":{";
        for (std::size_t i = 0; i < s.gem_offers.size(); ++i) { if (i) out << ','; out << json_string(name(static_cast<Gem>(i))) << ':' << s.gem_offers[i]; }
        out << "}}";
    }
    out << "]}";
}
void paired_json(std::ostream& out, const Evaluation& a, const Evaluation& b, const Options& o) {
    out << "{\"normalized_fitness_difference\":"; moment_json(out, difference(a, b)); out << ",\"budgets\":[";
    for (std::size_t k = 0; k < a.results.size(); ++k) {
        if (k) out << ',';
        std::vector<double> d(a.results[k].size());
        for (std::size_t i = 0; i < d.size(); ++i) d[i] = static_cast<double>(a.results[k][i].completions - b.results[k][i].completions);
        out << "{\"budget\":" << o.budgets[k] << ",\"completion_difference\":"; moment_json(out, moments(d)); out << '}';
    }
    out << "]}";
}
std::vector<std::string> control_names(const Options& o) {
    if (o.baseline == "both") return {"legacy", "survival"};
    if (o.baseline == "none") return {};
    return {o.baseline};
}
using Controls = std::map<std::string, Evaluation>;
Controls controls(const Profile& p, const Options& o, const Cohort& c, const std::vector<double>& scales, bool normalization_required) {
    Controls result;
    for (const auto& kind : control_names(o)) result.emplace(kind, evaluate(p, o, c, nullptr, kind, scales));
    if (normalization_required) {
        const std::string normalizer = o.baseline == "survival" || o.baseline == "both" ? "survival" : "legacy";
        if (!result.contains(normalizer)) result.emplace(normalizer, evaluate(p, o, c, nullptr, normalizer, scales));
    }
    return result;
}
void controls_json(std::ostream& out, const Controls& controls, const Options& o) {
    out << '{'; bool comma = false;
    for (const auto& [name, e] : controls) { if (comma) out << ','; comma = true; out << json_string(name) << ':'; evaluation_json(out, e, o); }
    out << '}';
}
struct Candidate { RulePolicy rules; Evaluation training; unsigned complexity = 0; unsigned generation = 0; };
void release_events(Evaluation& e) { e.results.clear(); e.event_fitness.clear(); }
void candidate_json(std::ostream& out, const Candidate& c, const Options& o) {
    out << "{\"id\":" << json_string(c.rules.id) << ",\"generation\":" << c.generation << ",\"complexity\":" << c.complexity
        << ",\"rules_text\":" << json_string(c.rules.encode()) << ",\"training\":";
    evaluation_json(out, c.training, o); out << '}';
}
void preamble(std::ostream& out, const Options& o, const Profile& p, const std::vector<Cohort>& cohorts, const std::vector<double>& scales) {
    out << std::setprecision(12) << "{\"schema\":\"sfld-rule-search-v1\",\"engine\":" << json_string(engine_version)
        << ",\"mode\":" << json_string(o.mode) << ",\"profile\":{\"path\":" << json_string(o.profile)
        << ",\"id\":" << json_string(p.id) << ",\"fingerprint\":" << json_string(p.fingerprint) << ",\"evidence\":" << json_string(p.evidence)
        << "},\"settings\":{\"controller\":" << json_string(o.controller) << ",\"scope\":" << json_string(o.scope)
        << ",\"gem_search\":" << json_string(o.gem_search) << ",\"baseline\":" << json_string(o.baseline)
        << ",\"root_seed\":" << json_string(std::to_string(o.seed)) << ",\"threads\":" << o.threads << ",\"deadline_hours\":" << o.hours
        << ",\"fixed_login_interval_hours\":" << o.login_hours
        << ",\"runs\":" << o.runs << ",\"generations\":" << o.generations << ",\"population\":" << o.population
        << ",\"unique_candidates\":" << o.unique_candidates << ",\"selection_policy\":" << json_string(o.selection)
        << ",\"finalist_budget\":" << json_string(o.unique_candidates ? "includes_initial_rule" : "shortlist_plus_initial_if_absent")
        << ",\"event_completions_path\":" << json_string(o.event_completions)
        << ",\"training_events\":" << o.training_events << ",\"validation_events\":" << o.validation_events
        << ",\"finalists\":" << o.finalists << ",\"initial_rules_path\":" << json_string(o.rules) << ",\"budgets\":[";
    for (std::size_t i = 0; i < o.budgets.size(); ++i) { if (i) out << ','; out << o.budgets[i]; }
    out << "],\"fitness_normalizers\":[";
    for (std::size_t i = 0; i < scales.size(); ++i) { if (i) out << ','; out << scales[i]; }
    out << "],\"eligible_gem_score_mutations\":[";
    for (std::size_t i = 0; i < o.eligible_gems.size(); ++i) { if (i) out << ','; out << json_string(name(static_cast<Gem>(o.eligible_gems[i]))); }
    out << "],\"gem_constraints\":{\"anchor_gem\":";
    if (o.anchor_gem == Gem::count) out << "null"; else out << json_string(name(o.anchor_gem));
    out << ",\"avoid_gems\":[";
    for (std::size_t i = 0; i < o.avoid_gems.size(); ++i) { if (i) out << ','; out << json_string(name(o.avoid_gems[i])); }
    out << "],\"offer_pools_unchanged\":true,\"controls_constrained\":false,\"implementation\":\"persisted_score_bounds\",\"anchor_points\":1000000,\"avoid_points\":-1000000,\"ordinary_base_plus_context_bound\":100000},\"fixed_revive_hp\":";
    if (o.fixed_revive_hp) out << *o.fixed_revive_hp; else out << "null";
    out << ",\"normalizer_source\":" << json_string(o.budgets.size() > 1 ? (o.baseline == "survival" || o.baseline == "both" ? "survival_control" : "legacy_control") : "single_budget_raw_completions")
        << ",\"continuation_settings\":{\"strategy\":\"key-stockpile\",\"paid_shop_allowance\":5,\"paid_shop_key_reserve\":4,\"survival_aware_only_for_survival\":true},\"selection\":"
        << json_string(o.selection == "mean" ? "maximize validation mean; exact training and validation ties favor earlier candidate index" :
            "maximize validation mean; choose simplest within one paired standard error of best; exact ties favor higher mean")
        << "},\"seed_plan\":{\"index_bits\":24,\"master_formula\":\"domain<<56 | (mix(root_seed)&0xffffffff)<<24\",\"event_formula\":\"mix(master XOR index)\",\"exact_disjoint_event_seed_check\":true,\"cohorts\":[";
    for (std::size_t i = 0; i < cohorts.size(); ++i) {
        if (i) out << ',';
        const auto& c = cohorts[i];
        out << "{\"name\":" << json_string(c.name) << ",\"master_seed\":" << json_string(std::to_string(c.master)) << ",\"events\":" << c.count
            << ",\"first_event_seed\":" << json_string(std::to_string(Random::mix(c.master))) << '}';
    }
    out << "]},\"limitations\":[\"Synthetic model and assumed mechanics; no claim of globally optimal or best live-game strategy.\",\"Validation selects a winner and is not an unbiased final test. Use separate evaluate mode and keep its result out of further tuning.\",\"Gem pick counts are policy-selected frequencies, not causal estimates of stone value.\",\"Reported confidence intervals describe event sampling only, not uncertain mechanics.\"]";
}
std::ofstream open_output(const std::string& path) {
    const auto parent = std::filesystem::path(path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent);
    std::ofstream out(path); if (!out) throw std::runtime_error("Cannot write: " + path);
    out.exceptions(std::ios::failbit | std::ios::badbit); return out;
}
int run(const Options& input) {
    Options o = input;
    const auto p = Profile::load(o.profile);
    auto initial = o.rules.empty() ? RulePolicy{} : RulePolicy::load(o.rules);
    if (o.login_specified) initial.login_interval_hours = o.login_hours;
    else o.login_hours = initial.login_interval_hours;
    initial.validate();
    std::set<std::size_t> eligible;
    for (const int run_number : {1, 2}) for (const auto gem : p.gems_for_run(run_number)) eligible.insert(ix(gem));
    const auto require_eligible = [&](Gem gem) {
        if (!eligible.contains(ix(gem))) throw std::invalid_argument("Constrained stone is absent from both profile pools: " + std::string(name(gem)));
    };
    if (o.anchor_gem != Gem::count) require_eligible(o.anchor_gem);
    for (const auto gem : o.avoid_gems) require_eligible(gem);
    std::erase_if(eligible, [&](std::size_t i) { return fixed_gem(o, static_cast<Gem>(i)); });
    o.eligible_gems.assign(eligible.begin(), eligible.end());
    constrain_rules(initial, o);
    const auto train = cohort("training", o.seed, 0x31, o.training_events);
    const auto validation = cohort("validation_selection", o.seed, 0x52, o.validation_events);
    const auto test = cohort("final_evaluation", o.seed, 0x73, o.runs);
    // Include the reserved final cohort even during search. It is checked but
    // never simulated or scored until an explicit evaluate invocation.
    const std::vector<Cohort> cohorts{train, validation, test}; check_disjoint(cohorts);
    const std::vector<double> ones(o.budgets.size(), 1);
    auto base = controls(p, o, o.mode == "search" ? train : test, ones, o.budgets.size() > 1);
    auto scales = ones;
    if (o.budgets.size() > 1) {
        const auto normalizer = o.baseline == "survival" || o.baseline == "both" ? "survival" : "legacy";
        for (std::size_t b = 0; b < scales.size(); ++b) scales[b] = std::max(.25, base.at(normalizer).budgets[b].completions.mean);
        for (auto& [name, e] : base) { (void)name; rescore(e, scales); }
    }
    if (o.mode == "evaluate") {
        const auto result = evaluate(p, o, test, &initial, o.controller, scales);
        if (!o.event_completions.empty()) {
            auto csv = open_output(o.event_completions);
            csv << "budget,event_index,completions\n";
            for (std::size_t b = 0; b < o.budgets.size(); ++b)
                for (std::size_t i = 0; i < result.results[b].size(); ++i)
                    csv << o.budgets[b] << ',' << i << ',' << result.results[b][i].completions << '\n';
        }
        auto out = open_output(o.output); preamble(out, o, p, cohorts, scales);
        out << ",\"rules_text\":" << json_string(initial.encode()) << ",\"evaluation\":"; evaluation_json(out, result, o);
        out << ",\"controls\":"; controls_json(out, base, o); out << ",\"paired_vs_controls\":{";
        bool comma = false; for (const auto& [name, e] : base) { if (comma) out << ','; comma = true; out << json_string(name) << ':'; paired_json(out, result, e, o); }
        out << "}}\n";
        std::cerr << "Evaluation complete: " << o.output << '\n'; return 0;
    }
    std::vector<Candidate> archive;
    std::unordered_map<std::string, std::size_t> cache;
    const auto add = [&](RulePolicy rules, unsigned generation) {
        constrain_rules(rules, o);
        const auto key = canonical(rules); const auto found = cache.find(key);
        if (found != cache.end()) return found->second;
        const auto id = archive.size(); rules.id = "candidate-" + std::to_string(id);
        auto e = evaluate(p, o, train, &rules, o.controller, scales); release_events(e);
        const auto cost = complexity(rules, o);
        archive.push_back({std::move(rules), std::move(e), cost, generation}); cache.emplace(key, id); return id;
    };
    const auto initial_index = add(initial, 0);
    (void)initial_index;
    std::mt19937_64 rng(Random::mix(o.seed ^ 0xf407a537129bd4e3ULL));
    std::vector<std::vector<std::size_t>> generations;
    std::vector<std::size_t> ranked{0};
    const auto rank = [&] {
        ranked.resize(archive.size()); std::iota(ranked.begin(), ranked.end(), 0);
        std::stable_sort(ranked.begin(), ranked.end(), [&](auto a, auto b) {
            if (archive[a].training.fitness.mean != archive[b].training.fitness.mean) return archive[a].training.fitness.mean > archive[b].training.fitness.mean;
            if (o.selection == "simple" && archive[a].complexity != archive[b].complexity)
                return archive[a].complexity < archive[b].complexity;
            return a < b;
        });
    };
    unsigned duplicate_attempts = 0;
    for (unsigned generation = 0; o.unique_candidates ? archive.size() < o.unique_candidates : generation < o.generations; ++generation) {
        std::vector<std::size_t> ids;
        const auto elite_count = std::min<std::size_t>(ranked.size(), std::max(2U, o.population / 4));
        for (unsigned member = 0; member < o.population && (!o.unique_candidates || archive.size() < o.unique_candidates);) {
            const auto parent = ranked[pick(rng, elite_count)];
            const auto changes = generation == 0 ? 1U + static_cast<unsigned>(pick(rng, 6)) : 1U + static_cast<unsigned>(pick(rng, 3));
            const auto before = archive.size();
            const auto id = add(mutate(archive[parent].rules, o, rng, changes), generation + 1);
            if (o.unique_candidates && archive.size() == before) {
                if (++duplicate_attempts >= 10000)
                    throw std::runtime_error("Cannot reach --unique-candidates: 10000 consecutive duplicate mutations; evaluated " + std::to_string(archive.size()));
                continue;
            }
            duplicate_attempts = 0; ids.push_back(id); ++member;
        }
        rank(); generations.push_back(std::move(ids));
        std::cerr << "Generation " << generation + 1;
        if (!o.unique_candidates) std::cerr << '/' << o.generations;
        std::cerr << ": " << archive.size()
            << " unique rules; best training score " << archive[ranked.front()].training.fitness.mean
            << " (" << archive[ranked.front()].rules.id << ")\n";
    }
    std::vector<std::size_t> finalists;
    for (const auto id : ranked) {
        if (finalists.size() >= o.finalists) break;
        finalists.push_back(id);
    }
    // Exact-budget mode reserves a slot for the starting rule. The original
    // generation-based mode retains its historical extra-initial comparison.
    if (std::find(finalists.begin(), finalists.end(), 0) == finalists.end()) {
        if (o.unique_candidates) finalists.back() = 0;
        else finalists.push_back(0);
    }
    auto validation_controls = controls(p, o, validation, scales, false);
    std::map<std::size_t, Evaluation> validation_results;
    std::size_t best = finalists.front();
    for (const auto id : finalists) {
        std::cerr << "Validate " << archive[id].rules.id << '\n';
        auto e = evaluate(p, o, validation, &archive[id].rules, o.controller, scales);
        validation_results.emplace(id, std::move(e));
        if (validation_results.at(id).fitness.mean > validation_results.at(best).fitness.mean ||
            (o.selection == "mean" && validation_results.at(id).fitness.mean == validation_results.at(best).fitness.mean && id < best)) best = id;
    }
    std::size_t winner = best;
    if (o.selection == "simple") for (const auto id : finalists) {
        const auto gap = difference(validation_results.at(best), validation_results.at(id));
        if (gap.mean > gap.se + 1e-12) continue;
        if (archive[id].complexity < archive[winner].complexity ||
            (archive[id].complexity == archive[winner].complexity && validation_results.at(id).fitness.mean > validation_results.at(winner).fitness.mean)) winner = id;
    }
    const auto parent = std::filesystem::path(o.save_rules).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent);
    archive[winner].rules.save(o.save_rules);
    auto out = open_output(o.output); preamble(out, o, p, cohorts, scales);
    out << ",\"training_controls\":"; controls_json(out, base, o);
    out << ",\"validation_controls\":"; controls_json(out, validation_controls, o);
    out << ",\"candidates\":[";
    for (std::size_t i = 0; i < archive.size(); ++i) { if (i) out << ','; candidate_json(out, archive[i], o); }
    out << "],\"generations\":[";
    for (std::size_t g = 0; g < generations.size(); ++g) {
        if (g) out << ','; out << "{\"generation\":" << g + 1 << ",\"candidate_ids\":[";
        for (std::size_t i = 0; i < generations[g].size(); ++i) { if (i) out << ','; out << json_string(archive[generations[g][i]].rules.id); }
        out << "]}";
    }
    out << "],\"validation_finalists\":[";
    for (std::size_t i = 0; i < finalists.size(); ++i) {
        if (i) out << ','; const auto id = finalists[i];
        out << "{\"id\":" << json_string(archive[id].rules.id) << ",\"evaluation\":"; evaluation_json(out, validation_results.at(id), o); out << '}';
    }
    out << "],\"validation_highest_mean_id\":" << json_string(archive[best].rules.id)
        << ",\"winner_id\":" << json_string(archive[winner].rules.id) << ",\"winner_rules_path\":" << json_string(o.save_rules)
        << ",\"winner_rules_text\":" << json_string(archive[winner].rules.encode()) << ",\"winner_validation\":";
    evaluation_json(out, validation_results.at(winner), o); out << ",\"winner_validation_paired_vs_controls\":{";
    bool comma = false; for (const auto& [name, e] : validation_controls) { if (comma) out << ','; comma = true; out << json_string(name) << ':'; paired_json(out, validation_results.at(winner), e, o); }
    out << "},\"final_evaluation_performed\":false}\n";
    std::cerr << "Selected " << archive[winner].rules.id << "; rules " << o.save_rules << "; report " << o.output << '\n';
    return 0;
}
}
int main(int argc, char** argv) {
    try { return run(parse(argc, argv)); }
    catch (const std::exception& e) { std::cerr << "rule_search: " << e.what() << '\n'; return 1; }
}
