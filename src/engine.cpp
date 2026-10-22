#include "sfld/engine.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <utility>

namespace sfld {
namespace {
std::size_t band(int room) { return static_cast<std::size_t>(std::clamp((room - 1) / 25, 0, 3)); }
int resource_index(Door door) {
    return door >= Door::wood && door <= Door::hourglasses ? static_cast<int>(ix(door) - ix(Door::wood)) : -1;
}
bool trap_eligible(Door door) { return door == Door::monster || door == Door::mystery; }
bool door_type(Door door) {
    return door != Door::monster && door != Door::mystery && door != Door::golden;
}
bool compatible(Door a, Door b) {
    return !(door_type(a) && door_type(b)) && !(a == Door::monster && b == Door::monster);
}
void record_held_gems(State& state) {
    for (std::size_t i = 0; i < state.gems.size(); ++i)
        state.offered_gems[i] = state.offered_gems[i] || state.gems[i];
}
void record_gem_menu(State& state) {
    for (Gem gem : state.gem_offers) {
        if (ix(gem) >= state.offered_gems.size()) throw std::invalid_argument("Invalid displayed gem");
        state.offered_gems[ix(gem)] = true;
    }
}
void check_limits(Limits limits) {
    if (limits.budget < 0 || limits.budget > 1000000 || !std::isfinite(limits.deadline_hours) ||
        limits.deadline_hours <= 0 || limits.deadline_hours > 1000000 || limits.max_actions == 0 || limits.run_number < 1 || limits.run_number > 1000000 ||
        (limits.restart_health != "full" && limits.restart_health != "carry" && limits.restart_health != "empty"))
        throw std::invalid_argument("Invalid run limits");
    if (!std::isfinite(limits.sleep_until_hour) || limits.sleep_until_hour < 0 || limits.sleep_until_hour >= 24 ||
        !std::isfinite(limits.event_start_hour) || limits.event_start_hour < 0 || limits.event_start_hour >= 24)
        throw std::invalid_argument("Daily schedule hours must be finite and in [0,24)");
}
void check_planning_snapshot(const PlanningSnapshot& snapshot) {
    const auto& s = snapshot.state; const auto& l = snapshot.limits;
    check_limits(l);
    const auto nonnegative = [](double x) { return std::isfinite(x) && x >= 0; };
    if (ix(s.phase) > ix(Phase::complete) || ix(snapshot.recovery_destination) > ix(Phase::complete) ||
        (s.phase == Phase::recovery && (snapshot.recovery_destination == Phase::recovery || snapshot.recovery_destination == Phase::complete)) ||
        s.room < 1 || s.room > 101 || s.turn < 0 || s.turn > 100 || s.run_number < 1 || s.run_number > 1000000 ||
        (s.room == 101) != (s.phase == Phase::complete) || !nonnegative(s.hp) || s.hp > 1 ||
        s.mushrooms > l.budget || s.actions > l.max_actions || !nonnegative(s.elapsed_hours) || s.elapsed_hours > l.deadline_hours + 1e-9 ||
        !nonnegative(s.active_hours) || s.active_hours > s.elapsed_hours + 1e-9 ||
        !nonnegative(s.run_start_hours) || s.run_start_hours > s.elapsed_hours + 1e-9 ||
        s.run_start_mushrooms > s.mushrooms || !nonnegative(s.gold_units) ||
        !nonnegative(s.flooded_seconds) || s.flooded_seconds > 3600 ||
        !std::isfinite(s.first_completion_hours) || s.first_completion_hours < -1 || s.first_completion_hours > s.elapsed_hours + 1e-9 ||
        s.paid_steps < 0 || s.paid_steps > 1000000 || s.trial_depth < 0 || s.trial_depth > 5 ||
        s.pending_trial_reward < 0 || s.pending_trial_reward > 5 || s.donated_resource < -1 || s.donated_resource > 5)
        throw std::invalid_argument("Invalid visible planning state or limits");
    for (int value : {s.keys, s.deaths, s.mushrooms, s.mushrooms_found, s.recovery_mushrooms, s.reroll_mushrooms,
        s.shop_rerolls, s.reroll_shop_visits, s.reroll_bank_used, s.shop_purchases, s.strong_one_hit_purchases,
        s.epics, s.legendaries, s.gold_rewards, s.lucky_coins, s.barrels_opened, s.barrels_skipped,
        s.run_start_mushrooms, s.completed_runs})
        if (value < 0 || value > 1000000000) throw std::invalid_argument("Invalid planning counter");
    for (int value : s.resources) if (value < 0 || value > 1000000000) throw std::invalid_argument("Invalid planning resource balance");
    const auto effect_valid = [&s, &nonnegative](const Effect& e, bool current) {
        return ix(e.kind) < ix(EffectKind::count) && ix(e.clock) <= ix(Clock::fight) && nonnegative(e.magnitude) &&
            e.remaining >= 0 && e.remaining <= 1000000 && e.starts_at >= 0 && (!current || e.starts_at <= s.turn + 1);
    };
    for (const auto& effects : {std::pair{&s.blessings, false}, std::pair{&s.curses, true}}) {
        if (effects.first->size < 0 || effects.first->size > 3) throw std::invalid_argument("Invalid planning effect count");
        std::array<bool, ix(EffectKind::count)> seen{};
        for (int i = 0; i < effects.first->size; ++i) {
            const auto& e = effects.first->slots[static_cast<std::size_t>(i)];
            if (!effect_valid(e, true) || e.remaining == 0 || e.kind == EffectKind::elixir || is_curse(e.kind) != effects.second || seen[ix(e.kind)])
                throw std::invalid_argument("Invalid planning active effect");
            seen[ix(e.kind)] = true;
        }
    }
    for (auto door : s.doors)
        if (ix(door.kind) >= ix(Door::count) || (door.cursed_trap && !door.trap)) throw std::invalid_argument("Invalid planning door");
    if (ix(s.encounter) >= ix(Encounter::count)) throw std::invalid_argument("Invalid planning encounter");
    const auto phase = s.phase == Phase::recovery ? snapshot.recovery_destination : s.phase;
    for (auto offer : s.offers)
        if (!effect_valid(offer.effect, phase == Phase::shop || phase == Phase::curse_shop) || offer.keys < 0 || offer.keys > 1000000)
            throw std::invalid_argument("Invalid planning offer");
    for (auto gem : s.gem_offers) if (ix(gem) >= ix(Gem::count)) throw std::invalid_argument("Invalid planning stone offer");
    for (const auto& outcome : snapshot.outcomes)
        if (!std::isfinite(outcome.amount) || outcome.duration < 0) throw std::invalid_argument("Invalid planning outcome");
}
}
Game::Game(const Profile& profile, std::uint64_t seed, Limits limits)
    : profile_(profile), seed_(seed), random_(seed), limits_(limits) {
    check_limits(limits);
    state_.run_number = limits.run_number;
    state_.resources.fill(profile.initial_resources);
    generate_doors();
    respect_sleep_schedule();
}
Game::Game(const Profile& profile, std::uint64_t seed, State state, Limits limits)
    : profile_(profile), seed_(seed), random_(seed), state_(std::move(state)), limits_(limits) {
    check_limits(limits);
    if (state_.room < 1 || state_.room > 101 || !std::isfinite(state_.hp) || state_.hp < 0 || state_.hp > 1 ||
        state_.keys < 0 || state_.mushrooms < 0 || state_.mushrooms > limits.budget || state_.mushrooms_found < 0 ||
        state_.blessings.size < 0 || state_.blessings.size > 3 || state_.curses.size < 0 || state_.curses.size > 3)
        throw std::invalid_argument("Invalid fixture state");
    random_.room(state_.room);
    if (state_.phase == Phase::complete) state_.offered_gems.fill(false);
    else {
        record_held_gems(state_);
        if (state_.phase == Phase::gems) record_gem_menu(state_);
    }
    if (state_.phase == Phase::shop || (state_.phase == Phase::doors &&
        std::any_of(state_.doors.begin(), state_.doors.end(), [](DoorView d) { return d.kind == Door::shop; })))
        state_.shops_seen[band(state_.room)] = true;
    if (state_.phase == Phase::shop && (!opening_shop() || profile_.opening_shop_rerolls != "disabled"))
        state_.reroll_shop_visits = std::max(1, state_.reroll_shop_visits);
    respect_sleep_schedule();
}
Game::Game(const Profile& profile, std::uint64_t seed, const StartState& start, Limits limits)
    : Game(profile, seed, start.state, limits) {
    start.validate(profile);
    limits_.run_number = state_.run_number;
    resume_phase_ = start.resume_phase;
    const auto phase = start.decision_phase();
    // Discard the fixture constructor's placeholder menu when this screen was
    // unknown. Only supplied history, owned stones and explicit menus are known.
    state_.offered_gems = start.state.offered_gems;
    record_held_gems(state_);
    if (phase == Phase::gems && !start.generate_current) record_gem_menu(state_);
    if (start.generate_current) {
        if (phase == Phase::doors) generate_doors();
        else if (phase == Phase::shop || phase == Phase::curse_shop) generate_shop(phase == Phase::curse_shop);
        else if (phase == Phase::gems) generate_gems();
    }
    state_.phase = start.state.phase;
    if (phase == Phase::shop && !(state_.run_number == 1 && state_.room == 5 && profile_.opening_shop_rerolls == "disabled"))
        state_.reroll_shop_visits = std::max(1, state_.reroll_shop_visits);
    // Observed walls/locked doors must never be silently replaced by the synthetic
    // generation safeguard. Invalid input should produce an actionable error.
    if (phase == Phase::doors && !available(state_.doors[0]) && !available(state_.doors[1]))
        throw std::invalid_argument("No accessible door in supplied progress; check walls, keys, resources and effects");
}
Game::Game(const Profile& profile, std::uint64_t independent_seed, const PlanningSnapshot& snapshot)
    : profile_(profile), seed_(independent_seed), random_(independent_seed), state_(snapshot.state), limits_(snapshot.limits),
      resume_phase_(snapshot.recovery_destination), outcomes_(snapshot.outcomes) {
    check_planning_snapshot(snapshot);
    // Do not normalize counters or regenerate revealed doors/offers. Old stream
    // positions deliberately do not survive: these are independent futures.
    random_.room(state_.room);
    if (!finished() && (state_.phase == Phase::doors || (state_.phase == Phase::recovery && resume_phase_ == Phase::doors)) &&
        !available(state_.doors[0]) && !available(state_.doors[1]))
        throw std::invalid_argument("No accessible door in planning snapshot");
    respect_sleep_schedule();
}
PlanningSnapshot Game::planning_snapshot() const {
    return {state_, limits_, resume_phase_, outcomes_};
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
    if (door.kind == Door::trial && ((state_.trial_seen && state_.trial_depth == 0) || state_.trial_depth >= 5)) return false;
    if (door.kind == Door::exit_trial && state_.trial_depth == 0) return false;
    return true;
}
int Game::step_price() const { return 10 + 5 * state_.paid_steps; }
int Game::full_price() const {
    return std::max(1, static_cast<int>(std::ceil((1 - state_.hp) * profile_.full_heal_cost_per_fraction - 1e-12)));
}
bool Game::legal(Action a) const {
    if (!std::isfinite(a.value)) return false;
    if (state_.actions >= limits_.max_actions || state_.elapsed_hours >= limits_.deadline_hours ||
        action_hours(a) > limits_.deadline_hours - state_.elapsed_hours + 1e-12) return false;
    if (limits_.sleep_until_hour > 0) {
        const double clock = local_hour();
        if (clock < limits_.sleep_until_hour - 1e-12) return false;
        // Recovery waits are passive and may span the night. Every active
        // action must finish by midnight; the next decision is at wake-up.
        if (a.kind != ActionKind::wait && action_hours(a) > 24 - clock + 1e-12) return false;
    }
    const auto phase = state_.phase;
    switch (a.kind) {
    case ActionKind::choose_door: return phase == Phase::doors && a.index >= 0 && a.index < 2 && available(state_.doors[static_cast<std::size_t>(a.index)]);
    case ActionKind::fight: return phase == Phase::encounter && is_enemy(state_.encounter);
    case ActionKind::flee: return phase == Phase::encounter && is_enemy(state_.encounter) && state_.encounter != Encounter::boss && state_.encounter != Encounter::mimic;
    case ActionKind::interact:
        return phase == Phase::encounter && !is_enemy(state_.encounter) &&
            (state_.encounter != Encounter::locked_sarcophagus || state_.keys >= door_cost(Door::locked));
    case ActionKind::skip: return phase == Phase::shop || phase == Phase::curse_shop || (phase == Phase::encounter && is_optional(state_.encounter));
    case ActionKind::buy: return (phase == Phase::shop || phase == Phase::curse_shop) && a.index >= 0 && a.index < 2 &&
            (phase == Phase::curse_shop || state_.keys >= state_.offers[static_cast<std::size_t>(a.index)].keys);
    case ActionKind::reroll: return phase == Phase::shop && state_.mushrooms < limits_.budget &&
        (!opening_shop() || profile_.opening_shop_rerolls != "disabled");
    case ActionKind::choose_gem: return phase == Phase::gems && a.index >= 0 && a.index < 3;
    case ActionKind::wait: return phase == Phase::recovery && a.value > 0 && a.value <= 1000000;
    case ActionKind::heal_step: return phase == Phase::recovery && state_.hp < 1 && step_price() <= limits_.budget - state_.mushrooms;
    case ActionKind::heal_full: return phase == Phase::recovery && state_.hp < 1 && full_price() <= limits_.budget - state_.mushrooms;
    case ActionKind::reenter: return phase == Phase::recovery && state_.hp >= .2 - 1e-12;
    case ActionKind::rps: return false; // Legacy action spelling; the game picks both hands.
    case ActionKind::linger: return phase == Phase::encounter && state_.encounter == Encounter::flooded && a.value >= 0 && a.value <= 3600;
    case ActionKind::restart: return phase == Phase::complete && limits_.repeat_runs;
    }
    return false;
}
double Game::action_hours(Action a) const {
    if (a.kind == ActionKind::wait) return a.value;
    return (a.kind == ActionKind::linger ? a.value : profile_.action_seconds) / 3600;
}
double Game::local_hour() const {
    return std::fmod(limits_.event_start_hour + state_.elapsed_hours, 24.0);
}
void Game::respect_sleep_schedule() {
    if (limits_.sleep_until_hour == 0 || finished()) return;
    const double clock = local_hour();
    double idle = 0;
    if (clock < limits_.sleep_until_hour - 1e-12) idle = limits_.sleep_until_hour - clock;
    else if (profile_.action_seconds / 3600 > 24 - clock + 1e-12)
        idle = 24 - clock + limits_.sleep_until_hour;
    idle = std::min(idle, limits_.deadline_hours - state_.elapsed_hours);
    if (idle <= 0) return;
    state_.elapsed_hours += idle;
    result("sleep", "daily_schedule", idle);
    // In the real game recovery starts only after death. A living character
    // keeps their HP, effects and current decision while the player is away.
    if (state_.phase == Phase::recovery) {
        const double healing = std::min(1 - state_.hp, idle / profile_.recovery_hours);
        state_.hp += healing;
        if (healing > 0) result("healing", "overnight", healing);
    }
}
bool Game::finished() const {
    if (state_.actions >= limits_.max_actions || state_.elapsed_hours >= limits_.deadline_hours) return true;
    if (state_.phase == Phase::complete && !limits_.repeat_runs) return true;
    return state_.phase != Phase::recovery && profile_.action_seconds / 3600 > limits_.deadline_hours - state_.elapsed_hours + 1e-12;
}
void Game::step(Action a) {
    if (!legal(a)) throw std::invalid_argument("Illegal " + std::string(name(a.kind)) + " in " + std::string(name(state_.phase)));
    outcomes_.clear();
    const auto before = state_;
    apply(a);
    // Observable rewards only; never reveal the contents of an unopened choice.
    if (a.kind != ActionKind::restart) {
        const auto delta = [&](std::string_view kind, double value) { if (value != 0) result(kind, "", value); };
        delta("keys", state_.keys - before.keys);
        delta("mushrooms_spent", state_.mushrooms - before.mushrooms);
        delta("mushrooms_found", state_.mushrooms_found - before.mushrooms_found);
        delta("epics", state_.epics - before.epics);
        delta("legendaries", state_.legendaries - before.legendaries);
        delta("lucky_coins", state_.lucky_coins - before.lucky_coins);
        constexpr std::string_view resources[]{"wood", "stone", "souls", "metal", "arcane", "hourglasses"};
        for (std::size_t i = 0; i < before.resources.size(); ++i)
            if (state_.resources[i] != before.resources[i]) result("resource", resources[i], state_.resources[i] - before.resources[i]);
    }
    if (state_.deaths > before.deaths) result("death", resume_phase_ == Phase::doors ? "next_choice" : "retry_boss");
    if (state_.completed_runs > before.completed_runs) result("completed");
    respect_sleep_schedule();
}
void Game::result(std::string_view kind, std::string_view detail, double amount, int duration) {
    outcomes_.push_back({kind, detail, amount, duration});
}
void Game::apply(Action a) {
    ++state_.actions;
    if (a.kind != ActionKind::wait) {
        const double hours = action_hours(a);
        state_.elapsed_hours += hours;
        state_.active_hours += hours;
        if (state_.phase == Phase::encounter && state_.encounter == Encounter::flooded) {
            state_.flooded_seconds += hours * 3600;
            if (state_.flooded_seconds >= 10 - 1e-12) { hurt(1); return; }
        }
    }
    switch (a.kind) {
    case ActionKind::choose_door: enter(state_.doors[static_cast<std::size_t>(a.index)]); break;
    case ActionKind::fight: fight(false); break;
    case ActionKind::flee: fight(true); break;
    case ActionKind::interact: interact(); break;
    case ActionKind::rps: break;
    case ActionKind::linger: finish_room(); break;
    case ActionKind::skip:
        if (state_.phase == Phase::encounter && state_.encounter == Encounter::barrel) ++state_.barrels_skipped;
        if (state_.phase == Phase::encounter && state_.encounter == Encounter::prize) state_.pending_trial_reward = 0;
        finish_room(); break;
    case ActionKind::buy: {
        const auto offer = state_.offers[static_cast<std::size_t>(a.index)];
        if (state_.phase == Phase::shop) {
            ++state_.shop_purchases;
            if (offer.effect.kind == EffectKind::one_hit && offer.effect.remaining >= 8) ++state_.strong_one_hit_purchases;
        }
        state_.keys += (state_.phase == Phase::curse_shop ? offer.keys : -offer.keys);
        give(offer.effect); finish_room(); break;
    }
    case ActionKind::reroll:
        ++state_.mushrooms; ++state_.reroll_mushrooms; ++state_.shop_rerolls;
        ++state_.reroll_bank_used;
        generate_shop(false); break;
    case ActionKind::choose_gem:
        record_gem_menu(state_);
        state_.gems[ix(state_.gem_offers[static_cast<std::size_t>(a.index)])] = true;
        state_.phase = Phase::doors; generate_doors(); break;
    case ActionKind::wait:
        state_.elapsed_hours += a.value;
        result("healing", "natural", std::min(1 - state_.hp, a.value / profile_.recovery_hours));
        state_.hp = std::min(1.0, state_.hp + a.value / profile_.recovery_hours); break;
    case ActionKind::heal_step: {
        const int price = step_price();
        state_.mushrooms += price; state_.recovery_mushrooms += price; ++state_.paid_steps;
        result("healing", "refill", std::min(1 - state_.hp, .2));
        state_.hp = std::min(1.0, state_.hp + .2); break;
    }
    case ActionKind::heal_full: {
        const int price = full_price();
        state_.mushrooms += price; state_.recovery_mushrooms += price; ++state_.paid_steps;
        result("healing", "refill", 1 - state_.hp); state_.hp = 1; break;
    }
    case ActionKind::reenter:
        state_.phase = resume_phase_;
        state_.flooded_seconds = 0;
        if (state_.has(Gem::greasy)) give({EffectKind::recovery, .1, 3, Clock::room}, true);
        if (state_.has(Gem::rusty)) give({EffectKind::poison, .05, 5, Clock::room}, true);
        break;
    case ActionKind::restart: restart(); break;
    }
}
void Game::restart() {
    // Event counters, time, spending and external resources survive; dungeon
    // keys, stones, effects and per-run refill pricing start a fresh run.
    ++state_.run_number;
    state_.run_start_hours = state_.elapsed_hours;
    state_.run_start_mushrooms = state_.mushrooms;
    if (limits_.restart_health == "full") state_.hp = 1;
    else if (limits_.restart_health == "empty") state_.hp = 0;
    state_.room = 1; state_.turn = 0; state_.keys = 0;
    state_.gems.fill(false); state_.offered_gems.fill(false);
    state_.blessings.clear(); state_.curses.clear();
    state_.paid_steps = 0; state_.shop_rerolls = 0;
    state_.shops_seen.fill(false);
    state_.trial_depth = 0; state_.trial_seen = false; state_.pending_trial_reward = 0;
    state_.donated_resource = -1; state_.encounter = Encounter::empty;
    state_.flooded_seconds = 0; state_.phase = Phase::doors;
    random_ = Random(Random::mix(seed_ ^ static_cast<std::uint64_t>(state_.run_number)));
    generate_doors();
    if (state_.hp < .2) { resume_phase_ = Phase::doors; state_.phase = Phase::recovery; }
}
void Game::generate_doors() {
    if (state_.room % 25 == 0) { state_.doors = {{{Door::boss}, {Door::wall}}}; return; }
    if (state_.run_number == 1 && state_.room == 5) {
        state_.shops_seen[0] = true; state_.doors = {{{Door::shop}, {Door::wall}}}; return;
    }
    if (state_.room <= 5) {
        // Player-confirmed opening protection applies to every run and profile.
        Door other = Door::mystery;
        if (profile_.opening_door_weights)
            other = static_cast<Door>(random_.weighted(Stream::doors, profile_.opening_door_weights->data(), profile_.opening_door_weights->size()));
        state_.doors = {{{Door::monster}, {other}}};
        if (random_.chance(Stream::doors, .5)) std::swap(state_.doors[0], state_.doors[1]);
        return;
    }
    if (state_.trial_depth > 0) {
        state_.doors = {{{state_.trial_depth < 5 ? Door::trial : Door::wall}, {Door::exit_trial}}}; return;
    }
    if (state_.room == profile_.trial_entry_room && !state_.trial_seen) {
        // This special pair has no inferred random traps or gem-based replacement.
        state_.doors = {{{Door::monster}, {Door::trial}}}; return;
    }
    auto weights = profile_.door_weights;
    if (state_.run_number > 1 && state_.room <= 25 && !profile_.later_stage_one_shop)
        weights[ix(Door::shop)] = 0;
    if (profile_.shop_schedule == "one_per_stage") {
        weights[ix(Door::shop)] = 0;
        const auto stage = band(state_.room);
        // Uniform conditional placement over remaining eligible ordinary rooms,
        // optionally restricted to an explicit sensitivity window. Positions are
        // assumptions; later stage one has no shop in current profiles.
        const bool eligible = (stage > 0 || (profile_.later_stage_one_shop && state_.run_number > 1 && state_.room >= 6)) &&
            (!profile_.shop_windows || state_.room >= (*profile_.shop_windows)[stage].low);
        if (eligible && !state_.shops_seen[stage]) {
            const int last = profile_.shop_windows ? static_cast<int>((*profile_.shop_windows)[stage].high) :
                static_cast<int>((stage + 1) * 25) - 1;
            int remaining = last - state_.room + 1;
            if (profile_.trial_entry_room > state_.room && profile_.trial_entry_room <= last) --remaining;
            // A late imported state or an arena spanning the window must not
            // silently lose its quota. Fixed boss/arena/opening rooms above keep
            // precedence; the next eligible ordinary room receives the shop.
            remaining = std::max(1, remaining);
            if (random_.chance(Stream::schedule, 1.0 / remaining)) {
                state_.shops_seen[stage] = true;
                state_.doors = {{{Door::shop}, {Door::wall}}}; return;
            }
        }
    }
    if (profile_.trial_entry_room != 0 || state_.trial_seen || state_.room <= 25 || state_.room % 25 > 18)
        weights[ix(Door::trial)] = 0;
    if (profile_.door_pair_weights) {
        // Joint ordinary-offer observations exclude progression encounters.
        // Their prescribed opening, scheduled shop and arena paths above still
        // apply; legacy weighted profiles without a joint table are unchanged.
        for (auto kind : {Door::shop, Door::trial, Door::boss, Door::exit_trial}) weights[ix(kind)] = 0;
    }
    // The joint-family model conditions styles on a physical choice. Retain
    // the old stone multipliers as a stated assumption by scaling door-family
    // exposure with the change in total physical weight. Gated variants are
    // excluded from the unmodified baseline, even if present in an old file.
    double baseline_physical = 0;
    for (std::size_t i = 0; i < weights.size(); ++i) {
        const auto kind = static_cast<Door>(i);
        if (door_type(kind) && kind != Door::unlocked && kind != Door::double_locked && kind != Door::epic)
            baseline_physical += weights[i];
    }
    if (state_.has(Gem::greed)) weights[ix(Door::mystery)] *= 2;
    if (state_.has(Gem::explorer)) weights[ix(Door::mystery)] *= .5;
    // These variants do not exist until their enabling stone is selected.
    weights[ix(Door::unlocked)] = 0;
    weights[ix(Door::double_locked)] = 0;
    weights[ix(Door::epic)] = 0;
    if (state_.has(Gem::spying)) {
        weights[ix(Door::unlocked)] += weights[ix(Door::locked)] * profile_.spying_unlock_fraction;
        weights[ix(Door::locked)] *= 1 - profile_.spying_unlock_fraction;
    }
    if (state_.has(Gem::lodestone)) weights[ix(Door::double_locked)] += weights[ix(Door::locked)] * .5;
    if (state_.has(Gem::devil)) weights[ix(Door::epic)] += 8;
    if (state_.has(Gem::greasy)) weights[ix(Door::epic)] = 0;
    if (state_.has(Gem::blood)) weights[ix(Door::sacrifice)] *= 2;
    if (state_.has(Gem::hick)) weights[ix(Door::sacrifice)] *= .5;
    if (state_.has(Gem::thirsty)) weights[ix(Door::cursed)] *= 2;
    if (state_.has(Gem::misadventurer)) weights[ix(Door::cursed)] *= .5;
    if (state_.run_number == 1 && state_.room < 25) {
        weights[ix(Door::cursed)] = 0; weights[ix(Door::sacrifice)] = 0;
    }
    if (std::accumulate(weights.begin(), weights.end(), 0.0) <= 0) weights[ix(Door::monster)] = 1;
    bool joint_sampled = false;
    if (profile_.door_pair_weights) {
        // Door::locked denotes the physical-family placeholder here, not the
        // selected style. Sampling families first avoids distorting measured
        // pair counts through sequential independent slot sampling.
        constexpr std::array<std::array<Door, 2>, 8> families{{
            {Door::locked, Door::mystery}, {Door::locked, Door::monster},
            {Door::mystery, Door::mystery}, {Door::locked, Door::golden},
            {Door::monster, Door::mystery}, {Door::golden, Door::mystery},
            {Door::golden, Door::monster}, {Door::golden, Door::golden}}};
        auto physical = weights;
        for (std::size_t i = 0; i < physical.size(); ++i)
            if (!door_type(static_cast<Door>(i))) physical[i] = 0;
        const double physical_total = std::accumulate(physical.begin(), physical.end(), 0.0);
        const double physical_factor = state_.has(Gem::pendant) ? 1 :
            (baseline_physical > 0 ? physical_total / baseline_physical : (physical_total > 0 ? 1 : 0));
        const double mystery_factor = (state_.has(Gem::greed) ? 2.0 : 1.0) * (state_.has(Gem::explorer) ? .5 : 1.0);
        auto pairs = *profile_.door_pair_weights;
        for (std::size_t i = 0; i < pairs.size(); ++i) {
            const bool has_physical = families[i][0] == Door::locked;
            if (state_.has(Gem::pendant) && !has_physical) pairs[i] = 0;
            if (has_physical) pairs[i] *= physical_factor;
            for (const auto kind : families[i]) if (kind == Door::mystery) pairs[i] *= mystery_factor;
        }
        if (std::accumulate(pairs.begin(), pairs.end(), 0.0) > 0) {
            auto selected = families[random_.weighted(Stream::doors, pairs.data(), pairs.size())];
            if (selected[0] == Door::locked)
                selected[0] = static_cast<Door>(random_.weighted(Stream::doors, physical.data(), physical.size()));
            state_.doors = {{{selected[0]}, {selected[1]}}};
            joint_sampled = true;
        }
    }
    if (!joint_sampled) {
        // Legacy profiles retain their exact draw sequence. Also a defensive
        // fallback if a sparse custom joint table has no compatible family
        // after progression/gem constraints (for example Pendant with only MM).
        auto first = weights;
        if (state_.has(Gem::pendant)) {
            for (std::size_t i = 0; i < first.size(); ++i)
                if (!door_type(static_cast<Door>(i))) first[i] = 0;
            if (std::accumulate(first.begin(), first.end(), 0.0) <= 0) first[ix(Door::locked)] = 1;
        }
        state_.doors[0] = {static_cast<Door>(random_.weighted(Stream::doors, first.data(), first.size()))};
        auto second = weights;
        for (std::size_t i = 0; i < second.size(); ++i)
            if (!compatible(state_.doors[0].kind, static_cast<Door>(i))) second[i] = 0;
        if (std::accumulate(second.begin(), second.end(), 0.0) <= 0)
            second[ix(state_.doors[0].kind == Door::monster ? Door::mystery : Door::monster)] = 1;
        state_.doors[1] = {static_cast<Door>(random_.weighted(Stream::doors, second.data(), second.size()))};
    }
    if (state_.doors[0].kind == Door::shop || state_.doors[1].kind == Door::shop) {
        state_.shops_seen[band(state_.room)] = true;
        state_.doors = {{{Door::shop}, {Door::wall}}}; return;
    }
    if (random_.chance(Stream::doors, .5)) std::swap(state_.doors[0], state_.doors[1]);
    ensure_open_path();
    // At most one trap; golden rooms are always safe from entry traps.
    const std::size_t first = random_.chance(Stream::doors, .5) ? 0 : 1;
    for (std::size_t side : {first, 1 - first}) {
        auto& door = state_.doors[side];
        if (trap_eligible(door.kind) && (state_.has(Gem::masochist) || random_.chance(Stream::doors, profile_.trap_chance))) {
            door.trap = true; door.cursed_trap = state_.has(Gem::gambler); break;
        }
    }
}
void Game::ensure_open_path() {
    // Synthetic safeguard, not a measured server rule. Recheck when death removes
    // a lockpick: doors generated before a lethal exit tick can become unaffordable.
    if (!available(state_.doors[0]) && !available(state_.doors[1])) state_.doors[1] = {Door::monster};
}
bool Game::opening_shop() const {
    return state_.phase == Phase::shop && state_.run_number == 1 && state_.room == 5;
}
void Game::generate_shop(bool curses) {
    state_.phase = curses ? Phase::curse_shop : Phase::shop;
    if (!curses) state_.shops_seen[band(state_.room)] = true;
    if (opening_shop() && (state_.shop_rerolls == 0 || profile_.opening_shop_rerolls == "fixed")) {
        state_.offers = {{{effect_template(EffectKind::one_hit, false), 0},
                         {effect_template(EffectKind::escape, false), 0}}};
        return;
    }
    // Player-observed: two different effect identities, with separate strengths.
    // Weighted sampling without replacement and independent strength rolls are
    // explicit distribution assumptions, not measured server probabilities.
    auto blessing_weights = profile_.shop_blessing_weights;
    auto curse_weights = profile_.shop_curse_weights;
    for (std::size_t i = 0; i < state_.offers.size(); ++i) {
        auto& offer = state_.offers[i];
        const auto kind = curses ? random_.weighted(Stream::shop, curse_weights.data(), curse_weights.size()) :
            random_.weighted(Stream::shop, blessing_weights.data(), blessing_weights.size());
        if (curses) curse_weights[kind] = 0; else blessing_weights[kind] = 0;
        const bool strong = random_.chance(Stream::shop, profile_.shop_strong_effect);
        offer.effect = effect_template(static_cast<EffectKind>(kind + (curses ? 8 : 0)), strong);
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
    auto pool = profile_.gems_for_run(state_.run_number);
    pool.erase(std::remove_if(pool.begin(), pool.end(), [this](Gem g) {
        return state_.has(g) || (profile_.exclude_offered_gems && state_.offered_gems[ix(g)]);
    }), pool.end());
    if (pool.size() < 3) throw std::logic_error("Insufficient distinct gem offers");
    for (std::size_t i = 0; i < 3; ++i) {
        const auto j = i + static_cast<std::size_t>(random_.unit(Stream::gems) * static_cast<double>(pool.size() - i));
        std::swap(pool[i], pool[j]); state_.gem_offers[i] = pool[i];
    }
    record_gem_menu(state_);
}
void Game::enter(DoorView door) {
    state_.flooded_seconds = 0;
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
    case Door::mystery: {
        // In a double-mystery pair either side has the same exact alternative.
        const auto other = state_.doors[state_.doors[0].kind == Door::mystery ? 1 : 0].kind;
        const bool beside_monster = other == Door::monster;
        auto weights = profile_.mystery_for_alternative(other);
        if (beside_monster) {
            // Player-confirmed pairing rule: no enemy on entry beside visible
            // monster eyes. A corpse remains eligible and may awaken on search.
            for (std::size_t i = 0; i < weights.size(); ++i)
                if (is_enemy(static_cast<Encounter>(i))) weights[i] = 0;
        }
        state_.encounter = std::accumulate(weights.begin(), weights.end(), 0.0) > 0 ? sample(weights) : Encounter::empty;
        break;
    }
    case Door::unlocked: state_.encounter = sample(profile_.mystery_weights); break;
    case Door::locked: case Door::double_locked: case Door::epic:
        state_.encounter = door.kind == Door::epic ? Encounter::epic : sample(profile_.locked_weights);
        if (state_.has(Gem::old_sacrifice) && random_.chance(Stream::contents, .2)) state_.encounter = Encounter::sacrifice_chest;
        if (state_.has(Gem::kidney) && random_.chance(Stream::contents, .2)) state_.encounter = Encounter::cursed_chest;
        if (state_.has(Gem::deceit) && random_.chance(Stream::contents, profile_.hidden_monster_chance)) state_.encounter = Encounter::monster;
        break;
    case Door::golden: {
        auto weights = profile_.golden_weights;
        if (state_.room < 90 || state_.room > 98) weights[ix(Encounter::armory)] = 0;
        state_.encounter = std::accumulate(weights.begin(), weights.end(), 0.0) > 0 ? sample(weights) : Encounter::empty;
        break;
    }
    case Door::shop:
        if (!(state_.run_number == 1 && state_.room == 5 && profile_.opening_shop_rerolls == "disabled"))
            ++state_.reroll_shop_visits;
        state_.encounter = Encounter::empty; generate_shop(false); break;
    case Door::cursed: state_.encounter = Encounter::cursed_chest; give(random_effect(true), true); break;
    case Door::sacrifice: state_.encounter = Encounter::sacrifice_chest; break;
    case Door::blessing: state_.encounter = Encounter::monster; give(random_effect(false), true); break;
    case Door::destiny:
        // Choosing the door includes its mandatory, key-free spin. The reward
        // and the room behind it are separate draws; neither completes the room.
        destiny_reward();
        state_.encounter = sample(profile_.destiny_weights);
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
    if (state_.room > 5 && door.trap && door.kind != Door::golden) {
        result("trap", door.cursed_trap || state_.has(Gem::gambler) ? "curse" : "damage");
        if (state_.effect(EffectKind::disarm)) { result("disarmed"); state_.blessings.consume(Clock::trap, state_.turn); }
        // Cursed traps inflict no direct HP loss. A granted poison effect can
        // deal its separate room-exit damage later, in finish_room().
        else if (door.cursed_trap || state_.has(Gem::gambler)) give(random_effect(true), true);
        else hurt(.1 * (state_.has(Gem::masochist) ? .8 : 1));
    }
}
double Game::battle_multiplier(bool fleeing) const {
    double multiplier = 1;
    if (state_.effect(EffectKind::broken_armor)) multiplier += .5;
    if (!fleeing || !profile_.fight_only_damage_stones) {
        if (state_.has(Gem::hero)) multiplier -= .2;
        if (state_.has(Gem::bull)) multiplier -= .2;
        if (state_.has(Gem::deceit)) multiplier -= .2;
        if (state_.has(Gem::rabbit)) multiplier += .25;
        if (state_.has(Gem::devil)) multiplier += .25;
    }
    if (fleeing) {
        if (state_.has(Gem::hick)) multiplier += .3;
        if (state_.has(Gem::pearl)) multiplier -= .4;
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
    double fight = profile_.monster_damage[band(state_.room)].high;
    double escape = profile_.escape_damage[band(state_.room)].high;
    if (state_.encounter == Encounter::undead || state_.encounter == Encounter::tube || state_.encounter == Encounter::beta) {
        fight *= .5; escape *= .5;
    }
    if (state_.encounter == Encounter::shakes) fight = escape = .3;
    if (state_.encounter == Encounter::valaraukar) fight = escape = .6;
    if (state_.encounter == Encounter::pig) fight = escape = .4;
    if (state_.encounter == Encounter::trial_monster) {
        const double multiplier = profile_.trial_multipliers.at(static_cast<std::size_t>(state_.trial_depth));
        fight *= multiplier; escape *= multiplier;
    }
    return std::max(fight * battle_multiplier(false), escape * battle_multiplier(true));
}
void Game::award_keys() {
    if (const auto* e = state_.effect(EffectKind::key_moment)) {
        if (random_.chance(Stream::keys, e->magnitude)) state_.keys += 2;
    } else {
        double p = profile_.key_chance + (state_.has(Gem::lodestone) ? .3 : 0)
            - (state_.has(Gem::spying) ? profile_.spying_key_penalty : 0);
        if (random_.chance(Stream::keys, p)) ++state_.keys;
    }
    state_.blessings.consume(Clock::fight, state_.turn);
}
void Game::fight(bool fleeing) {
    const auto encounter = state_.encounter;
    const bool boss = encounter == Encounter::boss;
    const bool one_hit = !boss && !fleeing && state_.effect(EffectKind::one_hit);
    if (fleeing && random_.chance(Stream::escape, flee_probability())) {
        result("escaped");
        if (state_.has(Gem::pendant) && random_.chance(Stream::keys, .4)) ++state_.keys;
        if (state_.has(Gem::pearl) && random_.chance(Stream::effects, .2)) give(random_effect(true));
        if (encounter == Encounter::trial_monster) ++state_.trial_depth;
        finish_room(); return;
    }
    if (fleeing) result("escape_failed");
    double damage = sample_damage(boss ? profile_.boss_damage[band(state_.room)] :
        (fleeing ? profile_.escape_damage[band(state_.room)] : profile_.monster_damage[band(state_.room)]));
    if (!boss) {
        if (encounter == Encounter::undead || encounter == Encounter::tube || encounter == Encounter::beta) damage *= .5;
        if (encounter == Encounter::shakes) damage = .3;
        if (encounter == Encounter::valaraukar) damage = .6;
        if (encounter == Encounter::pig) damage = .4;
        if (encounter == Encounter::trial_monster) damage *= profile_.trial_multipliers[static_cast<std::size_t>(state_.trial_depth)];
        damage *= battle_multiplier(fleeing);
        if (one_hit) damage = 0;
    }
    if (hurt(damage)) return;
    if (!fleeing) {
        result("defeated", name(encounter));
        if (boss) { if (state_.room != 100) ++state_.epics; }
        else {
            award_keys();
            if (encounter == Encounter::mimic) ++state_.epics;
            if (encounter == Encounter::pig) heal(.6);
            if (encounter == Encounter::tube) state_.lucky_coins += 10;
            if (encounter == Encounter::shakes) give(random_effect(false));
            if (encounter == Encounter::valaraukar) give({EffectKind::recovery, .1, 3, Clock::room});
            if (state_.has(Gem::kidney) && random_.chance(Stream::effects, .1)) give(random_effect(false));
            const double curse = profile_.combat_curse_chance + (state_.has(Gem::misadventurer) ? .1 : 0);
            if (!one_hit && state_.room > 25 && random_.chance(Stream::effects, curse)) {
                if (profile_.floor_specific_curses) {
                    constexpr EffectKind floor_curses[]{EffectKind::gold_hangover, EffectKind::poison, EffectKind::broken_armor};
                    const double strong = profile_.strong_effect + (state_.has(Gem::explorer) ? .2 : 0);
                    give(effect_template(floor_curses[band(state_.room) - 1], random_.chance(Stream::effects, strong)));
                } else give(random_effect(true));
            }
        }
    }
    if (encounter == Encounter::trial_monster) ++state_.trial_depth;
    finish_room();
}
double Game::sample_damage(Range range) {
    double u = random_.unit(Stream::damage);
    if (profile_.damage_distribution == "low") u = 0;
    else if (profile_.damage_distribution == "high") u = 1;
    else if (profile_.damage_distribution == "midpoint") u = .5;
    else if (profile_.damage_distribution == "triangular")
        u = u < .5 ? std::sqrt(u / 2) : 1 - std::sqrt((1 - u) / 2);
    else if (profile_.damage_distribution == "triangular_mean" && range.high > range.low) {
        // Fitted shape: E[X] = (low+mode+high)/3. The server distribution is unknown.
        const double mode = std::clamp((3*range.mean - 2*range.low - range.high) / (range.high-range.low), 0.0, 1.0);
        u = u < mode ? std::sqrt(u*mode) : 1 - std::sqrt((1-u)*(1-mode));
    }
    return range.low + (range.high - range.low) * u;
}
bool Game::hurt(double damage) {
    if (damage > 0) result("damage", "", std::min(state_.hp, damage));
    state_.hp = std::max(0.0, state_.hp - damage);
    if (state_.hp <= 1e-12) { state_.hp = 0; die(); return true; }
    return false;
}
void Game::heal(double fraction) {
    const double amount = std::min(1 - state_.hp, fraction * (state_.has(Gem::rusty) ? 1.4 : 1));
    result("healing", "", amount); state_.hp += amount;
}
void Game::die() {
    state_.hp = 0; ++state_.deaths;
    state_.blessings.clear(); state_.curses.clear();
    const bool in_trial = state_.trial_depth > 0 || state_.pending_trial_reward > 0 || state_.encounter == Encounter::trial_monster;
    if (in_trial) {
        state_.trial_depth = 0; state_.pending_trial_reward = 0;
    }
    // A lethal entry/combat consumes an ordinary room. An exit poison tick has
    // already committed it in finish_room(), so it must not advance a second time.
    // Bosses must still be defeated before a stone or completion is awarded.
    if (state_.phase == Phase::encounter && state_.room % 25 != 0) {
        ++state_.room; ++state_.turn; random_.room(state_.room);
        state_.encounter = Encounter::empty; state_.flooded_seconds = 0;
        state_.phase = Phase::doors; generate_doors();
    } else if (in_trial && state_.phase == Phase::doors) generate_doors();
    if (state_.phase == Phase::doors) ensure_open_path();
    resume_phase_ = state_.phase; state_.phase = Phase::recovery;
}
void Game::finish_room() {
    const bool boss = state_.room % 25 == 0;
    bool lethal_tick = false;
    if (!boss) {
        if (const auto* e = state_.effect(EffectKind::recovery)) heal(e->magnitude);
        if (const auto* e = state_.effect(EffectKind::poison)) {
            result("damage", "poison", std::min(state_.hp, e->magnitude));
            state_.hp = std::max(0.0, state_.hp - e->magnitude);
            lethal_tick = state_.hp <= 1e-12;
            if (lethal_tick) state_.hp = 0;
        }
    }
    state_.blessings.consume(Clock::room, state_.turn);
    state_.curses.consume(Clock::room, state_.turn);
    state_.flooded_seconds = 0;
    const int finished = state_.room++;
    ++state_.turn; random_.room(state_.room); state_.encounter = Encounter::empty;
    if (finished == 100) {
        state_.phase = Phase::complete; ++state_.legendaries; ++state_.completed_runs;
        state_.offered_gems.fill(false);
        if (state_.first_completion_hours < 0) state_.first_completion_hours = state_.elapsed_hours;
    }
    else if (boss) { state_.phase = Phase::gems; generate_gems(); }
    else { state_.phase = Phase::doors; generate_doors(); }
    // The completed room is committed before a lethal exit tick: no duplicate rewards.
    if (lethal_tick) die();
}
Effect Game::random_effect(bool curse, Stream stream) {
    const auto& curses = profile_.curse_weights;
    const auto& blessings = profile_.blessing_weights;
    const std::size_t kind = curse ? random_.weighted(stream, curses.data(), curses.size()) + 8 :
        random_.weighted(stream, blessings.data(), blessings.size());
    const double strong_chance = profile_.strong_effect +
        ((curse && state_.has(Gem::explorer)) ? .2 : 0) +
        ((!curse && state_.encounter == Encounter::barrel && state_.has(Gem::thirsty)) ? .4 : 0);
    const bool strong = random_.chance(stream, strong_chance);
    return effect_template(static_cast<EffectKind>(kind), strong);
}
void Game::give(Effect effect, bool immediately) {
    if (effect.kind == EffectKind::elixir) {
        result("blessing", name(effect.kind), effect.magnitude);
        heal(profile_.time_traveler_elixir_full_heal && state_.has(Gem::time_traveler) ? 1.0 : effect.magnitude);
        return;
    }
    // Native footage shows purchased and chest-acquired Recovery healing at
    // this room's exit (six uses become five), before the next decision.
    effect.starts_at = state_.turn + (immediately || effect.kind == EffectKind::recovery ? 0 : 1);
    if (is_curse(effect.kind)) {
        if (state_.has(Gem::moonstone)) ++effect.remaining;
        state_.curses.give(effect);
    } else {
        if (state_.has(Gem::time_traveler)) ++effect.remaining;
        state_.blessings.give(effect);
    }
    result(is_curse(effect.kind) ? "curse" : "blessing", name(effect.kind), effect.magnitude, effect.remaining);
}
void Game::random_reward(bool ordinary_container) {
    const auto empty_result = [this] {
        // First-run containers cannot be empty. Gold is a provisional fallback:
        // keep item/blessing probabilities intact until run-specific loot is measured.
        if (state_.run_number == 1) award_gold(true);
        else result("nothing");
    };
    if (ordinary_container && profile_.container_joint_weights) {
        const auto& weights = state_.has(Gem::greed) && profile_.container_joint_greed_weights ?
            *profile_.container_joint_greed_weights : *profile_.container_joint_weights;
        const auto outcome = random_.weighted(Stream::contents, weights.data(), weights.size());
        bool received = false;
        if ((outcome & 1) != 0) { give(random_effect(false)); received = true; }
        if ((outcome & 2) != 0 && (!state_.has(Gem::hero) || random_.chance(Stream::contents, .75))) {
            result("item"); received = true;
        }
        if ((outcome & 4) != 0) { award_gold(true); received = true; }
        if (!received) empty_result();
        return;
    }
    auto weights = profile_.container_weights;
    const double total = std::accumulate(weights.begin(), weights.end(), 0.0);
    // Player clarification: Greed unlocks blessings in this loot pool. An
    // awakened corpse goes through combat instead and never reaches this roll.
    // The eligible probability comes from the profile, not an invented bonus.
    const bool eligible = state_.has(Gem::greed) || !profile_.container_blessing_requires_greed;
    const double blessing = eligible ? weights[0] / total : 0;
    if (random_.chance(Stream::effects, blessing)) { give(random_effect(false)); return; }
    weights[0] = 0;
    if (std::accumulate(weights.begin(), weights.end(), 0.0) == 0) { empty_result(); return; }
    const auto outcome = random_.weighted(Stream::contents, weights.data(), weights.size());
    if (outcome == 2) award_gold(true);
    else if (outcome == 1 && (!state_.has(Gem::hero) || random_.chance(Stream::contents, .75))) result("item");
    else empty_result();
    // Ordinary items are deliberately not valued in the completion objective.
}
void Game::destiny_reward() {
    const auto& weights = profile_.destiny_reward_weights;
    const auto& amounts = profile_.destiny_reward_amounts;
    switch (random_.weighted(Stream::special, weights.data(), weights.size())) {
    case 0: give(random_effect(false), true); break;
    case 1: award_gold(false, amounts[0]); break;
    case 2: state_.lucky_coins += static_cast<int>(amounts[1]); break;
    case 3: state_.resources[5] += static_cast<int>(amounts[2]); break;
    case 4: state_.mushrooms_found += static_cast<int>(amounts[3]); break;
    case 5: give(random_effect(true), true); break;
    default: throw std::logic_error("Invalid destiny reward");
    }
}
void Game::award_gold(bool chest, double units) {
    if (chest) {
        if (const auto* e = state_.effect(EffectKind::raider)) units *= 1 + e->magnitude;
        if (const auto* e = state_.effect(EffectKind::gold_hangover)) units *= 1 - e->magnitude;
    }
    ++state_.gold_rewards;
    state_.gold_units += units;
    result("gold", "", units);
}
void Game::interact() {
    const auto encounter = state_.encounter;
    switch (encounter) {
    case Encounter::barrel: {
        ++state_.barrels_opened;
        const double p = barrel_blessing_chance(profile_, state_);
        give(random_effect(!random_.chance(Stream::effects, p))); break;
    }
    case Encounter::crate: case Encounter::silver: case Encounter::bronze: random_reward(true); break;
    case Encounter::skeleton:
        if (random_.chance(Stream::contents, profile_.skeleton_wakes)) { result("awakened"); state_.encounter = Encounter::monster; return; }
        random_reward(); break;
    case Encounter::epic: case Encounter::locker:
        if (!state_.has(Gem::hero) || random_.chance(Stream::contents, .75)) ++state_.epics;
        else if (state_.run_number == 1) award_gold(true);
        break;
    case Encounter::armory:
        if (profile_.armory_bonus_eligible && random_.chance(Stream::contents, profile_.armory_legendary_chance)) ++state_.legendaries;
        else ++state_.epics;
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
        if (state_.pending_trial_reward == 5 && profile_.trial_reward_weights_5) {
            const auto& weights = *profile_.trial_reward_weights_5;
            switch (random_.weighted(Stream::contents, weights.data(), weights.size())) {
            case 0: award_gold(true); break;
            case 1: ++state_.resources[4]; break;
            case 2: ++state_.epics; break;
            case 3: ++state_.legendaries; break;
            default: throw std::logic_error("Invalid tier-five arena reward");
            }
        }
        else if (state_.pending_trial_reward >= 1 && state_.pending_trial_reward <= 5 &&
            random_.chance(Stream::contents, profile_.trial_legendary_chances[static_cast<std::size_t>(state_.pending_trial_reward - 1)])) ++state_.legendaries;
        else if (state_.pending_trial_reward >= 2) ++state_.epics;
        else award_gold(true);
        state_.pending_trial_reward = 0; break;
    case Encounter::fountain: heal(.25); break;
    case Encounter::cleansing_fountain: result("cleansed", "", state_.curses.size); state_.curses.clear(); heal(.25); break;
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
        const int outcome = static_cast<int>(random_.unit(Stream::special) * 3);
        result("rps", outcome == 0 ? "draw" : outcome == 1 ? "win" : "loss");
        if (outcome == 1) { give(random_effect(false)); if (profile_.rps_epic_reward) ++state_.epics; }
        if (outcome == 2) { if (hurt(.1)) return; give(random_effect(true)); }
        break;
    }
    case Encounter::sewers: ++state_.epics; break;
    case Encounter::auction:
        if (random_.chance(Stream::contents, profile_.auction_epic_chance)) ++state_.epics;
        break;
    case Encounter::sarcophagus: award_gold(true); break;
    case Encounter::locked_sarcophagus:
        state_.keys -= door_cost(Door::locked); state_.blessings.consume(Clock::door, state_.turn); ++state_.epics; break;
    case Encounter::wheel: {
        const int outcome = static_cast<int>(random_.unit(Stream::special) * 5);
        if (outcome == 0) give(random_effect(false));
        if (outcome == 1) give(random_effect(true));
        if (outcome == 2) ++state_.keys;
        if (outcome == 3) state_.keys = std::max(0, state_.keys - 1);
        if (outcome == 4) award_gold(false);
        break;
    }
    case Encounter::spider_legs: case Encounter::spider_head: case Encounter::spider_full: {
        const double success = profile_.spider_success[ix(encounter) - ix(Encounter::spider_legs)];
        if (random_.chance(Stream::special, success)) { result("spider", "success"); state_.keys += encounter == Encounter::spider_legs ? 1 : (encounter == Encounter::spider_head ? 2 : 5); }
        else { result("spider", "bite"); if (hurt(profile_.spider_bite_damage)) return; give({EffectKind::poison, .05, 5, Clock::room}); }
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
