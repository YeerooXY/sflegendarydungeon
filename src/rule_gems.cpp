#include "sfld/rule_gems.hpp"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>

namespace sfld {
namespace {
struct Adjustment {
    std::string_view key;
    double GemRules::*member;
};
constexpr std::array<Adjustment, 9> adjustments{{
    {"escape_pair_bonus", &GemRules::escape_pair_bonus},
    {"greasy_traveler_bonus", &GemRules::greasy_traveler_bonus},
    {"gambler_traveler_adjustment", &GemRules::gambler_traveler_adjustment},
    {"greasy_with_perfect_escape_adjustment", &GemRules::greasy_with_perfect_escape_adjustment},
    {"greasy_second_pick_adjustment", &GemRules::greasy_second_pick_adjustment},
    {"greasy_third_pick_adjustment", &GemRules::greasy_third_pick_adjustment},
    {"spying_pendant_adjustment", &GemRules::spying_pendant_adjustment},
    {"gambler_moonstone_adjustment", &GemRules::gambler_moonstone_adjustment},
    {"pearl_with_perfect_escape_adjustment", &GemRules::pearl_with_perfect_escape_adjustment}
}};
void check_point(double value) {
    if (!std::isfinite(value) || std::abs(value) > 1000000)
        throw std::invalid_argument("Stone rule points must be finite and between -1000000 and 1000000");
}
bool completes_pair(const State& state, Gem offered, Gem first, Gem second) {
    return (offered == first && state.has(second)) || (offered == second && state.has(first));
}
double score_unchecked(const Game& game, const GemRules& rules, Gem gem) {
    const auto& state = game.state();
    double score = rules.scores[ix(gem)];
    if (completes_pair(state, gem, Gem::rabbit, Gem::moonstone)) score += rules.escape_pair_bonus;
    if (completes_pair(state, gem, Gem::greasy, Gem::time_traveler)) score += rules.greasy_traveler_bonus;
    if (completes_pair(state, gem, Gem::gambler, Gem::time_traveler)) score += rules.gambler_traveler_adjustment;
    if (completes_pair(state, gem, Gem::spying, Gem::pendant)) score += rules.spying_pendant_adjustment;
    if (completes_pair(state, gem, Gem::gambler, Gem::moonstone)) score += rules.gambler_moonstone_adjustment;
    if (gem == Gem::greasy || gem == Gem::pearl) {
        const double permanent_escape = game.profile().escape_chance + (state.has(Gem::rabbit) ? .4 : 0) +
            (state.has(Gem::moonstone) ? .2 : 0) - (state.has(Gem::bull) ? .3 : 0);
        if (permanent_escape >= 1 - 1e-12) score += gem == Gem::greasy ?
            rules.greasy_with_perfect_escape_adjustment : rules.pearl_with_perfect_escape_adjustment;
    }
    if (gem == Gem::greasy) {
        const int pick = std::clamp((state.room - 1) / 25, 1, 3);
        if (pick == 2) score += rules.greasy_second_pick_adjustment;
        if (pick == 3) score += rules.greasy_third_pick_adjustment;
    }
    return score;
}
}

GemRules::GemRules() {
    // Reproduce the existing handwritten ranking exactly. Search may change
    // these points; the initial chart is not a claim of optimality.
    constexpr std::array priority{Gem::rabbit, Gem::greasy, Gem::moonstone, Gem::pendant, Gem::spying,
        Gem::gambler, Gem::greed, Gem::hero, Gem::time_traveler, Gem::thirsty, Gem::pearl, Gem::blood,
        Gem::explorer, Gem::misadventurer, Gem::devil, Gem::deceit, Gem::lodestone, Gem::bull,
        Gem::hick, Gem::old_sacrifice, Gem::kidney, Gem::masochist, Gem::rusty};
    static_assert(priority.size() == ix(Gem::count));
    for (std::size_t i = 0; i < priority.size(); ++i) scores[ix(priority[i])] = static_cast<double>(priority.size() - i);
}
void GemRules::validate() const {
    for (double value : scores) check_point(value);
    for (const auto& adjustment : adjustments) check_point(this->*adjustment.member);
}
double score_rule_gem(const Game& game, const GemRules& rules, Gem gem) {
    rules.validate();
    if (ix(gem) >= ix(Gem::count)) throw std::invalid_argument("Invalid stone for rule scoring");
    return score_unchecked(game, rules, gem);
}
Action choose_rule_gem(const Game& game, const GemRules& rules) {
    rules.validate();
    if (game.state().phase != Phase::gems) throw std::invalid_argument("Stone rules need a gem offer");
    int best = -1;
    double best_score = -std::numeric_limits<double>::infinity();
    for (int i = 0; i < 3; ++i) {
        const auto action = Action{ActionKind::choose_gem, i};
        const auto gem = game.state().gem_offers[static_cast<std::size_t>(i)];
        if (!game.legal(action) || ix(gem) >= ix(Gem::count) || game.state().has(gem)) continue;
        const double score = score_unchecked(game, rules, gem);
        if (score > best_score) { best = i; best_score = score; }
    }
    if (best < 0) throw std::logic_error("No legal unowned stone offer");
    return {ActionKind::choose_gem, best}; // Equal scores retain visible offer order.
}
bool parse_rule_gem_setting(GemRules& rules, std::string_view key, double value) {
    if (!key.starts_with("gem.")) return false;
    key.remove_prefix(4);
    for (std::size_t i = 0; i < ix(Gem::count); ++i) if (name(static_cast<Gem>(i)) == key) {
        check_point(value); rules.scores[i] = value; return true;
    }
    for (const auto& adjustment : adjustments) if (adjustment.key == key) {
        check_point(value); rules.*adjustment.member = value; return true;
    }
    return false;
}
std::string serialize_rule_gems(const GemRules& rules) {
    rules.validate();
    std::ostringstream out; out.imbue(std::locale::classic());
    out << std::setprecision(std::numeric_limits<double>::max_digits10);
    for (std::size_t i = 0; i < ix(Gem::count); ++i)
        out << "gem." << name(static_cast<Gem>(i)) << '=' << rules.scores[i] << '\n';
    for (const auto& adjustment : adjustments)
        out << "gem." << adjustment.key << '=' << rules.*adjustment.member << '\n';
    return out.str();
}
}
