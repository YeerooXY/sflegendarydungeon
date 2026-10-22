#pragma once
#include "sfld/policy.hpp"
#include "sfld/rule_gems.hpp"

namespace sfld {
// Explicit, player-readable decision rules. The search harness may mutate these
// public settings, but choices inspect only currently visible Game state.
struct RulePolicy {
    std::string id = "human-rules";
    GemRules gem_rules;
    std::array<int, 4> fight_keys{10, 6, 2, 0}; // Fight while keys are strictly below the stage target.
    std::array<int, 4> door_reserve{6, 4, 2, 0};
    double max_fight_death_risk = .05;
    double min_fight_hp = .2;
    double fight_escape_cutoff = .8; // Escape instead of farming keys at/above this success chance.
    bool fight_when_cheaper = false; // Fight past farming limits when expected end-room HP is higher.
    int door_mode = 0; // 0 balanced, 1 conserve keys, 2 avoid expected damage.
    bool guaranteed_escape_door_preference = false; // Prefer a trap-free monster when escape is certain.
    int shop_lookahead = 8;
    int shop_key_reserve = 4;
    int shop_allowance = 5;
    int shop_mode = 0; // 0 One Hit first, 1 healing first, 2 escape first.
    double shop_purchase_floor = 0; // Minimum score for a paid offer; useful free offers remain eligible.
    int barrel_mode = 1; // 0 skip, 1 conditional, 2 open unless the blessing chance is zero.
    int paid_refill_mode = 1; // 0 none, 1 danger/recovery, 2 also below revive_hp, 3 mode 1 with efficient refills.
    double trap_hp = .3;
    double barrel_hp = .45;
    double revive_hp = .2;
    double shop_reserve_fraction = 0; // Portion of the total mushroom cap protected from healing.
    double login_interval_hours = 0; // External availability constraint; not an optimization knob.

    Action choose(const Game&) const;
    void validate() const;
    static RulePolicy load(const std::string& path);
    void save(const std::string& path) const;
    std::string encode() const;
};
}
