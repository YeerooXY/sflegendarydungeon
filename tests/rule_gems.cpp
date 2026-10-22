#include "sfld/policy.hpp"
#include "sfld/rule_gems.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>

using namespace sfld;
namespace {
void require(bool ok, const char* expression, int line) {
    if (!ok) throw std::runtime_error("Line " + std::to_string(line) + ": " + expression);
}
#define CHECK(expression) require(bool(expression), #expression, __LINE__)
template<class F> void rejects(F&& operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    CHECK(rejected);
}
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
State offer(int room = 26) {
    State s; s.room = room; s.turn = room; s.phase = Phase::gems; s.run_number = 2;
    s.hp = .75; s.keys = 5; s.resources.fill(10);
    s.gem_offers = {Gem::hero, Gem::rabbit, Gem::greasy};
    return s;
}
Gem selected(const Profile& p, const State& s, const GemRules& rules) {
    Game game(p, 811, s);
    const auto action = choose_rule_gem(game, rules);
    CHECK(action.kind == ActionKind::choose_gem); CHECK(game.legal(action));
    return game.state().gem_offers[static_cast<std::size_t>(action.index)];
}
void defaults_match_the_existing_ranking() {
    auto p = profile(); GemRules rules; Policy original;
    for (std::size_t i = 0; i < ix(Gem::count); ++i) {
        auto s = offer();
        s.gem_offers = {static_cast<Gem>(i), static_cast<Gem>((i + 4) % ix(Gem::count)), static_cast<Gem>((i + 11) % ix(Gem::count))};
        Game game(p, 77, s);
        CHECK(choose_rule_gem(game, rules).index == original.choose(game).index);
    }
}
void pair_rules_apply_in_either_order_and_can_change_the_choice() {
    auto p = profile(); GemRules rules; rules.scores.fill(0);
    auto s = offer(); s.gem_offers = {Gem::hero, Gem::rabbit, Gem::greed};
    CHECK(selected(p, s, rules) == Gem::hero);
    rules.escape_pair_bonus = 2;
    s.gems[ix(Gem::moonstone)] = true;
    CHECK(selected(p, s, rules) == Gem::rabbit);
    s.gems.fill(false); s.gems[ix(Gem::rabbit)] = true; s.gem_offers[1] = Gem::moonstone;
    CHECK(selected(p, s, rules) == Gem::moonstone);

    rules = GemRules{}; rules.scores.fill(0); rules.scores[ix(Gem::hero)] = 1;
    rules.greasy_traveler_bonus = 2;
    s.gems.fill(false); s.gems[ix(Gem::greasy)] = true; s.gem_offers[1] = Gem::time_traveler;
    CHECK(selected(p, s, rules) == Gem::time_traveler);
    s.gems.fill(false); s.gems[ix(Gem::time_traveler)] = true; s.gem_offers[1] = Gem::greasy;
    CHECK(selected(p, s, rules) == Gem::greasy);
}
void interaction_penalties_preserve_the_plain_chart_without_the_pair() {
    auto p = profile(); GemRules rules; rules.scores.fill(0); rules.scores[ix(Gem::hero)] = 1;
    rules.scores[ix(Gem::gambler)] = 2; rules.scores[ix(Gem::spying)] = 2;
    rules.gambler_traveler_adjustment = -3;
    rules.gambler_moonstone_adjustment = -3;
    rules.spying_pendant_adjustment = -3;
    auto s = offer(); s.gem_offers = {Gem::hero, Gem::gambler, Gem::greed};
    CHECK(selected(p, s, rules) == Gem::gambler);
    s.gems[ix(Gem::time_traveler)] = true;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.gems.fill(false); s.gems[ix(Gem::moonstone)] = true;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.gems.fill(false); s.gems[ix(Gem::pendant)] = true; s.gem_offers[1] = Gem::spying;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.gems.fill(false);
    CHECK(selected(p, s, rules) == Gem::spying);
}
void greasy_conditions_use_permanent_escape_and_pick_number() {
    auto p = profile(); p.escape_chance = .4;
    GemRules rules; rules.scores.fill(0); rules.scores[ix(Gem::greasy)] = 2; rules.scores[ix(Gem::hero)] = 1;
    rules.greasy_with_perfect_escape_adjustment = -3;
    rules.greasy_second_pick_adjustment = -2;
    rules.greasy_third_pick_adjustment = -1.5;
    auto s = offer();
    CHECK(selected(p, s, rules) == Gem::greasy);
    s.gems[ix(Gem::rabbit)] = s.gems[ix(Gem::moonstone)] = true;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.gems[ix(Gem::bull)] = true;
    CHECK(selected(p, s, rules) == Gem::greasy);
    s.gems.fill(false); s.blessings.give({EffectKind::escape, .8, 5, Clock::room});
    CHECK(selected(p, s, rules) == Gem::greasy); // Temporary escape is not permanent protection.
    s.room = 51;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.room = 76;
    CHECK(selected(p, s, rules) == Gem::hero);

    rules.scores[ix(Gem::pearl)] = 2;
    rules.pearl_with_perfect_escape_adjustment = -3;
    s.gem_offers = {Gem::hero, Gem::pearl, Gem::greed}; s.gems.fill(false);
    CHECK(selected(p, s, rules) == Gem::pearl); // The temporary escape effect above does not trigger this rule.
    s.gems[ix(Gem::rabbit)] = s.gems[ix(Gem::moonstone)] = true;
    CHECK(selected(p, s, rules) == Gem::hero);
    s.gems[ix(Gem::bull)] = true;
    CHECK(selected(p, s, rules) == Gem::pearl);
}
void selection_respects_offers_owned_stones_and_phase() {
    auto p = profile(); GemRules rules;
    auto s = offer(); s.gem_offers = {Gem::hero, Gem::blood, Gem::hick};
    CHECK(selected(p, s, rules) == Gem::hero); // Highest chart entries are not offered.
    s.gems[ix(Gem::hero)] = true;
    CHECK(selected(p, s, rules) == Gem::blood);
    rules.scores.fill(0); s.gems.fill(false);
    CHECK(selected(p, s, rules) == Gem::hero); // Stable leftmost tie.
    s.phase = Phase::encounter;
    Game wrong(p, 1, s); rejects([&] { choose_rule_gem(wrong, rules); });
    s.phase = Phase::gems;
    for (auto gem : s.gem_offers) s.gems[ix(gem)] = true;
    Game owned(p, 1, s); rejects([&] { choose_rule_gem(owned, rules); });
}
void settings_round_trip_and_invalid_values_do_not_mutate() {
    GemRules rules;
    CHECK(parse_rule_gem_setting(rules, "gem.rabbit", -2.125));
    CHECK(parse_rule_gem_setting(rules, "gem.escape_pair_bonus", 7.375));
    CHECK(parse_rule_gem_setting(rules, "gem.greasy_traveler_bonus", -.125));
    CHECK(parse_rule_gem_setting(rules, "gem.gambler_traveler_adjustment", -1));
    CHECK(parse_rule_gem_setting(rules, "gem.greasy_with_perfect_escape_adjustment", -2));
    CHECK(parse_rule_gem_setting(rules, "gem.greasy_second_pick_adjustment", -.5));
    CHECK(parse_rule_gem_setting(rules, "gem.greasy_third_pick_adjustment", -1.5));
    CHECK(parse_rule_gem_setting(rules, "gem.spying_pendant_adjustment", -4));
    CHECK(parse_rule_gem_setting(rules, "gem.gambler_moonstone_adjustment", -3));
    CHECK(parse_rule_gem_setting(rules, "gem.pearl_with_perfect_escape_adjustment", -5));
    const auto text = serialize_rule_gems(rules);
    GemRules restored;
    std::istringstream input(text);
    for (std::string line; std::getline(input, line);) {
        const auto split = line.find('='); CHECK(split != std::string::npos);
        CHECK(parse_rule_gem_setting(restored, line.substr(0, split), std::stod(line.substr(split + 1))));
    }
    CHECK(serialize_rule_gems(restored) == text);
    CHECK(!parse_rule_gem_setting(rules, "room.target", 10));
    CHECK(!parse_rule_gem_setting(rules, "gem.unknown", 10));
    rejects([&] { parse_rule_gem_setting(rules, "gem.rabbit", std::numeric_limits<double>::quiet_NaN()); });
    rejects([&] { parse_rule_gem_setting(rules, "gem.escape_pair_bonus", 1000001); });
    CHECK(serialize_rule_gems(rules) == text);
    rules.scores[ix(Gem::rabbit)] = std::numeric_limits<double>::infinity();
    rejects([&] { rules.validate(); });
}
void decisions_are_seed_independent_and_do_not_advance_rng() {
    auto p = profile(); auto s = offer(); GemRules rules;
    Game inspected(p, 44, s), untouched(p, 44, s), other_seed(p, 999, s);
    const auto before = state_digest(inspected.state());
    const auto action = choose_rule_gem(inspected, rules);
    CHECK(choose_rule_gem(other_seed, rules).index == action.index);
    for (int i = 0; i < 20; ++i) CHECK(choose_rule_gem(inspected, rules).index == action.index);
    CHECK(state_digest(inspected.state()) == before);
    inspected.step(action); untouched.step(action);
    Policy next;
    for (int i = 0; i < 30 && !inspected.finished(); ++i) {
        CHECK(state_digest(inspected.state()) == state_digest(untouched.state()));
        const auto a = next.choose(inspected); inspected.step(a); untouched.step(a);
    }
    CHECK(state_digest(inspected.state()) == state_digest(untouched.state()));
}
}
int main() {
    try {
        defaults_match_the_existing_ranking();
        pair_rules_apply_in_either_order_and_can_change_the_choice();
        interaction_penalties_preserve_the_plain_chart_without_the_pair();
        greasy_conditions_use_permanent_escape_and_pick_number();
        selection_respects_offers_owned_stones_and_phase();
        settings_round_trip_and_invalid_values_do_not_mutate();
        decisions_are_seed_independent_and_do_not_advance_rng();
        std::cout << "7 human stone rule tests passed\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n'; return 1;
    }
}
