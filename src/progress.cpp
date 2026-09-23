#include "sfld/progress.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace sfld {
namespace {
std::string trim(std::string_view value) {
    const auto start = value.find_first_not_of(" \t\r\n");
    if (start == std::string_view::npos) return {};
    return std::string(value.substr(start, value.find_last_not_of(" \t\r\n") - start + 1));
}
std::vector<std::string> split(std::string_view text, char delimiter = ',') {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start <= text.size()) {
        const auto end = text.find(delimiter, start);
        out.push_back(trim(text.substr(start, end == std::string_view::npos ? end : end - start)));
        if (end == std::string_view::npos) break;
        start = end + 1;
    }
    return out;
}
double number(const std::string& text) {
    double value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size() || !std::isfinite(value))
        throw std::invalid_argument("Invalid progress number: " + text);
    return value;
}
int integer(const std::string& text, int low, int high) {
    const double value = number(text);
    if (value < low || value > high || std::floor(value) != value)
        throw std::invalid_argument("Progress integer out of range: " + text);
    return static_cast<int>(value);
}
bool boolean(const std::string& text) {
    if (text == "true") return true;
    if (text == "false") return false;
    throw std::invalid_argument("Expected true/false in progress: " + text);
}
Effect read_effect(const std::vector<std::string>& parts, int turn) {
    if (parts.size() < 3 || parts.size() > 4)
        throw std::invalid_argument("Effect syntax: kind:weak|strong:remaining[:delay]");
    if (parts[1] != "weak" && parts[1] != "strong") throw std::invalid_argument("Effect strength must be weak or strong");
    auto effect = effect_template(effect_from(parts[0]), parts[1] == "strong");
    effect.remaining = integer(parts[2], 1, 1000);
    effect.starts_at = turn + (parts.size() == 4 ? integer(parts[3], 0, 1) : 0);
    return effect;
}
std::string effect_text(const Effect& effect, int turn, bool delay = true) {
    const auto weak = effect_template(effect.kind, false);
    std::string out = std::string(name(effect.kind)) + (effect.magnitude == weak.magnitude ? ":weak:" : ":strong:")
        + std::to_string(effect.remaining);
    if (delay && effect.starts_at > turn) out += ":" + std::to_string(effect.starts_at - turn);
    return out;
}
void check_effect(const Effect& e, int turn) {
    if (ix(e.kind) >= ix(EffectKind::count)) throw std::invalid_argument("Invalid progress effect");
    const auto weak = effect_template(e.kind, false), strong = effect_template(e.kind, true);
    if (e.clock != weak.clock || (e.magnitude != weak.magnitude && e.magnitude != strong.magnitude) ||
        e.remaining < 1 || e.remaining > 1000 || e.starts_at < turn || e.starts_at > turn + 1)
        throw std::invalid_argument("Invalid effect strength, counter or activation delay");
}
}

