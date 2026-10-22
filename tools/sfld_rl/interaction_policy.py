"""Experimental interaction corrections and exact desktop-guard evaluation.

Every choice uses public observations. These variants must pass a fresh weekly
comparison before deployment; a local action trial is not a promotion gate.
"""
import numpy as np
from .practical import Practical
from .effect_lab import EffectLab
from .visual_advice import annotate, correct_action
from .late_recovery import recovery_action

VARIANTS=('base','protect_recovery','safe_trial','both','cheap_elixir','strong_one_hit')


class InteractionPolicy(Practical):
    def __init__(self, env, profile, variant='base'):
        if variant not in VARIANTS:raise ValueError(variant)
        super().__init__(env.observation_names,env.action_names,'learned',profile)
        self.lab=EffectLab(env);self.experiment=variant
        self.profile=profile
        self.traveler_full=profile.get('time_traveler_elixir_full_heal','false')=='true'
        self.count_before_episode=None
        self.counts=dict(runtime_corrections=0,protect_recovery=0,safe_trial=0,cheap_elixir=0,strong_one_hit=0)

    def apply(self,obs,masks,proposed):
        actions,targets,reasons=super().apply(obs,masks,proposed)
        v=lambda n:self.v(obs,n)
        f=lambda n:self.flag(obs,n)
        counted=np.ones(len(obs),bool) if self.count_before_episode is None else self.lab.env.episode_indices<self.count_before_episode
        # Match desktop-only corrections exactly using native public JSON.
        # Only these phases/actions can trigger a correction; do not serialize
        # every batch member at every simulation step.
        # A shop reroll can only change through the Time Traveler elixir rule,
        # so skip serializing rerolls that cannot meet its preconditions.
        reroll=(actions==self.ai['reroll'])&f('owned_gem.time_traveler')&(v('hp')<=.5+1e-6)&self.traveler_full
        shop=f('phase.shop')&(np.isin(actions,[self.ai['buy_0'],self.ai['buy_1']])|reroll)
        hungry=np.zeros(len(obs),bool)
        for side in range(2):
            for kind in ('wood','stone','souls','metal','arcane','hourglasses'):
                hungry|=f(f'doors.{side}.kind.{kind}')
        door=f('phase.doors')&hungry&np.isin(actions,[self.ai['door_0'],self.ai['door_1']])
        # Other door rules have their own triggers. A hungry door only enables
        # the safe free door (certain escape, no poison) and curse protection
        # for a chosen trapped door, so skip serializing the rest.
        escape=self.base_escape+.4*f('owned_gem.rabbit')+.2*f('owned_gem.moonstone')-.3*f('owned_gem.bull')+self.effect(obs,'escape')-self.effect(obs,'clumsy')
        certain=(escape>=1-1e-4)&~f('owned_gem.pearl')
        trapped=((actions==self.ai['door_0'])&f('doors.0.trap'))|((actions==self.ai['door_1'])&f('doors.1.trap'))
        door&=(certain&(self.effect(obs,'poison')==0))|((certain|(self.effect(obs,'one_hit')>0))&trapped)
        # A paid hungry chest has exactly one correction: open it instead of skipping.
        chest=f('phase.encounter')&f('encounter.sated_chest')&(actions==self.ai['skip'])&masks[:,self.ai['interact']]
        actions[chest]=self.ai['interact'];self.counts['runtime_corrections']+=int((chest&counted).sum())
        chest[:]=False
        mimic=f('phase.encounter')&f('encounter.mimic')&((self.effect(obs,'one_hit')>0)|(self.effect(obs,'recovery')>0))
        cursed_pair=f('phase.doors')&f('owned_gem.rabbit')&f('owned_gem.moonstone')
        cursed_pair&=(f('doors.0.kind.cursed')&(actions==self.ai['door_0']))|(f('doors.1.kind.cursed')&(actions==self.ai['door_1']))
        protected_doors=np.zeros(len(obs),bool)
        for side in range(2):
            protected_doors|=f('phase.doors')&(f(f'doors.{side}.kind.cursed')|f(f'doors.{side}.kind.destiny'))&(actions==self.ai[f'door_{side}'])
        protected_doors|=f('phase.doors')&(self.effect(obs,'lockpick')>0)&(self.remaining(obs,'recovery')==1)
        protected_doors|=f('phase.doors')&(self.effect(obs,'lockpick')>0)&(self.effect(obs,'poison')>0)
        for side in range(2):
            protected_doors|=(f('phase.doors')&f(f'doors.{side}.kind.monster')&(actions==self.ai[f'door_{side}'])
                              &f(f'doors.{1-side}.kind.mystery')&f(f'doors.{1-side}.trap'))
        late_recovery=f('phase.recovery')&(v('room_div100')>=.97-1e-6)&(v('hours_left_div168')*168<=24+1e-4)
        arena=f('phase.encounter')&(f('encounter.trial_monster')|f('encounter.prize'))
        for side in range(2):
            arena|=f('phase.doors')&(f(f'doors.{side}.kind.trial')|f(f'doors.{side}.kind.exit_trial'))
        for slot in np.flatnonzero(shop|door|chest|arena|mimic|cursed_pair|protected_doors|late_recovery):
            o=self.lab.visible(int(slot));name=self.lab.env.action_names[int(actions[slot])]
            if late_recovery[slot]:
                if name.startswith('wait_'):
                    target=min(1,max(.2,o['boss_damage_max']+.001)) if name=='wait_boss_safe' else (1 if name=='wait_deadline' else int(name.rsplit('_',1)[1])/100)
                    hours=o['hours_left'] if name=='wait_deadline' else min(o['hours_left'],max(0,target-o['hp'])*self.recovery_hours)
                    original=dict(kind='wait',index=0,value=hours)
                else:
                    original=next(a for a in o['legal_actions'] if a['kind']==name)
                choice,_,rule=recovery_action(o,original,self.profile)
                if rule:
                    if choice['kind']=='wait':
                        start=self.ai['wait_hp_20']
                        actions[slot]=start+int(np.flatnonzero(masks[slot,start:])[0])
                        targets[slot]=min(1.,max(.2,o['hp']+choice['value']/self.recovery_hours))
                    else:actions[slot]=self.ai[choice['kind']]
                    self.counts['runtime_corrections']+=int(counted[slot])
                continue
            kind='buy' if name.startswith('buy_') else 'choose_door' if name.startswith('door_') else name
            index=int(name[-1]) if kind in ('buy','choose_door') else 0
            original=next(a for a in o['legal_actions'] if a['kind']==kind and a['index']==index)
            annotate(o);choice,_,rule=correct_action(o,original)
            if rule:
                name=({'buy':'buy_','choose_door':'door_'}.get(choice['kind'],'')+str(choice['index'])
                      if choice['kind'] in ('buy','choose_door') else choice['kind'])
                actions[slot]=self.ai[name];self.counts['runtime_corrections']+=int(counted[slot])
        if self.experiment=='base':return actions,targets,reasons
        def choose(condition,name,reason):
            use=condition&masks[:,self.ai[name]]&(actions!=self.ai[name])
            actions[use]=self.ai[name];self.counts[reason]+=int((use&counted).sum())
        if self.experiment in ('protect_recovery','both'):
            protected=(f('blessings.0.active')&f('blessings.0.kind.recovery')&
                       (v('blessings.0.magnitude')>=.2-1e-6)&(v('blessings.0.remaining_div10')>=.3-1e-6)&
                       f('blessings.1.present')&f('blessings.2.present')&(self.remaining(obs,'one_hit')>=2))
            protected&=np.minimum(1,v('hp')+self.effect(obs,'recovery')*np.where(f('owned_gem.rusty'),1.4,1))-self.effect(obs,'poison')>1e-6
            for side in range(2):
                other=1-side
                choose(f('phase.doors')&protected&f(f'doors.{side}.kind.monster')&~f(f'doors.{side}.trap')&
                       f(f'doors.{other}.kind.blessing'),f'door_{side}','protect_recovery')
        if self.experiment in ('safe_trial','both'):
            permanent=self.base_escape+.4*f('owned_gem.rabbit')+.2*f('owned_gem.moonstone')-.3*f('owned_gem.bull')-self.effect(obs,'clumsy')
            needed=5-np.rint(v('trial_depth_div5')*5)
            reliable=(permanent>=1-1e-6)|((permanent+self.effect(obs,'escape')>=1-1e-6)&(self.remaining(obs,'escape')>=needed))
            safe=reliable&(self.effect(obs,'poison')==0)&~f('owned_gem.pearl')
            for side in range(2):
                choose(f('phase.doors')&safe&f(f'doors.{side}.kind.trial')&~f(f'doors.{side}.trap'),f'door_{side}','safe_trial')
            choose(f('phase.encounter')&f('encounter.trial_monster')&safe&(self.effect(obs,'one_hit')==0),'flee','safe_trial')
        if self.experiment=='cheap_elixir' and self.traveler_full:
            for side in range(2):
                choose(f('phase.shop')&f('owned_gem.time_traveler')&(v('hp')<=.5)&
                       (actions==self.ai['reroll'])&f(f'offers.{side}.effect.kind.elixir')&
                       (v(f'offers.{side}.keys_div6')<=1/6+1e-6),f'buy_{side}','cheap_elixir')
        if self.experiment=='strong_one_hit':
            slots=sum(f(f'blessings.{i}.present').astype(int) for i in range(3))
            for side in range(2):
                choose(f('phase.shop')&(v('hp')>=.75)&(v('room_div100')<=.94)&(slots<3)&
                       (self.effect(obs,'one_hit')==0)&(actions==self.ai['reroll'])&
                       f(f'offers.{side}.effect.kind.one_hit')&
                       (v(f'offers.{side}.effect.remaining_div10')>=.8-1e-6),f'buy_{side}','strong_one_hit')
        if not masks[np.arange(len(obs)),actions].all():raise RuntimeError('Illegal interaction action')
        return actions,targets,reasons
