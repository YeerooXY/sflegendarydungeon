#include "sfld/policy.hpp"
#include <iostream>
#include <stdexcept>

using namespace sfld;
namespace {
void require(bool ok, const char* expression, int line) {
    if (!ok) throw std::runtime_error("Line " + std::to_string(line) + ": " + expression);
}
#define CHECK(expression) require(bool(expression), #expression, __LINE__)

Profile fixture() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    p.door_weights.fill(0); p.door_weights[ix(Door::wall)] = 1;
    p.door_pair_weights = std::array<double, 8>{0, 1, 0, 0, 0, 0, 0, 0};
    p.monster_damage.fill({.2, .2, .2}); p.escape_damage = p.monster_damage;
    p.boss_damage.fill({.25, .25, .25});
    p.trap_chance = p.combat_curse_chance = p.key_chance = 0;
    p.golden_weights.fill(0); p.golden_weights[ix(Encounter::crate)] = 1;
    p.blessing_weights.fill(0); p.blessing_weights[ix(EffectKind::elixir)] = 1;
    p.strong_effect = 0;
    p.container_joint_weights = std::array<double, 8>{1, 0, 0, 0, 0, 0, 0, 0};
    p.container_joint_greed_weights = std::array<double, 8>{.8, .2, 0, 0, 0, 0, 0, 0};
    return p;
}
State offer(int room = 26) {
    State s; s.phase = Phase::gems; s.room = room; s.turn = room;
    s.run_number = 2; s.hp = .2; s.keys = 4; s.resources.fill(10);
    s.shops_seen.fill(true);
    return s;
}
Gem selected(const Profile& p, State s, Limits limits = {}, Policy policy = {}) {
    Game game(p, 19, s, limits);
    const auto action = policy.choose_gem_adaptive(game);
    CHECK(action.kind == ActionKind::choose_gem); CHECK(game.legal(action));
    return game.state().gem_offers[static_cast<std::size_t>(action.index)];
}
void escape_saturation_changes_the_offer() {
    auto p = fixture(); p.escape_chance = .8;
    p.door_pair_weights = std::array<double, 8>{0, .75, 0, 0, 0, 0, 0, .25};
    auto s = offer(); s.gem_offers = {Gem::rabbit, Gem::greed, Gem::rusty};
    CHECK(selected(p, s) == Gem::rabbit);
    s.gems[ix(Gem::moonstone)] = true;
    CHECK(selected(p, s) == Gem::greed);
}
void revive_stone_depends_on_budget_and_remaining_rooms() {
    auto p = fixture(); p.escape_chance = .1;
    auto s = offer(); s.gem_offers = {Gem::greasy, Gem::rabbit, Gem::hero};
    CHECK(selected(p, s) == Gem::greasy);
    Limits paid; paid.budget = 10000;
    CHECK(selected(p, s, paid) == Gem::rabbit);
    Policy no_recovery; no_recovery.recovery_budget = 0;
    CHECK(selected(p, s, paid, no_recovery) == Gem::greasy);
    Policy reserved; reserved.shop_mushroom_reserve = paid.budget;
    CHECK(selected(p, s, paid, reserved) == Gem::greasy);
    p.monster_damage.fill({.1, .1, .1}); p.escape_damage = p.monster_damage;
    s.hp = 1;
    CHECK(selected(p, s) == Gem::greasy);
    s.room = 76;
    // Rabbit's damage penalty now applies to failed escapes too, so Greasy
    // remains ahead until less of the run is left to benefit from revivals.
    CHECK(selected(p, s) == Gem::greasy);
    s.room = 86;
    CHECK(selected(p, s) == Gem::rabbit);
}
void explicit_preference_only_applies_when_offered() {
    auto p = fixture(); p.escape_chance = .1;
    auto s = offer(); s.gem_offers = {Gem::greasy, Gem::rabbit, Gem::hero};
    Policy policy; policy.preferred_gem = Gem::hero;
    CHECK(selected(p, s, {}, policy) == Gem::hero);
    policy.preferred_gem = Gem::spying;
    CHECK(selected(p, s, {}, policy) == Gem::greasy);
}
void every_current_and_legacy_stone_is_evaluated() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    for (std::size_t i = 0; i < ix(Gem::count); ++i) {
        auto s = offer(76);
        s.gem_offers = {static_cast<Gem>(i), static_cast<Gem>((i + 1) % ix(Gem::count)), static_cast<Gem>((i + 2) % ix(Gem::count))};
        const auto choice = selected(p, s);
        CHECK(choice == s.gem_offers[0] || choice == s.gem_offers[1] || choice == s.gem_offers[2]);
    }
}
void fixed_enemies_and_bosses_ignore_ordinary_base_damage() {
    auto p = fixture(); p.escape_chance = .1;
    p.door_pair_weights = std::array<double, 8>{0, 0, 0, 0, 0, 0, 0, 1};
    p.blessing_weights.fill(0); p.blessing_weights[ix(EffectKind::raider)] = 1;
    p.curse_weights.fill(0); p.curse_weights[ix(EffectKind::gold_hangover) - ix(EffectKind::broken_armor)] = 1;
    auto s = offer(76); s.hp = 1;
    s.gem_offers = {Gem::greasy, Gem::rabbit, Gem::hero};
    for (auto e : {Encounter::shakes, Encounter::valaraukar, Encounter::pig}) {
        p.golden_weights.fill(0); p.golden_weights[ix(e)] = 1;
        p.monster_damage.fill({.001, .001, .001}); p.escape_damage = p.monster_damage;
        const auto low = selected(p, s);
        if (e == Encounter::shakes) CHECK(low == Gem::greasy);
        p.monster_damage.fill({.95, .95, .95}); p.escape_damage = p.monster_damage;
        CHECK(selected(p, s) == low);
        s.blessings.give({EffectKind::one_hit, 1, 100, Clock::room});
        const auto covered = selected(p, s);
        p.monster_damage.fill({.001, .001, .001}); p.escape_damage = p.monster_damage;
        CHECK(selected(p, s) == covered);
        s.blessings.clear();
    }
    p.golden_weights.fill(0); p.golden_weights[ix(Encounter::boss)] = 1;
    s.gem_offers = {Gem::hero, Gem::rabbit, Gem::devil};
    CHECK(selected(p, s) == Gem::hero); // Equal scores: ordinary combat stones cannot affect bosses.
    s.blessings.give({EffectKind::one_hit, 1, 100, Clock::room});
    CHECK(selected(p, s) == Gem::hero);
}
void traveler_barrels_subtract_half_and_override_gambler() {
    auto p = fixture(); p.time_traveler_elixir_full_heal = true;
    p.time_traveler_barrel_curse_multiplier = 0; // Explicit historical scenario.
    p.door_pair_weights = std::array<double, 8>{0, 0, 0, 0, 0, 0, 0, 1};
    p.golden_weights.fill(0); p.golden_weights[ix(Encounter::crate)] = 1; p.golden_weights[ix(Encounter::barrel)] = 1;
    p.curse_weights.fill(0); p.curse_weights[ix(EffectKind::gold_hangover) - ix(EffectKind::broken_armor)] = 1;
    p.container_joint_weights = std::array<double, 8>{.8, .2, 0, 0, 0, 0, 0, 0};
    p.container_joint_greed_weights = std::array<double, 8>{.75, .25, 0, 0, 0, 0, 0, 0};
    auto s = offer(76); s.gem_offers = {Gem::time_traveler, Gem::greed, Gem::hero};
    p.barrel_blessing = 1;
    CHECK(selected(p, s) == Gem::time_traveler); // Traveller still has 50% blessing barrels.
    p.barrel_blessing = .5;
    CHECK(selected(p, s) == Gem::greed);
    s.gems[ix(Gem::time_traveler)] = true; s.gem_offers = {Gem::gambler, Gem::greed, Gem::hero};
    p.barrel_blessing = .9;
    CHECK(selected(p, s) == Gem::greed); // Gambler cannot cancel Traveller's override.
}
void selection_does_not_read_or_advance_random_draws() {
    auto p = fixture(); p.escape_chance = .4;
    auto s = offer(); s.gem_offers = {Gem::rabbit, Gem::greed, Gem::hero};
    Game inspected(p, 774, s), untouched(p, 774, s), other_seed(p, 992, s);
    Policy policy; policy.adaptive_gems = true;
    const auto before = state_digest(inspected.state());
    const auto first = policy.choose_gem_adaptive(inspected);
    CHECK(policy.choose_gem_adaptive(other_seed).index == first.index);
    for (int i = 0; i < 30; ++i) CHECK(policy.choose_gem_adaptive(inspected).index == first.index);
    CHECK(state_digest(inspected.state()) == before);
    inspected.step(first); untouched.step(first);
    for (int i = 0; i < 40 && !inspected.finished(); ++i) {
        CHECK(state_digest(inspected.state()) == state_digest(untouched.state()));
        const auto a = policy.choose(inspected);
        inspected.step(a); untouched.step(a);
    }
    CHECK(state_digest(inspected.state()) == state_digest(untouched.state()));
}
void wrong_phase_is_rejected() {
    auto p = fixture(); auto s = offer(); s.phase = Phase::encounter;
    Game game(p, 1, s); Policy policy;
    bool rejected = false;
    try { policy.choose_gem_adaptive(game); } catch (const std::invalid_argument&) { rejected = true; }
    CHECK(rejected);
}
}
int main() {
    try {
        escape_saturation_changes_the_offer();
        revive_stone_depends_on_budget_and_remaining_rooms();
        explicit_preference_only_applies_when_offered();
        every_current_and_legacy_stone_is_evaluated();
        fixed_enemies_and_bosses_ignore_ordinary_base_damage();
        traveler_barrels_subtract_half_and_override_gambler();
        selection_does_not_read_or_advance_random_draws();
        wrong_phase_is_rejected();
        std::cout << "8 adaptive stone policy tests passed\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n'; return 1;
    }
}
