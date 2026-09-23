#include "sfld/model.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace sfld {
#define NAMES(TYPE, ...) \
std::string_view name(TYPE value) { \
    static constexpr std::string_view names[] = {__VA_ARGS__}; \
    return ix(value) < std::size(names) ? names[ix(value)] : "invalid"; \
}
NAMES(Phase, "doors", "encounter", "shop", "curse_shop", "gems", "recovery", "complete")
NAMES(Door, "monster", "mystery", "locked", "double_locked", "unlocked", "epic", "golden", "shop",
    "cursed", "sacrifice", "blessing", "destiny", "wood", "stone", "souls", "metal", "arcane",
    "hourglasses", "trial", "exit_trial", "boss", "wall")
NAMES(Encounter, "monster", "boss", "barrel", "crate", "silver", "bronze", "epic", "skeleton",
    "mimic", "cursed_chest", "sacrifice_chest", "sated_chest", "prize", "empty", "fountain",
    "cleansing_fountain", "rocks", "lava", "narrator", "flooded", "wishing_well", "rps", "sewers",
    "undead", "sarcophagus", "locked_sarcophagus", "wood", "wheel", "spider_legs", "spider_head",
    "spider_full", "souls", "arcane", "curse_shop", "armory", "locker", "tube", "beta", "shakes",
    "valaraukar", "auction", "rainbow", "pig", "trial_monster")
NAMES(Gem, "rabbit", "moonstone", "spying", "pendant", "greasy", "gambler", "greed", "hero",
    "time_traveler", "thirsty", "pearl", "blood", "explorer", "misadventurer", "devil", "deceit",
    "lodestone", "bull", "hick", "rusty", "masochist", "old_sacrifice", "kidney")
NAMES(EffectKind, "raider", "one_hit", "escape", "disarm", "lockpick", "key_moment", "elixir",
    "recovery", "broken_armor", "poison", "clumsy", "gold_hangover", "hard_lock")
NAMES(ActionKind, "choose_door", "fight", "flee", "interact", "skip", "buy", "reroll", "choose_gem",
    "wait", "heal_step", "heal_full", "reenter", "rps", "linger")
