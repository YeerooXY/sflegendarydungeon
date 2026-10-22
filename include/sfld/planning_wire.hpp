#pragma once
#include "sfld/engine.hpp"
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <type_traits>

namespace sfld {
// Versioned, bounded, RNG-free transport between the interactive process and
// the planning library. Every State/Limits field is explicit. Last-action
// presentation outcomes are intentionally omitted; they do not affect play.
namespace planning_wire_detail {
struct Writer {
    std::ostream& stream;
    template<class T> void operator()(T& value) {
        if constexpr (std::is_enum_v<T>) stream << static_cast<int>(value) << ' ';
        else stream << value << ' ';
    }
};
struct Reader {
    std::istream& stream;
    template<class T> void operator()(T& value) {
        if constexpr (std::is_enum_v<T>) {
            int raw = 0; stream >> raw; value = static_cast<T>(raw);
        } else stream >> value;
        if (!stream) throw std::invalid_argument("Malformed planning state");
    }
};
template<class Archive> void effect(Archive& a, Effect& e) {
    a(e.kind); a(e.magnitude); a(e.remaining); a(e.clock); a(e.starts_at);
}
template<class Archive> void fields(Archive& a, PlanningSnapshot& snapshot) {
    auto& s = snapshot.state; auto& l = snapshot.limits;
    a(s.phase); a(s.room); a(s.turn); a(s.run_number); a(s.hp); a(s.keys);
    for (auto& v : s.resources) a(v);
    for (auto& v : s.gems) a(v);
    for (auto& v : s.offered_gems) a(v);
    for (auto* effects : {&s.blessings, &s.curses}) {
        a(effects->size); for (auto& e : effects->slots) effect(a, e);
    }
    for (auto& d : s.doors) { a(d.kind); a(d.trap); a(d.cursed_trap); }
    a(s.encounter);
    for (auto& o : s.offers) { effect(a, o.effect); a(o.keys); }
    for (auto& v : s.gem_offers) a(v);
    a(s.trial_depth); a(s.trial_seen); a(s.donated_resource); a(s.pending_trial_reward);
    a(s.deaths); a(s.paid_steps); a(s.mushrooms); a(s.mushrooms_found);
    a(s.recovery_mushrooms); a(s.reroll_mushrooms); a(s.shop_rerolls);
    a(s.reroll_shop_visits); a(s.reroll_bank_used);
    for (auto& v : s.shops_seen) a(v);
    a(s.shop_purchases); a(s.strong_one_hit_purchases); a(s.epics); a(s.legendaries);
    a(s.gold_rewards); a(s.gold_units); a(s.lucky_coins); a(s.barrels_opened); a(s.barrels_skipped);
    a(s.actions); a(s.elapsed_hours); a(s.active_hours); a(s.run_start_hours); a(s.run_start_mushrooms);
    a(s.flooded_seconds); a(s.completed_runs); a(s.first_completion_hours);
    a(l.budget); a(l.deadline_hours); a(l.max_actions); a(l.run_number); a(l.repeat_runs);
    a(l.restart_health); a(l.sleep_until_hour); a(l.event_start_hour);
    a(snapshot.recovery_destination);
}
}
inline std::string planning_state_text(const Game& game) {
    std::ostringstream stream; stream.imbue(std::locale::classic()); stream << std::setprecision(17);
    stream << "sfld-planning-v1 " << engine_version << ' ' << game.profile().fingerprint << ' ';
    auto snapshot = game.planning_snapshot();
    planning_wire_detail::Writer writer{stream}; planning_wire_detail::fields(writer, snapshot);
    return stream.str();
}
inline PlanningSnapshot parse_planning_state(const Profile& profile, std::string_view text) {
    if (text.size() > 65536) throw std::invalid_argument("Planning state exceeds 64 KiB");
    std::istringstream stream{std::string(text)}; stream.imbue(std::locale::classic());
    std::string format, version, fingerprint;
    if (!(stream >> format >> version >> fingerprint) || format != "sfld-planning-v1" ||
        version != engine_version || fingerprint != profile.fingerprint)
        throw std::invalid_argument("Planning state format, engine or profile mismatch");
    PlanningSnapshot snapshot;
    planning_wire_detail::Reader reader{stream}; planning_wire_detail::fields(reader, snapshot);
    stream >> std::ws;
    if (!stream.eof()) throw std::invalid_argument("Extra planning state fields");
    // The native constructor validates counters, effects, enum ranges, limits,
    // schedule and accessible doors before any imported state can be restored.
    Game checked(profile, 0, snapshot);
    return snapshot;
}
}