StartState StartState::load(const std::string& path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) throw std::runtime_error("Cannot read progress: " + path);
    if (file.tellg() > 65536) throw std::invalid_argument("Progress file exceeds 64 KiB");
    file.seekg(0);
    return parse(std::string((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>()));
}
StartState StartState::parse(std::string_view text) {
    if (text.size() > 65536) throw std::invalid_argument("Progress exceeds 64 KiB");
    if (text.starts_with("\xef\xbb\xbf")) text.remove_prefix(3);
    std::map<std::string, std::string> fields;
    std::istringstream input{std::string(text)}; std::string line;
    while (std::getline(input, line)) {
        line = trim(line.substr(0, line.find('#')));
        if (line.empty()) continue;
        const auto equals = line.find('=');
        if (equals == std::string::npos) throw std::invalid_argument("Progress expects key=value: " + line);
        const auto key = trim(line.substr(0, equals));
        if (!fields.emplace(key, trim(line.substr(equals + 1))).second)
            throw std::invalid_argument("Duplicate progress field: " + key);
    }
    const auto take = [&fields](const std::string& key, std::optional<std::string> fallback = {}) {
        const auto it = fields.find(key);
        if (it == fields.end()) {
            if (fallback) return *fallback;
            throw std::invalid_argument("Missing progress field: " + key);
        }
        auto value = it->second; fields.erase(it); return value;
    };
    if (take("schema") != "1") throw std::invalid_argument("Unsupported progress schema");
    StartState start; auto& s = start.state;
    s.room = integer(take("room"), 1, 100); s.turn = s.room - 1;
    s.run_number = integer(take("run_number"), 1, 1000000);
    s.phase = phase_from(take("phase"));
    // Fraction form is used in canonical traces to avoid rounding through *100
    // and /100 when replaying an arbitrary binary floating-point HP value.
    s.hp = fields.contains("hp_fraction") ? number(take("hp_fraction")) : number(take("hp_percent")) / 100;
    s.keys = integer(take("keys"), 0, 1000000);
    const auto gems = take("gems", "none");
    if (gems != "none") for (const auto& text_gem : split(gems)) {
        const auto gem = gem_from(text_gem);
        if (s.has(gem)) throw std::invalid_argument("Duplicate held gem: " + text_gem);
        s.gems[ix(gem)] = true;
    }
    for (auto [key, effects] : {std::pair{"blessings", &s.blessings}, std::pair{"curses", &s.curses}}) {
        const auto value = take(key, "none");
        if (value == "none") continue;
        std::set<EffectKind> seen;
        for (const auto& item : split(value)) {
            auto e = read_effect(split(item, ':'), s.turn);
            if (effects->size == 3 || !seen.insert(e.kind).second || is_curse(e.kind) != (effects == &s.curses) || e.kind == EffectKind::elixir)
                throw std::invalid_argument("Progress needs at most three distinct lasting effects in each category");
            effects->give(e);
        }
    }
    const auto resources = split(take("resources", "0,0,0,0,0,0"));
    if (resources.size() != s.resources.size()) throw std::invalid_argument("resources needs six donation-unit balances");
    for (std::size_t i = 0; i < resources.size(); ++i) s.resources[i] = integer(resources[i], 0, 1000000);
    s.paid_steps = integer(take("paid_heals", "0"), 0, 1000000);
    s.shop_rerolls = integer(take("shop_rerolls", "0"), 0, 1000000);
    s.trial_seen = boolean(take("trial_seen", "false"));
    s.trial_depth = integer(take("trial_depth", "0"), 0, 5);
    s.pending_trial_reward = integer(take("pending_trial_reward", "0"), 0, 5);
    s.donated_resource = integer(take("donated_resource", "-1"), -1, 5);
    if (s.phase == Phase::recovery) start.resume_phase = phase_from(take("resume_phase"));
    const auto phase = start.decision_phase();
    if (phase == Phase::doors) {
        const auto doors = take("doors", "unknown");
        start.generate_current = doors == "unknown";
        if (!start.generate_current) {
            const auto parts = split(doors);
            if (parts.size() != 2) throw std::invalid_argument("doors needs exactly two positions; use wall for a bricked-up door");
            for (std::size_t i = 0; i < 2; ++i) {
                const auto door = split(parts[i], ':');
                if (door.size() > 2) throw std::invalid_argument("Door syntax: kind[:trap|cursed_trap]");
                s.doors[i].kind = door_from(door[0]);
                if (door.size() == 2) {
                    if (door[1] != "trap" && door[1] != "cursed_trap") throw std::invalid_argument("Unknown trap type");
                    s.doors[i].trap = true; s.doors[i].cursed_trap = door[1] == "cursed_trap";
                }
            }
        }
    } else if (phase == Phase::encounter) {
        start.generate_current = false;
        s.encounter = encounter_from(take("encounter"));
    } else if (phase == Phase::shop || phase == Phase::curse_shop) {
        const auto offers = take("offers", "unknown");
        start.generate_current = offers == "unknown";
        if (!start.generate_current) {
            const auto parts = split(offers);
            if (parts.size() != 2) throw std::invalid_argument("offers needs two effect:strength:remaining:keys entries");
            for (std::size_t i = 0; i < 2; ++i) {
                auto offer = split(parts[i], ':');
                if (offer.size() != 4) throw std::invalid_argument("Offer syntax: effect:weak|strong:remaining:keys");
                s.offers[i].keys = integer(offer.back(), 0, 1000000); offer.pop_back();
                s.offers[i].effect = read_effect(offer, s.turn);
            }
        }
    } else if (phase == Phase::gems) {
        const auto offers = take("gem_offers", "unknown");
        start.generate_current = offers == "unknown";
        if (!start.generate_current) {
            const auto parts = split(offers);
            if (parts.size() != 3) throw std::invalid_argument("gem_offers needs exactly three choices");
            for (std::size_t i = 0; i < 3; ++i) s.gem_offers[i] = gem_from(parts[i]);
        }
    } else throw std::invalid_argument("Unsupported progress phase");
    if (!fields.empty()) throw std::invalid_argument("Unknown or inapplicable progress field: " + fields.begin()->first);
    return start;
}

