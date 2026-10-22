#include "sfld/policy.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
using namespace sfld;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Line " + std::to_string(__LINE__) + ": " #x); } while(false)
int main() {
 try {
    const auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    Policy policy; policy.strategy=Strategy::key_stockpile; policy.practical_tactics=true;
    policy.survival_aware=true; policy.shop_key_reserve=4; policy.patient_boss_hp=.52;
    CHECK(std::find(p.first_run_gem_pool.begin(),p.first_run_gem_pool.end(),Gem::rabbit)==p.first_run_gem_pool.end());
    CHECK(std::find(p.later_run_gem_pool.begin(),p.later_run_gem_pool.end(),Gem::rabbit)!=p.later_run_gem_pool.end());
    State s; s.run_number=2; s.room=51; s.turn=50; s.hp=.05; s.keys=5;
    s.gems[ix(Gem::rabbit)]=s.gems[ix(Gem::moonstone)]=true;
    s.doors={DoorView{Door::monster},DoorView{Door::trial}};
    Game trial(p,7,s,{0}); CHECK(policy.choose(trial).index==1);
    trial.step(policy.choose(trial)); CHECK(policy.choose(trial).kind==ActionKind::flee);
    trial.step(policy.choose(trial)); CHECK(trial.state().trial_depth==1); CHECK(trial.state().hp==.05);
    CHECK(trial.state().phase==Phase::doors);
    s.gems[ix(Gem::moonstone)]=false; s.trial_seen=true; s.trial_depth=3;
    s.doors={DoorView{Door::exit_trial},DoorView{Door::trial}};
    Game risky(p,7,s,{0}); CHECK(policy.choose(risky).index==0);
    s.blessings.give({EffectKind::escape,.8,1,Clock::room});
    Game temporary(p,7,s,{0}); CHECK(policy.choose(temporary).index==1);
    temporary.step(policy.choose(temporary)); temporary.step(policy.choose(temporary));
    CHECK(!temporary.state().effect(EffectKind::escape));
    CHECK(temporary.state().doors[static_cast<std::size_t>(policy.choose(temporary).index)].kind==Door::exit_trial);
    s=State{}; s.room=99; s.turn=98; s.hp=.6; s.keys=2;
    s.doors={DoorView{Door::golden},DoorView{Door::locked}};
    Game final_room(p,9,s,{0}); CHECK(policy.choose(final_room).index==1);
    s.room=35; s.turn=34; s.hp=1; s.keys=4; s.doors={DoorView{Door::locked},DoorView{Door::monster}};
    Game before_shop(p,1,s,{0}); CHECK(policy.choose(before_shop).index==1);
    s.hp=.05; s.doors[1].trap=true;
    Game endangered(p,1,s,{0}); CHECK(policy.choose(endangered).index==0);
    s=State{}; s.room=60; s.turn=59; s.hp=.8; s.phase=Phase::curse_shop;
    s.offers={Offer{effect_template(EffectKind::broken_armor,true),5},Offer{effect_template(EffectKind::poison,true),3}};
    s.blessings.give({EffectKind::one_hit,1,8,Clock::room});
    Game covered(p,7,s,{0}); CHECK(policy.choose(covered).kind==ActionKind::buy); CHECK(policy.choose(covered).index==0);
    auto shop=s; shop.room=35; shop.turn=34; shop.phase=Phase::shop; shop.keys=8; shop.hp=.7; shop.blessings.clear();
    shop.offers={Offer{effect_template(EffectKind::one_hit,true),4},Offer{effect_template(EffectKind::raider,false),1}};
    policy.shop_allowance=5;
    Game good_shop(p,7,shop,{100}); CHECK(policy.choose(good_shop).kind==ActionKind::buy); CHECK(policy.choose(good_shop).index==0);
    s.room=10; s.turn=9; s.hp=.01; s.blessings.clear();
    s.offers={Offer{effect_template(EffectKind::poison,true),8},Offer{effect_template(EffectKind::hard_lock,true),1}};
    Game sacrifice(p,7,s,{0}); CHECK(policy.choose(sacrifice).kind==ActionKind::buy); CHECK(policy.choose(sacrifice).index==0);
    sacrifice.step(policy.choose(sacrifice)); CHECK(sacrifice.state().keys==8); CHECK(sacrifice.state().hp>0);
    auto next=sacrifice.state(); next.phase=Phase::encounter; next.encounter=Encounter::empty;
    Game poison(p,7,next,{0}); poison.step({ActionKind::interact}); CHECK(poison.state().hp==0); CHECK(poison.state().keys==8);
    s=State{}; s.room=100; s.turn=99; s.phase=Phase::recovery; s.encounter=Encounter::boss; s.hp=0; s.paid_steps=2;
    Game patient(p,7,s,{100}); auto action=policy.choose(patient); CHECK(action.kind==ActionKind::wait); CHECK(std::abs(action.value-.52*p.recovery_hours)<1e-10);
    patient.step(action); CHECK(policy.choose(patient).kind==ActionKind::reenter);
    Limits late; late.budget=100; late.deadline_hours=1;
    Game urgent(p,7,s,late); CHECK(policy.choose(urgent).kind!=ActionKind::wait);
    std::cout << "PASS practical tactics, expiry, key protection, curse trades, Rabbit pool and patient boss\n";
 } catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