#undef NAMES
template<class T> T parse_enum(std::string_view value, std::size_t count) {
    for (std::size_t i = 0; i < count; ++i) {
        const auto e = static_cast<T>(i);
        if (name(e) == value) return e;
    }
    throw std::invalid_argument("Unknown identifier: " + std::string(value));
}
Door door_from(std::string_view s) { return parse_enum<Door>(s, ix(Door::count)); }
Encounter encounter_from(std::string_view s) { return parse_enum<Encounter>(s, ix(Encounter::count)); }
Gem gem_from(std::string_view s) { return parse_enum<Gem>(s, ix(Gem::count)); }
ActionKind action_from(std::string_view s) { return parse_enum<ActionKind>(s, 14); }
bool is_curse(EffectKind k) { return k >= EffectKind::broken_armor; }
bool is_enemy(Encounter e) {
    switch (e) {
    case Encounter::monster: case Encounter::boss: case Encounter::mimic: case Encounter::undead: case Encounter::tube:
    case Encounter::beta: case Encounter::shakes: case Encounter::valaraukar: case Encounter::pig:
    case Encounter::trial_monster: return true;
    default: return false;
    }
}
bool is_optional(Encounter e) {
    return e == Encounter::mimic || (!is_enemy(e) && e != Encounter::lava && e != Encounter::rocks && e != Encounter::wood);
}
const Effect* Effects::find(EffectKind k, int turn) const {
    for (int i = 0; i < size; ++i) {
        const auto& e = slots[static_cast<std::size_t>(i)];
        if (e.kind == k && e.remaining > 0 && e.starts_at <= turn) return &e;
    }
    return nullptr;
}
void Effects::give(Effect effect) {
    if (effect.remaining <= 0) return;
    for (int i = 0; i < size; ++i) {
        if (slots[static_cast<std::size_t>(i)].kind == effect.kind) {
            slots[static_cast<std::size_t>(i)] = effect;
            return;
        }
    }
    if (size == 3) { slots[0] = slots[1]; slots[1] = slots[2]; --size; }
    slots[static_cast<std::size_t>(size++)] = effect;
}
void Effects::consume(Clock clock, int turn) {
    int write = 0;
    for (int i = 0; i < size; ++i) {
        auto effect = slots[static_cast<std::size_t>(i)];
        if (effect.clock == clock && effect.starts_at <= turn) --effect.remaining;
        if (effect.remaining > 0) slots[static_cast<std::size_t>(write++)] = effect;
    }
    size = write;
}
const Effect* State::effect(EffectKind k) const {
    return (is_curse(k) ? curses : blessings).find(k, turn);
}
std::uint64_t Random::mix(std::uint64_t value) {
    value += UINT64_C(0x9e3779b97f4a7c15);
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
}
double Random::unit(Stream stream) {
    auto& counter = counters_[ix(stream)];
    const auto bits = mix(seed_ ^ mix(static_cast<std::uint64_t>(room_))
        ^ mix(ix(stream) + UINT64_C(0x100000000)) ^ mix(counter++ + UINT64_C(0x200000000)));
    return static_cast<double>(bits >> 11) * (1.0 / 9007199254740992.0);
}
bool Random::chance(Stream stream, double p) { return unit(stream) < std::clamp(p, 0.0, 1.0); }
double Random::uniform(Stream stream, Range r) { return r.low + (r.high - r.low) * unit(stream); }
std::size_t Random::weighted(Stream stream, const double* weights, std::size_t count) {
    double total = 0;
    for (std::size_t i = 0; i < count; ++i) total += weights[i];
    if (!(total > 0) || !std::isfinite(total)) throw std::invalid_argument("Empty/invalid outcome distribution");
    double draw = unit(stream) * total;
    std::size_t last = 0;
    for (std::size_t i = 0; i < count; ++i) {
        if (weights[i] > 0) last = i;
        draw -= weights[i];
        if (draw < 0) return i;
    }
    return last;
}
std::string state_digest(const State& s) {
    std::uint64_t h = UINT64_C(14695981039346656037);
    const auto add = [&h](std::uint64_t n) { h ^= Random::mix(n); h *= UINT64_C(1099511628211); };
    const auto real = [&add](double n) { add(std::bit_cast<std::uint64_t>(n)); };
    add(ix(s.phase)); add(static_cast<std::uint64_t>(s.room)); add(static_cast<std::uint64_t>(s.turn));
    add(static_cast<std::uint64_t>(s.run_number)); real(s.hp); add(static_cast<std::uint64_t>(s.keys));
    for (auto n : s.resources) add(static_cast<std::uint64_t>(n));
    for (bool g : s.gems) add(g);
    const auto effect = [&add, &real](const Effect& e) {
        add(ix(e.kind)); real(e.magnitude); add(static_cast<std::uint64_t>(e.remaining));
        add(ix(e.clock)); add(static_cast<std::uint64_t>(e.starts_at));
    };
    for (const auto* set : {&s.blessings, &s.curses}) {
        add(static_cast<std::uint64_t>(set->size));
        for (int i = 0; i < set->size; ++i) effect(set->slots[static_cast<std::size_t>(i)]);
    }
    for (auto d : s.doors) { add(ix(d.kind)); add(d.trap); add(d.cursed_trap); }
    add(ix(s.encounter));
    for (auto o : s.offers) { effect(o.effect); add(static_cast<std::uint64_t>(o.keys)); }
    for (auto g : s.gem_offers) add(ix(g));
    for (int n : {s.trial_depth, int(s.trial_seen), s.donated_resource, s.pending_trial_reward,
        s.deaths, s.paid_steps, s.mushrooms, s.recovery_mushrooms, s.reroll_mushrooms,
        s.shop_rerolls, s.epics, s.legendaries, s.gold_rewards, s.barrels_opened, s.barrels_skipped})
        add(static_cast<std::uint64_t>(n));
    add(s.actions); real(s.elapsed_hours); real(s.active_hours);
    std::ostringstream out; out << std::hex << h; return out.str();
}
std::string json_string(std::string_view text) {
    std::ostringstream out; out << '"';
    for (unsigned char ch : text) {
        if (ch == '"' || ch == '\\') out << '\\' << ch;
        else if (ch < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(ch) << std::dec;
        else out << ch;
    }
    out << '"'; return out.str();
}
}