void StartState::validate(const Profile& profile) const {
    const auto& s = state;
    const auto phase = decision_phase();
    if (s.room < 1 || s.room > 100 || s.turn != s.room - 1 || s.run_number < 1 || s.run_number > 1000000 ||
        !std::isfinite(s.hp) || s.hp < 0 || s.hp > 1 || (s.hp == 0 && s.phase != Phase::recovery) ||
        s.keys < 0 || s.keys > 1000000 || s.paid_steps < 0 || s.paid_steps > 1000000 ||
        s.shop_rerolls < 0 || s.shop_rerolls > 1000000 || ix(s.phase) > ix(Phase::recovery) ||
        (s.phase == Phase::recovery && phase != Phase::doors && phase != Phase::encounter && phase != Phase::shop && phase != Phase::curse_shop))
        throw std::invalid_argument("Invalid progress phase, room, run number, HP or counter");
    // Historical result counters are intentionally not part of this input format.
    if (s.elapsed_hours != 0 || s.active_hours != 0 || s.actions != 0 || s.mushrooms != 0 || s.recovery_mushrooms != 0 ||
        s.reroll_mushrooms != 0 || s.deaths != 0 || s.barrels_opened != 0 || s.barrels_skipped != 0 ||
        s.epics != 0 || s.legendaries != 0 || s.gold_rewards != 0)
        throw std::invalid_argument("Progress result counters must start at zero; budgets and time are measured from now");
    for (int resource : s.resources) if (resource < 0 || resource > 1000000) throw std::invalid_argument("Invalid resource balance");
    const auto count = std::count(s.gems.begin(), s.gems.end(), true);
    const int expected = (s.room - 1) / 25 - (phase == Phase::gems ? 1 : 0);
    if (count != expected) throw std::invalid_argument("Held gem count does not match room/phase; gems phase uses room 26, 51 or 76 before picking");
    if (phase == Phase::gems && s.room != 26 && s.room != 51 && s.room != 76)
        throw std::invalid_argument("Gem selection must be at room 26, 51 or 76");
    const auto& pool = profile.gems_for_run(s.run_number);
    // Held/explicitly observed gems can be outside the future pool, e.g. event data
    // has changed. Preserve observations; use the profile only for unknown offers.
    const auto available = std::count_if(pool.begin(), pool.end(), [&s](Gem g) { return !s.has(g); });
    if ((s.room <= 75 || phase == Phase::gems) && available < 3)
        throw std::invalid_argument("Too few unowned gems in the selected run's pool");
    for (const auto* effects : {&s.blessings, &s.curses}) {
        if (effects->size < 0 || effects->size > 3) throw std::invalid_argument("Too many effect slots");
        std::set<EffectKind> kinds;
        for (int i = 0; i < effects->size; ++i) {
            const auto& e = effects->slots[static_cast<std::size_t>(i)]; check_effect(e, s.turn);
            if (e.kind == EffectKind::elixir || is_curse(e.kind) != (effects == &s.curses) || !kinds.insert(e.kind).second)
                throw std::invalid_argument("Duplicate effect or wrong blessing/curse category");
        }
    }
    if (s.phase == Phase::recovery && (s.blessings.size || s.curses.size))
        throw std::invalid_argument("Effects are cleared on death; enter re-entry effects after reentering");
    if (s.trial_depth < 0 || s.trial_depth > 5 || s.pending_trial_reward < 0 || s.pending_trial_reward > 5 ||
        s.donated_resource < -1 || s.donated_resource > 5 ||
        ((s.trial_depth > 0 || s.pending_trial_reward > 0) && (!s.trial_seen || s.room <= 25)) ||
        (s.phase == Phase::recovery && s.trial_depth > 0))
        throw std::invalid_argument("Invalid trial progress or donated resource");
    if (s.trial_depth > 0 && phase != Phase::doors && !(phase == Phase::encounter && s.encounter == Encounter::trial_monster))
        throw std::invalid_argument("An active trial requires trial doors or a trial monster");
    if (s.pending_trial_reward > 0 && !(phase == Phase::encounter && s.encounter == Encounter::prize))
        throw std::invalid_argument("A pending trial reward requires the prize encounter");
    if (s.room % 25 == 0 && phase != Phase::doors && phase != Phase::encounter)
        throw std::invalid_argument("Boss rooms must have boss doors or the boss encounter");
    if (phase == Phase::doors && !generate_current) {
        int bosses = 0, walls = 0;
        for (auto d : s.doors) {
            if (ix(d.kind) >= ix(Door::count) || (d.cursed_trap && !d.trap) ||
                ((d.kind == Door::wall || d.kind == Door::boss) && d.trap)) throw std::invalid_argument("Invalid observed door/trap");
            bosses += d.kind == Door::boss ? 1 : 0; walls += d.kind == Door::wall ? 1 : 0;
            if (d.kind == Door::exit_trial && s.trial_depth == 0) throw std::invalid_argument("Trial exit requires active trial progress");
            if (d.kind == Door::trial && (s.room <= 25 || s.trial_depth >= 5 || (s.trial_seen && s.trial_depth == 0)))
                throw std::invalid_argument("Trial door conflicts with room/trial history");
            if (s.trial_depth > 0 && d.kind != Door::trial && d.kind != Door::exit_trial && d.kind != Door::wall)
                throw std::invalid_argument("An active trial offers trial/exit doors");
        }
        if ((s.room % 25 == 0 && (bosses != 1 || walls != 1)) || (s.room % 25 != 0 && bosses != 0))
            throw std::invalid_argument("Boss doors belong at rooms 25/50/75/100, beside a wall");
    }
    if (phase == Phase::encounter) {
        if (generate_current || ix(s.encounter) >= ix(Encounter::count) || s.encounter == Encounter::curse_shop ||
            ((s.room % 25 == 0) != (s.encounter == Encounter::boss)))
            throw std::invalid_argument("Invalid revealed encounter; use curse_shop phase for that shop");
        if (s.encounter == Encounter::trial_monster && (!s.trial_seen || s.room <= 25 || s.trial_depth >= 5 || s.phase == Phase::recovery))
            throw std::invalid_argument("Invalid trial encounter; death resumes at normal doors");
        if (s.encounter == Encounter::sated_chest && s.donated_resource < 0)
            throw std::invalid_argument("A sated chest needs donated_resource (0..5)");
    }
    if ((phase == Phase::shop || phase == Phase::curse_shop) && !generate_current) for (const auto& offer : s.offers) {
        check_effect(offer.effect, s.turn);
        if (is_curse(offer.effect.kind) != (phase == Phase::curse_shop) || offer.keys < 0 || offer.keys > 1000000 ||
            offer.effect.starts_at != s.turn)
            throw std::invalid_argument("Invalid observed shop offer");
    }
    if (phase == Phase::gems && !generate_current) {
        std::set<Gem> seen;
        for (Gem g : s.gem_offers) if (ix(g) >= ix(Gem::count) || s.has(g) || !seen.insert(g).second)
            throw std::invalid_argument("Gem offers must be three distinct unowned gems");
    }
}

