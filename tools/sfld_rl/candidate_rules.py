"""Candidate corrections from the October 22 local optimality audit.

Each rule reads public observations only and proposes a legal replacement for
the deployed adviser's action. They are research candidates: none is deployed
until it passes a fresh paired weekly comparison.
"""
import numpy as np
from .player_ideas import PlayerIdeas, CASES

RULES=('lethal_line_door','step_not_full','no_low_hp_reroll','reentry_floor','deadline_heal','preboss_safe_reentry')
SAFE_KINDS=('cursed','blessing','wood','stone','souls','metal','arcane','hourglasses')


class CandidateRules(PlayerIdeas):
    def __init__(self, env, profile, rules=RULES):
        super().__init__(env.observation_names,env.action_names,CASES['control'],profile)
        if set(rules)-set(RULES):raise ValueError('Unknown candidate rule')
        self.rules=tuple(rules);self.full_cost=float(profile['full_heal_cost_per_fraction'])
        self.counts={r:0 for r in RULES}

    def proposals(self, obs, masks, actions):
        """Return {rule: (where, replacement action)} for rows whose action would change."""
        v=lambda n:self.v(obs,n);f=lambda n:self.flag(obs,n);g=lambda n:f('owned_gem.'+n)
        n=len(obs);hp=v('hp').astype(np.float64);room=np.rint(v('room_div100')*100).astype(int)
        stage=np.clip((room-1)//25,0,3);result={}
        recovery=self.effect(obs,'recovery')*np.where(g('rusty'),1.4,1.);poison=self.effect(obs,'poison')
        one_hit=self.effect(obs,'one_hit')>0;disarm=self.effect(obs,'disarm')>0
        escape=np.clip(self.base_escape+.4*g('rabbit')+.2*g('moonstone')-.3*g('bull')+
                       self.effect(obs,'escape')-self.effect(obs,'clumsy'),0,1)
        mult=np.maximum(0,1+.25*g('rabbit')+.25*g('devil')-.2*g('hero')-.2*g('bull')-.2*g('deceit')+self.effect(obs,'broken_armor'))
        worst=np.where(one_hit|((escape>=1-1e-6)&~g('pearl')),0,self.high[stage]*mult)
        # Before a boss room, keep the boss's maximum hit as the survival line.
        line=np.where(room%25==24,self.boss[np.minimum(stage,3),1],0)+1e-6
        after=lambda damage:np.minimum(1,hp-damage+recovery)-poison
        def entry(side):
            trapped=f(f'doors.{side}.trap')&~disarm&(room>5)&~f(f'doors.{side}.kind.golden')
            cursed=f(f'doors.{side}.cursed_trap')|g('gambler')
            return np.where(trapped&~cursed,.1*np.where(g('masochist'),.8,1),0)+np.where(f(f'doors.{side}.kind.sacrifice'),.12*np.where(g('blood'),.6,1),0)
        if 'lethal_line_door' in self.rules:
            where=np.zeros(n,bool);target=actions.copy()
            for side in range(2):
                other=1-side;chosen=f('phase.doors')&(actions==self.ai[f'door_{side}'])
                fight=f(f'doors.{side}.kind.monster')|f(f'doors.{side}.kind.mystery')
                danger=chosen&fight&(hp-entry(side)-worst<=line)
                calm=np.zeros(n,bool)
                for kind in SAFE_KINDS:calm|=f(f'doors.{other}.kind.{kind}')
                # A visible monster excludes a monster from its paired mystery.
                calm|=f(f'doors.{other}.kind.mystery')&f(f'doors.{side}.kind.monster')
                use=danger&calm&(hp-entry(other)>1e-6)&(after(entry(other))>line)&masks[:,self.ai[f'door_{other}']]
                where|=use;target[use]=self.ai[f'door_{other}']
            result['lethal_line_door']=(where,target)
        recovering=f('phase.recovery')
        if 'step_not_full' in self.rules:
            full=np.maximum(1,np.ceil((1-hp)*self.full_cost-1e-12));left=v('mushrooms_left_div500')*500
            use=recovering&(actions==self.ai['heal_full'])&(full>.5*left)&masks[:,self.ai['heal_step']]
            result['step_not_full']=(use,np.where(use,self.ai['heal_step'],actions))
        if 'no_low_hp_reroll' in self.rules:
            use=f('phase.shop')&(actions==self.ai['reroll'])&(hp<=.2+1e-6)&masks[:,self.ai['skip']]
            result['no_low_hp_reroll']=(use,np.where(use,self.ai['skip'],actions))
        if 'reentry_floor' in self.rules:
            use=recovering&(actions==self.ai['reenter'])&(hp<=.2+1e-6)&(room%25!=0)
            step=use&masks[:,self.ai['heal_step']];wait=use&~step&masks[:,self.ai['wait_hp_40']]
            target=actions.copy();target[step]=self.ai['heal_step'];target[wait]=self.ai['wait_hp_40']
            result['reentry_floor']=(step|wait,target)
        if 'deadline_heal' in self.rules:
            # Waiting late in the week can forfeit the remaining hours; spend instead.
            waiting=(actions>=self.ai['wait_hp_20'])&(actions<=self.ai['wait_deadline'])
            use=recovering&waiting&(v('hours_left_div168')*168<=24+1e-6)&masks[:,self.ai['heal_full']]
            result['deadline_heal']=(use,np.where(use,self.ai['heal_full'],actions))
        if 'preboss_safe_reentry' in self.rules:
            # October 23 condition audit: within five rooms of a boss, re-entering below the
            # boss's maximum hit was the most repeated confirmed mistake. Wait to that line.
            safe=self.boss[np.minimum(stage,3),1]
            levels=np.array([.2,.4,.6,.8,1.])
            waits=np.array([self.ai[f'wait_hp_{k}'] for k in (20,40,60,80,100)])
            chosen=np.full(n,np.inf)
            for level,a in zip(levels,waits):chosen[actions==a]=level
            chosen[actions==self.ai['reenter']]=hp[actions==self.ai['reenter']]
            goal=np.minimum(1.,safe+.001)
            legal_wait=masks[:,waits].any(axis=1);first=waits[np.argmax(masks[:,waits],axis=1)]
            # With mushrooms for a partial heal the adviser's own heal choice did better (gate, 100 and 1,000).
            broke=v('mushrooms_left_div500')*500<v('heal_step_price_div100')*100-1e-6
            use=recovering&broke&(room%25>=20)&(chosen<safe)&(goal>hp+1e-4)&legal_wait&(v('hours_left_div168')*168>1e-3)
            # Any wait action accepts an exact HP target (sfld_rl_step_wait_targets).
            result['preboss_safe_reentry']=(use,np.where(use,first,actions),np.where(use,goal,-1.))
        return result

    def apply(self, obs, masks, actions, targets):
        actions=actions.copy();targets=targets.copy()
        for rule,proposal in self.proposals(obs,masks,actions).items():
            where,target=proposal[:2];goal=proposal[2] if len(proposal)>2 else None
            where=where&((actions!=target) if goal is None else np.ones_like(where))
            actions[where]=target[where];targets[where]=-1 if goal is None else goal[where];self.counts[rule]+=int(where.sum())
        return actions,targets


class CandidateAdviser:
    """Deployed adviser followed by the selected candidate rules."""
    def __init__(self, base, rules):
        self.base,self.rules=base,rules
        self.rule=base.rule

    def __call__(self, obs, masks):
        actions,targets=self.base(obs,masks)
        return self.rules.apply(obs,masks,actions,targets)
