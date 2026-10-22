"""Experimental recovery plans based on visible damage and revival effects.

No hidden draws are inspected. A projected sequence is a planning heuristic,
not a promise that future rooms contain monsters or no additional traps/curses.
"""
from dataclasses import dataclass, asdict
import numpy as np
from .interaction_policy import InteractionPolicy


@dataclass(frozen=True)
class Idea:
    name: str
    damage: str = 'maximum'
    trigger: str = 'entry'  # entry: extend chosen reentry; wait: replace natural target
    hybrid: bool = False
    only_one: bool = False
    extra_limit: float = 1.0


IDEAS = {x.name:x for x in (
    Idea('control'), Idea('cap20'),
    Idea('entry_mean', damage='mean'), Idea('entry_max'),
    Idea('wait_mean', damage='mean',trigger='wait'),
    Idea('wait_max',trigger='wait'), Idea('wait_one',trigger='wait',only_one=True),
    Idea('hybrid_mean',damage='mean',trigger='wait',hybrid=True),
    Idea('hybrid_max',trigger='wait',hybrid=True),
    Idea('hybrid_nearby',trigger='wait',hybrid=True,extra_limit=.10),
)}


def required_health(damage, healing, poison, entry_damage=0.0, margin=.001):
    """HP needed for each prefix of a fixed hit/heal/poison sequence.

    Work backwards through each prefix. The character must survive damage
    before healing, healing caps at 100%, and poison follows healing. A 0.1
    percentage-point cushion avoids death exactly at zero. Infeasible paths
    stay above 100% rather than being mislabeled survivable.
    """
    damage,healing,poison=np.broadcast_arrays(np.asarray(damage,dtype=float),
                                             np.asarray(healing,dtype=float),np.asarray(poison,dtype=float))
    if damage.ndim!=2:raise ValueError('Expected [characters, successive rooms] arrays')
    result=np.empty_like(damage)
    for count in range(1,damage.shape[1]+1):
        need=np.zeros(damage.shape[0])
        for i in range(count-1,-1,-1):
            possible=need+poison[:,i]<1
            need=np.maximum(damage[:,i],damage[:,i]-healing[:,i]+poison[:,i]+need)
            need=np.where(possible,need,np.inf)
        result[:,count-1]=need+np.asarray(entry_damage)+margin
    return result


