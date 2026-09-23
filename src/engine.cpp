#include "sfld/engine.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>

namespace sfld {
namespace {
std::size_t band(int room) { return static_cast<std::size_t>(std::clamp((room - 1) / 25, 0, 3)); }
int resource_index(Door door) {
    return door >= Door::wood && door <= Door::hourglasses ? static_cast<int>(ix(door) - ix(Door::wood)) : -1;
}
bool trap_eligible(Door door) { return door == Door::monster || door == Door::mystery || door == Door::golden; }
void check_limits(Limits limits) {
    if (limits.budget < 0 || limits.budget > 1000000 || !std::isfinite(limits.deadline_hours) ||
        limits.deadline_hours <= 0 || limits.deadline_hours > 1000000 || limits.max_actions == 0 || limits.run_number < 1)
        throw std::invalid_argument("Invalid run limits");
}
}
Game::Game(const Profile& profile, std::uint64_t seed, Limits limits)
    : profile_(profile), random_(seed), limits_(limits) {
    check_limits(limits);
    state_.run_number = limits.run_number;
    state_.resources.fill(profile.initial_resources);
    generate_doors();
}
Game::Game(const Profile& profile, std::uint64_t seed, State state, Limits limits)
    : profile_(profile), random_(seed), state_(std::move(state)), limits_(limits) {
    check_limits(limits);
    if (state_.room < 1 || state_.room > 101 || !std::isfinite(state_.hp) || state_.hp < 0 || state_.hp > 1 ||
        state_.keys < 0 || state_.mushrooms < 0 || state_.mushrooms > limits.budget ||
        state_.blessings.size < 0 || state_.blessings.size > 3 || state_.curses.size < 0 || state_.curses.size > 3)
        throw std::invalid_argument("Invalid fixture state");
    random_.room(state_.room);
}
int Game::door_cost(Door door) const {
    int cost = door == Door::double_locked ? 2 : ((door == Door::locked || door == Door::epic) ? 1 : 0);
    if (state_.effect(EffectKind::lockpick)) return 0;
    if (state_.effect(EffectKind::hard_lock)) cost *= 2;
    return cost;
}
bool Game::available(DoorView door) const {
    if (door.kind == Door::wall) return false;
    if (state_.keys < door_cost(door.kind)) return false;
    const int resource = resource_index(door.kind);
    if (resource >= 0 && state_.resources[static_cast<std::size_t>(resource)] < 1) return false;
    if (door.kind == Door::trial && (state_.trial_seen && state_.trial_depth == 0)) return false;
    return true;
}
int Game::step_price() const { return state_.paid_steps == 0 ? 10 : (state_.paid_steps == 1 ? 15 : 20); }
int Game::full_price() const {
    return std::max(1, static_cast<int>(std::ceil((1 - state_.hp) * profile_.full_heal_cost_per_fraction - 1e-12)));
}
bool Game::legal(Action a) const {
    if (!std::isfinite(a.value)) return false;
    const auto phase = state_.phase;
    switch (a.kind) {
    case ActionKind::choose_door: return phase == Phase::doors && a.index >= 0 && a.index < 2 && available(state_.doors[static_cast<std::size_t>(a.index)]);
    case ActionKind::fight: return phase == Phase::encounter && is_enemy(state_.encounter);
    case ActionKind::flee: return phase == Phase::encounter && is_enemy(state_.encounter) && state_.encounter != Encounter::boss;
    case ActionKind::interact:
        return phase == Phase::encounter && !is_enemy(state_.encounter) && state_.encounter != Encounter::rps &&
            (state_.encounter != Encounter::locked_sarcophagus || state_.keys >= door_cost(Door::locked));
    case ActionKind::skip: return phase == Phase::shop || phase == Phase::curse_shop || (phase == Phase::encounter && is_optional(state_.encounter));
    case ActionKind::buy: return (phase == Phase::shop || phase == Phase::curse_shop) && a.index >= 0 && a.index < 2 &&
            (phase == Phase::curse_shop || state_.keys >= state_.offers[static_cast<std::size_t>(a.index)].keys);
    case ActionKind::reroll: return (phase == Phase::shop || phase == Phase::curse_shop) && state_.mushrooms < limits_.budget;
    case ActionKind::choose_gem: return phase == Phase::gems && a.index >= 0 && a.index < 3;
    case ActionKind::wait: return phase == Phase::recovery && a.value > 0 && a.value <= 1000000;
    case ActionKind::heal_step: return phase == Phase::recovery && state_.hp < 1 && step_price() <= limits_.budget - state_.mushrooms;
    case ActionKind::heal_full: return phase == Phase::recovery && state_.hp < 1 && full_price() <= limits_.budget - state_.mushrooms;
    case ActionKind::reenter: return phase == Phase::recovery && state_.hp >= .2 - 1e-12;
    case ActionKind::rps: return phase == Phase::encounter && state_.encounter == Encounter::rps && a.index >= 0 && a.index < 3;
    case ActionKind::linger: return phase == Phase::encounter && state_.encounter == Encounter::flooded && a.value >= 0 && a.value <= 3600;
    }
    return false;
}
void Game::step(Action a) {
    if (!legal(a)) throw std::invalid_argument("Illegal " + std::string(name(a.kind)) + " in " + std::string(name(state_.phase)));
    ++state_.actions;
    if (a.kind != ActionKind::wait) {
        const double hours = (a.kind == ActionKind::linger ? a.value : profile_.action_seconds) / 3600;
        state_.elapsed_hours += hours;
        state_.active_hours += hours;
        if (state_.phase == Phase::encounter && state_.encounter == Encounter::flooded && hours * 3600 >= 10) {
            hurt(1); return;
        }
    }
    switch (a.kind) {
    case ActionKind::choose_door: enter(state_.doors[static_cast<std::size_t>(a.index)]); break;
    case ActionKind::fight: fight(false); break;
    case ActionKind::flee: fight(true); break;
    case ActionKind::interact: interact(); break;
    case ActionKind::rps: interact(a.index); break;
    case ActionKind::linger: finish_room(); break;
    case ActionKind::skip:
        if (state_.phase == Phase::encounter && state_.encounter == Encounter::barrel) ++state_.barrels_skipped;
        finish_room(); break;
    case ActionKind::buy: {
        const auto offer = state_.offers[static_cast<std::size_t>(a.index)];
        state_.keys += (state_.phase == Phase::curse_shop ? offer.keys : -offer.keys);
        give(offer.effect); finish_room(); break;
    }
    case ActionKind::reroll:
        ++state_.mushrooms; ++state_.reroll_mushrooms; ++state_.shop_rerolls;
        generate_shop(state_.phase == Phase::curse_shop); break;
    case ActionKind::choose_gem:
        state_.gems[ix(state_.gem_offers[static_cast<std::size_t>(a.index)])] = true;
        state_.phase = Phase::doors; generate_doors(); break;
    case ActionKind::wait:
        state_.elapsed_hours += a.value;
        state_.hp = std::min(1.0, state_.hp + a.value / profile_.recovery_hours); break;
    case ActionKind::heal_step: {
        const int price = step_price();
        state_.mushrooms += price; state_.recovery_mushrooms += price; ++state_.paid_steps;
        state_.hp = std::min(1.0, state_.hp + .2); break;
    }
    case ActionKind::heal_full: {
        const int price = full_price();
        state_.mushrooms += price; state_.recovery_mushrooms += price; ++state_.paid_steps;
        state_.hp = 1; break;
    }
    case ActionKind::reenter:
        state_.phase = resume_phase_;
        if (state_.has(Gem::greasy)) give({EffectKind::recovery, .1, 3, Clock::room}, true);
        if (state_.has(Gem::rusty)) give({EffectKind::poison, .05, 5, Clock::room}, true);
        break;
    }
}
void Game::generate_doors() {
    if (state_.room % 25 == 0) { state_.doors = {{{Door::boss}, {Door::wall}}}; return; }
    if (state_.run_number == 1 && state_.room == 5) { state_.doors = {{{Door::shop}, {Door::wall}}}; return; }
    if (state_.trial_depth > 0) {
        state_.doors = {{{state_.trial_depth < 5 ? Door::trial : Door::wall}, {Door::exit_trial}}}; return;
    }
    auto weights = profile_.door_weights;
    if (state_.has(Gem::greed)) weights[ix(Door::mystery)] *= 2;
    if (state_.has(Gem::explorer)) weights[ix(Door::mystery)] *= .5;
    if (state_.has(Gem::spying)) {
        weights[ix(Door::unlocked)] += weights[ix(Door::locked)] * .5;
        weights[ix(Door::locked)] *= .5;
    }
    if (state_.has(Gem::lodestone)) weights[ix(Door::double_locked)] += weights[ix(Door::locked)] * .5;
    if (state_.has(Gem::devil)) weights[ix(Door::epic)] += 8;
    if (state_.has(Gem::greasy)) weights[ix(Door::epic)] = 0;
    if (state_.has(Gem::blood)) weights[ix(Door::sacrifice)] *= 2;
    if (state_.has(Gem::hick)) weights[ix(Door::sacrifice)] *= .5;
    if (state_.has(Gem::thirsty)) weights[ix(Door::cursed)] *= 2;
    if (state_.has(Gem::misadventurer)) weights[ix(Door::cursed)] *= .5;
    if (state_.trial_seen || state_.room <= 25 || state_.room % 25 > 18) weights[ix(Door::trial)] = 0;
    if (std::accumulate(weights.begin(), weights.end(), 0.0) <= 0) weights[ix(Door::monster)] = 1;
    for (auto& door : state_.doors) {
        door = {static_cast<Door>(random_.weighted(Stream::doors, weights.data(), weights.size()))};
        door.trap = trap_eligible(door.kind) && random_.chance(Stream::doors, profile_.trap_chance);
        door.cursed_trap = door.trap && random_.chance(Stream::doors, profile_.cursed_trap_chance);
    }
    if (state_.has(Gem::pendant)) state_.doors[0] = {Door::locked};
    ensure_open_path();
    if (state_.has(Gem::masochist)) {
        const auto side = random_.chance(Stream::doors, .5) ? 0U : 1U;
        state_.doors[side].trap = true;
    }
}
void Game::ensure_open_path() {
    // Synthetic safeguard, not a measured server rule. Recheck when death removes
    // a lockpick: doors generated before a lethal exit tick can become unaffordable.
    if (!available(state_.doors[0]) && !available(state_.doors[1])) state_.doors[1] = {Door::monster};
}
void Game::generate_shop(bool curses) {
    state_.phase = curses ? Phase::curse_shop : Phase::shop;
    for (auto& offer : state_.offers) {
        offer.effect = random_effect(curses, Stream::shop);
        const bool strong = offer.effect.kind == EffectKind::elixir ? offer.effect.magnitude > .25 :
            (offer.effect.kind == EffectKind::recovery ? offer.effect.magnitude > .1 :
            offer.effect.remaining > (offer.effect.kind == EffectKind::lockpick ? 2 :
            ((offer.effect.kind == EffectKind::escape || offer.effect.kind == EffectKind::raider ||
                offer.effect.kind == EffectKind::poison || offer.effect.kind == EffectKind::clumsy ||
                offer.effect.kind == EffectKind::gold_hangover) ? 5 : 4)));
        if (curses) {
            constexpr int prices[] = {1, 1, 2, 1, 2};
            offer.keys = prices[ix(offer.effect.kind) - ix(EffectKind::broken_armor)] * (strong ? 2 : 1);
        } else {
            constexpr int prices[] = {1, 2, 3, 2, 1, 1, 1, 1};
            offer.keys = prices[ix(offer.effect.kind)] * (strong ? 2 : 1);
            if (strong && (offer.effect.kind == EffectKind::elixir || offer.effect.kind == EffectKind::recovery)) offer.keys = 3;
            if (profile_.free_first_level_shops && state_.run_number == 1 && state_.room < 25) offer.keys = 0;
        }
    }
}
void Game::generate_gems() {
    auto pool = profile_.gem_pool;
    pool.erase(std::remove_if(pool.begin(), pool.end(), [this](Gem g) { return state_.has(g); }), pool.end());
    if (pool.size() < 3) throw std::logic_error("Insufficient distinct gem offers");
    for (std::size_t i = 0; i < 3; ++i) {
        const auto j = i + static_cast<std::size_t>(random_.unit(Stream::gems) * static_cast<double>(pool.size() - i));
        std::swap(pool[i], pool[j]); state_.gem_offers[i] = pool[i];
    }
}
void Game::enter(DoorView door) {
    state_.keys -= door_cost(door.kind);
    if (door.kind == Door::locked || door.kind == Door::double_locked || door.kind == Door::epic)
        state_.blessings.consume(Clock::door, state_.turn);
    state_.phase = Phase::encounter;
    state_.shop_rerolls = 0;
    const auto sample = [this](const auto& table) {
        return static_cast<Encounter>(random_.weighted(Stream::contents, table.data(), table.size()));
    };
    switch (door.kind) {
    case Door::boss: state_.encounter = Encounter::boss; break;
    case Door::monster: state_.encounter = Encounter::monster; break;
    case Door::mystery: case Door::unlocked: state_.encounter = sample(profile_.mystery_weights); break;
    case Door::locked: case Door::double_locked: case Door::epic:
        state_.encounter = door.kind == Door::epic ? Encounter::epic : sample(profile_.locked_weights);
        if (state_.has(Gem::old_sacrifice) && random_.chance(Stream::contents, .2)) state_.encounter = Encounter::sacrifice_chest;
        if (state_.has(Gem::kidney) && random_.chance(Stream::contents, .2)) state_.encounter = Encounter::cursed_chest;
        if (state_.has(Gem::deceit) && random_.chance(Stream::contents, profile_.hidden_monster_chance)) state_.encounter = Encounter::monster;
        break;
    case Door::golden: state_.encounter = sample(profile_.golden_weights); break;
    case Door::shop: state_.encounter = Encounter::empty; generate_shop(false); break;
    case Door::cursed: state_.encounter = Encounter::cursed_chest; give(random_effect(true), true); break;
    case Door::sacrifice: state_.encounter = Encounter::sacrifice_chest; break;
    case Door::blessing: state_.encounter = Encounter::monster; give(random_effect(false), true); break;
    case Door::destiny:
        state_.encounter = sample(profile_.mystery_weights);
        if (random_.chance(Stream::special, .5)) give(random_effect(false), true);
        else give(random_effect(true), true);
        break;
    case Door::trial: state_.trial_seen = true; state_.encounter = Encounter::trial_monster; break;
    case Door::exit_trial:
        state_.pending_trial_reward = state_.trial_depth; state_.trial_depth = 0;
        state_.encounter = Encounter::prize; break;
    default: {
        const int resource = resource_index(door.kind);
        if (resource < 0) throw std::logic_error("Unhandled door");
        --state_.resources[static_cast<std::size_t>(resource)]; state_.donated_resource = resource;
        state_.encounter = Encounter::sated_chest; break;
    }
    }
    if (state_.has(Gem::greasy) && state_.encounter == Encounter::epic) state_.encounter = Encounter::empty;
    if (state_.encounter == Encounter::curse_shop) generate_shop(true);
    if (door.kind == Door::sacrifice && hurt(.12 * (state_.has(Gem::blood) ? .6 : 1))) return;
    if (door.trap) {
        if (state_.effect(EffectKind::disarm)) state_.blessings.consume(Clock::trap, state_.turn);
        else if (door.cursed_trap || state_.has(Gem::gambler)) give(random_effect(true), true);
        else hurt(.1 * (state_.has(Gem::masochist) ? .8 : 1));
    }
}
double Game::battle_multiplier(bool fleeing) const {
    double multiplier = 1;
    if (state_.effect(EffectKind::broken_armor)) multiplier += .5;
    if (fleeing) {
        if (state_.has(Gem::hick)) multiplier += .3;
        if (state_.has(Gem::pearl)) multiplier -= .4;
    } else {
        if (state_.has(Gem::hero)) multiplier -= .2;
        if (state_.has(Gem::bull)) multiplier -= .2;
        if (state_.has(Gem::deceit)) multiplier -= .2;
        if (state_.has(Gem::rabbit)) multiplier += .25;
        if (state_.has(Gem::devil)) multiplier += .25;
    }
    return std::max(0.0, multiplier);
}
double Game::flee_probability() const {
    double p = profile_.escape_chance;
    if (state_.has(Gem::rabbit)) p += .4;
    if (state_.has(Gem::moonstone)) p += .2;
    if (state_.has(Gem::bull)) p -= .3;
    if (const auto* e = state_.effect(EffectKind::escape)) p += e->magnitude;
    if (const auto* e = state_.effect(EffectKind::clumsy)) p -= e->magnitude;
    return std::clamp(p, 0.0, 1.0);
}
double Game::damage_ceiling(bool boss) const {
    if (boss) return profile_.boss_damage[band(state_.room)].high;
    return std::max(profile_.monster_damage[band(state_.room)].high * battle_multiplier(false),
        profile_.escape_damage[band(state_.room)].high * battle_multiplier(true));
}
void Game::award_keys() {
    if (const auto* e = state_.effect(EffectKind::key_moment)) {
        if (random_.chance(Stream::keys, e->magnitude)) state_.keys += 2;
    } else {
        double p = profile_.key_chance + (state_.has(Gem::lodestone) ? .3 : 0) - (state_.has(Gem::spying) ? .15 : 0);
        if (random_.chance(Stream::keys, p)) ++state_.keys;
    }
    state_.blessings.consume(Clock::fight, state_.turn);
}
void Game::fight(bool fleeing) {
    const auto encounter = state_.encounter;
    const bool boss = encounter == Encounter::boss;
    if (fleeing && random_.chance(Stream::escape, flee_probability())) {
        if (state_.has(Gem::pendant) && random_.chance(Stream::keys, .4)) ++state_.keys;
        if (state_.has(Gem::pearl) && random_.chance(Stream::effects, .2)) give(random_effect(true));
        if (encounter == Encounter::trial_monster) { state_.trial_depth = 0; }
        finish_room(); return;
    }
    double damage = random_.uniform(Stream::damage, boss ? profile_.boss_damage[band(state_.room)] :
        (fleeing ? profile_.escape_damage[band(state_.room)] : profile_.monster_damage[band(state_.room)]));
    if (!boss) {
        if (encounter == Encounter::undead || encounter == Encounter::tube || encounter == Encounter::beta) damage *= .5;
        if (encounter == Encounter::shakes) damage = .3;
        if (encounter == Encounter::valaraukar) damage = .6;
        if (encounter == Encounter::pig) damage = .4;
        if (encounter == Encounter::trial_monster) damage *= profile_.trial_multipliers[static_cast<std::size_t>(state_.trial_depth)];
        damage *= battle_multiplier(fleeing);
        if (!fleeing && state_.effect(EffectKind::one_hit)) damage = 0;
    }
    if (hurt(damage)) return;
    if (!fleeing) {
        if (boss) { if (state_.room != 100) ++state_.epics; }
        else {
            award_keys();
            if (encounter == Encounter::mimic) ++state_.epics;
            if (encounter == Encounter::pig) heal(.6);
            if (encounter == Encounter::shakes) give(random_effect(false));
            if (encounter == Encounter::valaraukar) give({EffectKind::recovery, .1, 3, Clock::room});
            if (state_.has(Gem::kidney) && random_.chance(Stream::effects, .1)) give(random_effect(false));
            const double curse = profile_.combat_curse_chance + (state_.has(Gem::misadventurer) ? .1 : 0);
            if (state_.room > 25 && random_.chance(Stream::effects, curse)) give(random_effect(true));
        }
    }
    if (encounter == Encounter::trial_monster) {
        if (fleeing) state_.trial_depth = 0;
        else ++state_.trial_depth;
    }
    finish_room();
}
bool Game::hurt(double damage) {
    state_.hp = std::max(0.0, state_.hp - damage);
    if (state_.hp <= 0) { die(); return true; }
    return false;
}
void Game::heal(double fraction) {
    state_.hp = std::min(1.0, state_.hp + fraction * (state_.has(Gem::rusty) ? 1.2 : 1));
}
void Game::die() {
    state_.hp = 0; ++state_.deaths;
    state_.blessings.clear(); state_.curses.clear();
    if (state_.trial_depth > 0 || state_.encounter == Encounter::trial_monster) {
        state_.trial_depth = 0; state_.phase = Phase::doors; generate_doors();
    }
    if (state_.phase == Phase::doors) ensure_open_path();
    resume_phase_ = state_.phase; state_.phase = Phase::recovery;
}
void Game::finish_room() {
    const bool boss = state_.room % 25 == 0;
    bool lethal_tick = false;
    if (!boss) {
        if (const auto* e = state_.effect(EffectKind::poison)) {
            state_.hp = std::max(0.0, state_.hp - e->magnitude); lethal_tick = state_.hp <= 0;
        }
        if (!lethal_tick) if (const auto* e = state_.effect(EffectKind::recovery)) heal(e->magnitude);
    }
    state_.blessings.consume(Clock::room, state_.turn);
    state_.curses.consume(Clock::room, state_.turn);
    const int finished = state_.room++;
    ++state_.turn; random_.room(state_.room); state_.encounter = Encounter::empty;
    if (finished == 100) { state_.phase = Phase::complete; ++state_.legendaries; }
    else if (boss) { state_.phase = Phase::gems; generate_gems(); }
    else { state_.phase = Phase::doors; generate_doors(); }
    // The completed room is committed before a lethal exit tick: no duplicate rewards.
    if (lethal_tick) die();
}
Effect Game::random_effect(bool curse, Stream stream) {
    std::size_t kind = curse ? random_.weighted(stream, profile_.curse_weights.data(), 5) + 8 :
        random_.weighted(stream, profile_.blessing_weights.data(), 8);
    const double strong_chance = profile_.strong_effect + ((curse && state_.has(Gem::explorer)) ? .2 : 0) +
        ((!curse && state_.encounter == Encounter::barrel && state_.has(Gem::thirsty)) ? .4 : 0);
    const bool strong = random_.chance(stream, strong_chance);
    const int twice = strong ? 2 : 1;
    const auto k = static_cast<EffectKind>(kind);
    switch (k) {
    case EffectKind::raider: return {k, 1, 5 * twice, Clock::room};
    case EffectKind::one_hit: return {k, 1, 4 * twice, Clock::room};
    case EffectKind::escape: return {k, .8, 5 * twice, Clock::room};
    case EffectKind::disarm: return {k, 1, 4 * twice, Clock::trap};
    case EffectKind::lockpick: return {k, 1, 2 * twice, Clock::door};
    case EffectKind::key_moment: return {k, .7, 4 * twice, Clock::fight};
    case EffectKind::elixir: return {k, strong ? .5 : .25, 1, Clock::room};
    case EffectKind::recovery: return {k, strong ? .2 : .1, 3 * twice, Clock::room};
    case EffectKind::broken_armor: return {k, .5, 4 * twice, Clock::room};
    case EffectKind::poison: return {k, .05, 5 * twice, Clock::room};
    case EffectKind::clumsy: return {k, .8, 5 * twice, Clock::room};
    case EffectKind::gold_hangover: return {k, .5, 5 * twice, Clock::room};
    case EffectKind::hard_lock: return {k, 2, 4 * twice, Clock::room};
    default: throw std::logic_error("Unhandled effect");
    }
}
void Game::give(Effect effect, bool immediately) {
    if (effect.kind == EffectKind::elixir) { heal(effect.magnitude); return; }
    effect.starts_at = state_.turn + (immediately ? 0 : 1);
    if (is_curse(effect.kind)) {
        if (state_.has(Gem::moonstone)) ++effect.remaining;
        state_.curses.give(effect);
    } else {
        if (state_.has(Gem::time_traveler)) ++effect.remaining;
        state_.blessings.give(effect);
    }
}
void Game::random_reward() {
    auto weights = profile_.container_weights;
    const double total = std::accumulate(weights.begin(), weights.end(), 0.0);
    const double blessing = weights[0] / total + (state_.has(Gem::greed) ? .2 : 0);
    if (random_.chance(Stream::effects, blessing)) { give(random_effect(false)); return; }
    weights[0] = 0;
    if (std::accumulate(weights.begin(), weights.end(), 0.0) == 0) return;
    const auto outcome = random_.weighted(Stream::contents, weights.data(), weights.size());
    if (outcome == 2) state_.gold_rewards += state_.effect(EffectKind::raider) ? 2 : 1;
    // Ordinary items are deliberately not valued in the completion objective.
}
void Game::interact(int choice) {
    const auto encounter = state_.encounter;
    switch (encounter) {
    case Encounter::barrel: {
        ++state_.barrels_opened;
        const double p = state_.has(Gem::time_traveler) ? 0 : profile_.barrel_blessing + (state_.has(Gem::gambler) ? .5 : 0);
        give(random_effect(!random_.chance(Stream::effects, p))); break;
    }
    case Encounter::crate: case Encounter::silver: case Encounter::bronze: random_reward(); break;
    case Encounter::skeleton:
        if (random_.chance(Stream::contents, profile_.skeleton_wakes)) { state_.encounter = Encounter::monster; return; }
        random_reward(); break;
    case Encounter::epic: case Encounter::locker: case Encounter::armory:
        if (!state_.has(Gem::hero) || random_.chance(Stream::contents, .75)) ++state_.epics;
        break;
    case Encounter::cursed_chest: give(random_effect(true)); ++state_.resources[0]; break;
    case Encounter::sacrifice_chest:
        if (hurt(.15 * (state_.has(Gem::old_sacrifice) ? .8 : 1))) return;
        ++state_.resources[0]; break;
    case Encounter::sated_chest: {
        int resource = static_cast<int>(random_.unit(Stream::contents) * 5);
        if (resource >= state_.donated_resource) ++resource;
        state_.resources[static_cast<std::size_t>(std::clamp(resource, 0, 5))] += 2;
        break;
    }
    case Encounter::prize:
        if (state_.pending_trial_reward >= 4 && random_.chance(Stream::contents, profile_.trial_legendary_chance)) ++state_.legendaries;
        else if (state_.pending_trial_reward >= 2) ++state_.epics;
        else ++state_.gold_rewards;
        state_.pending_trial_reward = 0; break;
    case Encounter::fountain: heal(.25); break;
    case Encounter::cleansing_fountain: state_.curses.clear(); heal(.25); break;
    case Encounter::rocks: ++state_.resources[1]; break;
    case Encounter::wood: ++state_.resources[0]; break;
    case Encounter::souls: ++state_.resources[2]; break;
    case Encounter::arcane: ++state_.resources[4]; break;
    case Encounter::lava: if (hurt(.1)) return; break;
    case Encounter::narrator: heal(.25); give(random_effect(false)); break;
    case Encounter::wishing_well:
        if (random_.chance(Stream::special, .5)) give(random_effect(false)); else ++state_.epics;
        break;
    case Encounter::rps: {
        const int opponent = static_cast<int>(random_.unit(Stream::special) * 3);
        const int outcome = (choice - opponent + 3) % 3;
        if (outcome == 1) give(random_effect(false));
        if (outcome == 2) { if (hurt(.1)) return; give(random_effect(true)); }
        break;
    }
    case Encounter::sewers: case Encounter::auction: ++state_.epics; break;
    case Encounter::sarcophagus: ++state_.gold_rewards; break;
    case Encounter::locked_sarcophagus:
        state_.keys -= door_cost(Door::locked); state_.blessings.consume(Clock::door, state_.turn); ++state_.epics; break;
    case Encounter::wheel: {
        const int outcome = static_cast<int>(random_.unit(Stream::special) * 5);
        if (outcome == 0) give(random_effect(false));
        if (outcome == 1) give(random_effect(true));
        if (outcome == 2) ++state_.keys;
        if (outcome == 3) state_.keys = std::max(0, state_.keys - 1);
        if (outcome == 4) ++state_.gold_rewards;
        break;
    }
    case Encounter::spider_legs: case Encounter::spider_head: case Encounter::spider_full: {
        const double success = encounter == Encounter::spider_legs ? .8 : (encounter == Encounter::spider_head ? .5 : .2);
        if (random_.chance(Stream::special, success)) state_.keys += encounter == Encounter::spider_legs ? 1 : (encounter == Encounter::spider_head ? 2 : 5);
        else give({EffectKind::poison, .05, 5, Clock::room});
        break;
    }
    case Encounter::rainbow: if (hurt(.2)) return; give(random_effect(false)); break;
    case Encounter::empty: case Encounter::flooded: break;
    case Encounter::curse_shop: generate_shop(true); return;
    default: throw std::logic_error("Encounter needs a different action: " + std::string(name(encounter)));
    }
    finish_room();
}
}
