#include "sfld/experiment.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace sfld;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Line " + std::to_string(__LINE__) + ": " #x); } while(false)
bool near(double a, double b) { return std::abs(a-b) < 1e-9; }
int main() {
 try {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    Limits l; l.budget=100; l.repeat_runs=true; l.deadline_hours=168;
    l.sleep_until_hour=8; l.event_start_hour=17;
    Game evening(p,7,l); CHECK(near(evening.state().elapsed_hours,0)); CHECK(near(evening.local_hour(),17));
    State recovery; recovery.room=10; recovery.turn=9; recovery.phase=Phase::recovery; recovery.hp=0;
    Game wait(p,7,recovery,l);
    wait.step({ActionKind::wait,0,10}); // Ready at 03:00, player returns at 08:00.
    CHECK(near(wait.state().elapsed_hours,15)); CHECK(near(wait.state().hp,15/p.recovery_hours));
    CHECK(wait.state().actions==1 && wait.state().active_hours==0 && wait.state().mushrooms==0);
    CHECK(wait.state().phase==Phase::recovery && wait.legal({ActionKind::reenter}));
    CHECK(wait.outcomes().size()==3 && wait.outcomes()[1].kind=="sleep" && near(wait.outcomes()[1].amount,5));
    CHECK(wait.outcomes()[2].detail=="overnight");
    auto late=l; late.deadline_hours=12; // Deadline at 05:00, inside the night.
    Game deadline(p,7,recovery,late); deadline.step({ActionKind::wait,0,10});
    CHECK(deadline.finished() && near(deadline.state().elapsed_hours,12));
    CHECK(near(deadline.state().hp,12/p.recovery_hours)); CHECK(!deadline.legal({ActionKind::heal_full}));
    auto midnight=l; midnight.event_start_hour=0;
    Game initial(p,7,midnight); CHECK(near(initial.state().elapsed_hours,8) && initial.state().hp==1);
    CHECK(initial.state().actions==0 && initial.state().active_hours==0);
    State living; living.phase=Phase::encounter; living.encounter=Encounter::empty;
    living.room=10; living.turn=9; living.hp=.37; living.keys=8; living.paid_steps=2; living.elapsed_hours=7;
    living.blessings.give({EffectKind::one_hit,1,8,Clock::room});
    living.curses.give({EffectKind::poison,.05,4,Clock::room});
    Game paused(p,7,living,l);
    CHECK(near(paused.state().elapsed_hours,15) && paused.state().hp==.37);
    CHECK(paused.state().keys==8 && paused.state().paid_steps==2 && paused.state().actions==0);
    CHECK(paused.state().blessings.slots[0].remaining==8 && paused.state().curses.slots[0].remaining==4);
    CHECK(paused.state().phase==Phase::encounter && paused.state().room==10 && paused.state().turn==9);
    CHECK(!paused.legal({ActionKind::wait,0,1}));
    Game restored(p,9,paused.planning_snapshot());
    CHECK(state_digest(restored.state())==state_digest(paused.state()));
    CHECK(restored.limits().event_start_hour==17 && restored.limits().sleep_until_hour==8);
    recovery.elapsed_hours=7; recovery.hp=.9;
    Game capped(p,7,recovery,l); CHECK(capped.state().hp==1 && near(capped.state().elapsed_hours,15));
    auto unscheduled=l; unscheduled.sleep_until_hour=0;
    Game continuous(p,7,living,unscheduled);
    CHECK(continuous.state().elapsed_hours==7 && continuous.state().hp==living.hp && continuous.outcomes().empty());
    living.elapsed_hours=7-p.action_seconds/7200; // Half an action remains before midnight.
    Game not_enough_time(p,7,living,l); CHECK(near(not_enough_time.state().elapsed_hours,15));
    living.elapsed_hours=7-p.action_seconds/3600; living.blessings.clear(); living.curses.clear();
    Game boundary(p,7,living,l); CHECK(boundary.state().elapsed_hours<7);
    boundary.step({ActionKind::skip}); CHECK(near(boundary.state().elapsed_hours,15));
    CHECK(boundary.state().actions==1 && boundary.state().room==11 && boundary.state().hp==living.hp);
    recovery.elapsed_hours=6; recovery.hp=0;
    Game spans_nights(p,7,recovery,l); spans_nights.step({ActionKind::wait,0,52});
    CHECK(near(spans_nights.state().elapsed_hours,63) && spans_nights.state().hp==1); // 03:00 -> 08:00.
    recovery.elapsed_hours=0;
    Game exact_wake(p,7,recovery,l); exact_wake.step({ActionKind::wait,0,15});
    CHECK(near(exact_wake.state().elapsed_hours,15) && exact_wake.outcomes().size()==1);
    for(double invalid : {-1.0,24.0,std::numeric_limits<double>::quiet_NaN()}) {
        auto bad=l; bad.sleep_until_hour=invalid; bool rejected=false;
        try { Game x(p,7,bad); } catch(const std::invalid_argument&) { rejected=true; } CHECK(rejected);
        bad=l; bad.event_start_hour=invalid; rejected=false;
        try { Game x(p,7,bad); } catch(const std::invalid_argument&) { rejected=true; } CHECK(rejected);
    }
    // Every decision in complete weeks is awake, with a fitting active duration.
    Policy policy; policy.survival_aware=true;
    for(int seed=0;seed<16;++seed) {
        Game game(p,static_cast<std::uint64_t>(seed),l);
        while(!game.finished()) {
            CHECK(game.local_hour()>=8-1e-10);
            const auto actions=legal_actions(game); CHECK(!actions.empty());
            auto action=policy.choose(game);
            if(action.kind==ActionKind::wait) action.value=std::min(action.value,l.deadline_hours-game.state().elapsed_hours);
            CHECK(game.legal(action));
            if(action.kind!=ActionKind::wait) CHECK(game.local_hour()+game.action_hours(action)<=24+1e-9);
            game.step(action);
        }
        CHECK(game.state().actions<l.max_actions && near(game.state().elapsed_hours,168));
    }
    std::cout << "PASS daily schedule: overnight recovery, living pauses, boundaries, deadlines, snapshots and complete weeks\n";
 } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