class RecoveryThresholds:
    def __init__(self,env,profile,variant):
        self.env=env;self.base=InteractionPolicy(env,profile,'base');self.idea=IDEAS[variant]
        self.ai=self.base.ai;self.oi=self.base.oi;self.hours=float(profile['recovery_hours'])
        self.ranges={key:np.array([[float(v) for v in row.split(':')] for row in profile[key].split(',')])
                     for key in ('monster_damage','escape_damage','boss_damage')}
        self.shape=profile['damage_distribution'];n=env.n_envs
        self.pending=np.full(n,-1.);self.bought=np.zeros(n,bool)
        self.identity=np.full(n,-1,dtype=np.int64);self.death=np.full(n,-1.)
        self.last_flags={};self.counts={}

    def v(self,obs,name):return self.base.v(obs,name)
    def f(self,obs,name):return self.base.flag(obs,name)

    def damage_value(self,key,mode):
        a=self.ranges[key];lo,hi=a[:,0],a[:,1];average=(lo+hi)/2
        if self.shape=='triangular_mean':average=a[:,2]
        if self.shape=='low':return lo
        if self.shape=='high':return hi
        if self.shape=='midpoint':return (lo+hi)/2
        return average if mode=='mean' else hi

    def health_targets(self,obs,base_hp):
        """Stage/stone-specific next survival boundary above the starting HP."""
        f=lambda n:self.f(obs,n);g=lambda n:f('owned_gem.'+n)
        room=np.rint(self.v(obs,'room_div100')*100).astype(int)
        stage=np.clip((room-1)//25,0,3);count=12;j=np.arange(count)[None,:]
        effect=lambda name:self.base.effect(obs,name)[:,None]
        remaining=lambda name:self.base.remaining(obs,name)[:,None]
        mult=(1+.25*g('rabbit')+.25*g('devil')-.2*g('hero')-.2*g('bull')-.2*g('deceit'))[:,None]
        mult=mult+effect('broken_armor')*(j<remaining('broken_armor'))
        fight=self.damage_value('monster_damage',self.idea.damage)[stage,None]*np.maximum(0,mult)
        flee=self.damage_value('escape_damage',self.idea.damage)[stage,None]*np.maximum(0,mult-.4*g('pearl')[:,None]+.3*g('hick')[:,None])
        damage=np.maximum(fight,flee)
        chance=np.clip((self.base.base_escape+.4*g('rabbit')+.2*g('moonstone')-.3*g('bull'))[:,None]
            +effect('escape')*(j<remaining('escape'))-effect('clumsy')*(j<remaining('clumsy')),0,1)
        # Existing protection offers a damage-free combat action. The unchanged
        # combat policy still chooses its actual action in the complete event.
        damage=np.where((chance>=1-1e-7)|(j<remaining('one_hit')),0,damage)
        heal_magnitude=np.where(g('greasy')[:,None],.1,effect('recovery'))
        heal_uses=np.where(g('greasy')[:,None],3+g('time_traveler')[:,None],remaining('recovery'))
        heal=heal_magnitude*(j<heal_uses)*np.where(g('rusty')[:,None],1.4,1)
        poison_magnitude=np.where(g('rusty')[:,None],.05,effect('poison'))
        poison_uses=np.where(g('rusty')[:,None],5+g('moonstone')[:,None],remaining('poison'))
        poison=poison_magnitude*(j<poison_uses)
        # Only unavoidable currently revealed damaging traps are charged here.
        available=np.column_stack([f(f'doors.{i}.available') for i in range(2)])
        trap=np.column_stack([f(f'doors.{i}.trap')&~f(f'doors.{i}.cursed_trap') for i in range(2)])
        forced=f('resume_phase.doors')&available.any(axis=1)&np.all(~available|trap,axis=1)&(self.base.effect(obs,'disarm')==0)
        entry=forced*.1*np.where(g('masochist'),.8,1)
        thresholds=required_health(damage,heal,poison,entry)
        # Do not project ordinary hits through a boss or assume more rooms exist.
        remaining_rooms=25-room%25
        thresholds=np.where(j<remaining_rooms[:,None],thresholds,np.inf)
        if self.idea.only_one:
            target=np.maximum(base_hp,thresholds[:,0])
        else:
            candidates=np.where((thresholds>base_hp[:,None]+1e-6)&(thresholds<=1),thresholds,np.inf)
            target=np.min(candidates,axis=1)
            target=np.where(np.isfinite(target),target,base_hp)
        target=np.where((target>1)|(target-base_hp>self.idea.extra_limit+1e-8),base_hp,target)
        boss=room%25==0
        boss_target=np.maximum(.2,self.damage_value('boss_damage','maximum')[stage]+.001)
        target=np.where(boss,np.maximum(base_hp,boss_target),target)
        return np.clip(target,.2,1),thresholds

    def apply(self,obs,masks,proposed,fallback):
        actual,targets,reasons=self.base.apply(obs,masks,proposed)
        self.last_flags={name:np.zeros(len(obs),bool) for name in
                         ('threshold_plans','extra_waits','partial_refills','expensive_refills_blocked','planned_reentries')}
        if self.idea.name=='control':return actual,targets,reasons
        recovery=self.f(obs,'phase.recovery');hp=self.v(obs,'hp').astype(float)
        deaths=self.v(obs,'deaths_div100');identity=self.env.episode_indices
        reset=(identity!=self.identity)|(deaths!=self.death)|~recovery
        self.pending[reset]=-1;self.bought[reset]=False
        self.identity=identity.copy();self.death=deaths.copy()
        paid=np.isin(actual,[self.ai['heal_step'],self.ai['heal_full']])&recovery
        price=np.rint(self.v(obs,'heal_step_price_div100')*100)
        cheap=masks[:,self.ai['heal_step']]&(price<=20)&~self.bought
        if self.idea.name=='cap20':
            take=paid&cheap
            actual[take]=self.ai['heal_step'];self.bought[take]=True
            actual[paid&~cheap]=fallback[paid&~cheap]
            self.last_flags['partial_refills']=take
            self.last_flags['expensive_refills_blocked']=paid&~cheap
            return actual,targets,reasons
        entering=actual==self.ai['reenter'];waiting=actual>=self.ai['wait_hp_20']
        boss=np.rint(self.v(obs,'room_div100')*100).astype(int)%25==0
        trigger=entering if self.idea.trigger=='entry' else (entering|waiting)
        if self.idea.hybrid:trigger|=paid
        else:trigger&=~boss
        new=recovery&trigger&(self.pending<0)
        if new.any():
            target,_=self.health_targets(obs,np.maximum(.2,hp))
            self.pending[new]=target[new];self.last_flags['threshold_plans']=new
        planned=recovery&(self.pending>=0)
        can_enter=masks[:,self.ai['reenter']]
        need=planned&(hp<self.pending-1e-6)
        # If a target cannot be reached before closing, use legal current HP.
        hours_left=self.v(obs,'hours_left_div168')*168
        too_late=need&can_enter&((self.pending-hp)*self.hours+1/3600>hours_left)
        need&=~too_late
        take=need&paid&cheap&self.idea.hybrid&new
        self.last_flags['partial_refills']=take
        self.last_flags['expensive_refills_blocked']=paid&self.idea.hybrid&~take
        actual[take]=self.ai['heal_step'];self.bought[take]=True
        wait=need&~take
        if wait.any():
            choices=masks[:,self.ai['wait_hp_20']:]
            if not choices[wait].any(axis=1).all():raise RuntimeError('No legal recovery wait')
            actual[wait]=choices[wait].argmax(axis=1)+self.ai['wait_hp_20']
            targets[wait]=self.pending[wait]
            self.last_flags['extra_waits']=wait
        enter=planned&~need&can_enter
        actual[enter]=self.ai['reenter'];self.last_flags['planned_reentries']=enter
        if not masks[np.arange(len(obs)),actual].all():raise RuntimeError('Illegal recovery plan')
        return actual,targets,reasons

    def record(self):return asdict(self.idea)
