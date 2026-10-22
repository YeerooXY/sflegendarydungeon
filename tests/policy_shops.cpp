#include "sfld/policy.hpp"
#include <iostream>
#include <stdexcept>

using namespace sfld;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Line " + std::to_string(__LINE__) + ": " #x); } while(false)
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
State shop(int room = 35) {
    State s; s.room = room; s.turn = room - 1; s.phase = Phase::shop; s.hp = 1; s.keys = 4;
    s.offers = {{{effect_template(EffectKind::raider, false), 1}, {effect_template(EffectKind::elixir, false), 1}}};
    return s;
}
Policy rolling() {
    Policy p; p.strategy = Strategy::key_stockpile; p.shop_allowance = 5; p.shop_key_reserve = 4;
    p.survival_aware = true; return p;
}
void carryover_and_gate() {
    auto p = profile(); p.shop_blessing_weights.fill(0);
    p.shop_blessing_weights[ix(EffectKind::raider)] = 1; p.shop_blessing_weights[ix(EffectKind::elixir)] = 1;
    auto policy = rolling(); Game first(p, 4, shop(), {100});
    CHECK(first.state().reroll_shop_visits == 1);
    first.step({ActionKind::reroll}); first.step({ActionKind::reroll});
    CHECK(first.state().reroll_bank_used == 2); CHECK(first.state().shop_rerolls == 2);
    auto next = first.state(); next.room = 65; next.turn = 64; next.phase = Phase::doors;
    next.doors = {{{Door::shop}, {Door::wall}}};
    Game second(p, 5, next, {100}); second.step({ActionKind::choose_door, 0});
    CHECK(second.state().reroll_shop_visits == 2); CHECK(second.state().shop_rerolls == 0);
    int rerolls = 0;
    while (policy.choose(second).kind == ActionKind::reroll) { second.step(policy.choose(second)); ++rerolls; CHECK(rerolls <= 8); }
    CHECK(rerolls == 8); CHECK(second.state().reroll_bank_used == 10);
    CHECK(second.state().mushrooms == 10); CHECK(policy.choose(second).kind == ActionKind::skip);
    auto poor = shop(); poor.keys = 3; Game no_keys(p, 6, poor, {100});
    CHECK(policy.choose(no_keys).kind != ActionKind::reroll);
    Game no_budget(p, 6, shop(), {0}); CHECK(policy.choose(no_budget).kind != ActionKind::reroll);
}
void useful_offer_stops_and_opening_is_excluded() {
    auto p = profile(); auto policy = rolling(); auto s = shop(82);
    s.offers[0] = {effect_template(EffectKind::one_hit, true), 4};
    Game good(p, 9, s, {100}); CHECK(policy.choose(good).kind == ActionKind::buy);
    auto opening = shop(5); Game first(p, 3, opening, {100});
    CHECK(first.state().reroll_shop_visits == 0); CHECK(!first.legal({ActionKind::reroll}));
    CHECK(policy.choose(first).kind != ActionKind::reroll);
    auto curse = shop(); curse.phase = Phase::curse_shop;
    curse.offers = {{{effect_template(EffectKind::poison, false), 2}, {effect_template(EffectKind::gold_hangover, false), 2}}};
    Game trader(p, 8, curse, {100}); CHECK(trader.state().reroll_shop_visits == 0);
}
void bank_survives_death_restart_and_progress() {
    auto p = profile(); auto s = shop(); s.phase = Phase::encounter; s.encounter = Encounter::monster;
    s.hp = .001; s.reroll_shop_visits = 2; s.reroll_bank_used = 2;
    Game death(p, 2, s, {100}); death.step({ActionKind::fight});
    CHECK(death.state().phase == Phase::recovery); CHECK(death.state().reroll_shop_visits == 2); CHECK(death.state().reroll_bank_used == 2);
    s.phase = Phase::complete; s.room = 101; s.hp = 1;
    Limits limits; limits.budget = 100; limits.repeat_runs = true;
    Game completed(p, 2, s, limits); completed.step({ActionKind::restart});
    CHECK(completed.state().reroll_shop_visits == 2); CHECK(completed.state().reroll_bank_used == 2);
    auto start = StartState::parse("schema=1\nroom=65\nrun_number=1\nphase=shop\nhp_percent=100\nkeys=4\ngems=rabbit,greed\nreroll_shop_visits=2\nreroll_bank_used=2\nshop_rerolls=0\noffers=raider:weak:5:1,elixir:weak:1:1\n");
    start.validate(p); auto restored = StartState::parse(start.encode()); restored.validate(p);
    Game resumed(p, 3, restored, {100}); CHECK(resumed.state().reroll_shop_visits == 2); CHECK(resumed.state().reroll_bank_used == 2);
    CHECK(resumed.state().mushrooms == 0);
}
void recovery_reserve_and_key_target() {
    auto p = profile(); auto s = shop(10); s.phase = Phase::encounter; s.encounter = Encounter::monster;
    s.hp = .05; s.keys = 0; Policy policy = rolling(); policy.shop_mushroom_reserve = 100;
    Game protected_budget(p, 4, s, {100}); CHECK(policy.choose(protected_budget).kind == ActionKind::flee);
    policy.shop_mushroom_reserve = 0; CHECK(policy.choose(protected_budget).kind == ActionKind::flee);
    s.phase = Phase::recovery; Game recovery(p, 4, s, {100});
    policy.shop_mushroom_reserve = 100; CHECK(policy.choose(recovery).kind == ActionKind::wait);
    policy.shop_mushroom_reserve = 0; CHECK(policy.choose(recovery).kind == ActionKind::heal_full);
    s.phase = Phase::encounter;
    s.hp = 1; s.keys = 8; s.shops_seen[0] = true; Game farming(p, 8, s, {0});
    Policy stockpile; stockpile.strategy = Strategy::key_stockpile;
    CHECK(stockpile.choose(farming).kind == ActionKind::fight); // Default early target is now ten.
    s.keys = 10; Game stocked(p, 8, s, {0}); CHECK(stockpile.choose(stocked).kind == ActionKind::flee);
    stockpile.survival_aware = true; stockpile.early_key_target = 12;
    CHECK(stockpile.choose(stocked).kind == ActionKind::fight);
    auto invalid = rolling(); invalid.reroll_limit = 5; bool threw = false;
    try { invalid.validate(); } catch (const std::invalid_argument&) { threw = true; } CHECK(threw);
}
int main() {
    try { carryover_and_gate(); useful_offer_stops_and_opening_is_excluded(); bank_survives_death_restart_and_progress(); recovery_reserve_and_key_target(); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
    std::cout << "PASS rolling shop allowance, key gate, persistence, progress and recovery reserve\n";
}
