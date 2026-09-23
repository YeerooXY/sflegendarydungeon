#pragma once
#include "sfld/model.hpp"

namespace sfld {
// A player's observed position, with fresh future randomness. Time, spending and
// result counters start at zero; paid_steps/shop_rerolls retain pricing history.
struct StartState {
    State state;
    Phase resume_phase = Phase::doors;
    bool generate_current = true;
    static StartState load(const std::string& path);
    static StartState parse(std::string_view text);
    void validate(const Profile&) const;
    std::string encode() const;
    std::string fingerprint() const { return text_fingerprint(encode()); }
    Phase decision_phase() const { return state.phase == Phase::recovery ? resume_phase : state.phase; }
};
}