std::string StartState::encode() const {
    const auto& s = state;
    std::ostringstream out; out << std::setprecision(17);
    out << "schema=1\nroom=" << s.room << "\nrun_number=" << s.run_number << "\nphase=" << name(s.phase)
        << "\nhp_fraction=" << s.hp << "\nkeys=" << s.keys << "\ngems=";
    bool first = true;
    for (std::size_t i = 0; i < s.gems.size(); ++i) if (s.gems[i]) {
        if (!first) out << ',';
        out << name(static_cast<Gem>(i)); first = false;
    }
    if (first) out << "none";
    for (auto [key, effects] : {std::pair{"blessings", &s.blessings}, std::pair{"curses", &s.curses}}) {
        out << '\n' << key << '=';
        if (!effects->size) out << "none";
        for (int i = 0; i < effects->size; ++i) { if (i) out << ','; out << effect_text(effects->slots[static_cast<std::size_t>(i)], s.turn); }
    }
    out << "\nresources=";
    for (std::size_t i = 0; i < s.resources.size(); ++i) { if (i) out << ','; out << s.resources[i]; }
    out << "\npaid_heals=" << s.paid_steps << "\nshop_rerolls=" << s.shop_rerolls
        << "\ntrial_seen=" << (s.trial_seen ? "true" : "false") << "\ntrial_depth=" << s.trial_depth
        << "\npending_trial_reward=" << s.pending_trial_reward << "\ndonated_resource=" << s.donated_resource << '\n';
    if (s.phase == Phase::recovery) out << "resume_phase=" << name(resume_phase) << '\n';
    switch (decision_phase()) {
    case Phase::doors:
        out << "doors=";
        if (generate_current) out << "unknown";
        else for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            out << name(s.doors[i].kind);
            if (s.doors[i].trap) out << (s.doors[i].cursed_trap ? ":cursed_trap" : ":trap");
        }
        break;
    case Phase::encounter: out << "encounter=" << name(s.encounter); break;
    case Phase::shop: case Phase::curse_shop:
        out << "offers=";
        if (generate_current) out << "unknown";
        else for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            out << effect_text(s.offers[i].effect, s.turn, false) << ':' << s.offers[i].keys;
        }
        break;
    case Phase::gems:
        out << "gem_offers=";
        if (generate_current) out << "unknown";
        else for (std::size_t i = 0; i < 3; ++i) { if (i) out << ','; out << name(s.gem_offers[i]); }
        break;
    default: throw std::invalid_argument("Cannot encode unsupported progress phase");
    }
    out << '\n'; return out.str();
}
}
