"""Experimental stage-one key conservation. Not enabled in the deployed adviser."""
import numpy as np
from .interaction_policy import InteractionPolicy

VARIANTS=('control','double_lock','key_bank')


class OpeningKeyPolicy(InteractionPolicy):
    def __init__(self,env,profile,variant='control'):
        if variant not in VARIANTS:raise ValueError(variant)
        super().__init__(env,profile,'base')
        self.opening_variant=variant
        self.first_hit_max=float(profile['monster_damage'].split(',')[0].split(':')[1])
        self.counts.update(early_doors_avoided=0,early_fights_added=0)

    def apply(self,obs,masks,proposed):
        actions,targets,reasons=super().apply(obs,masks,proposed)
        if self.opening_variant=='control':return actions,targets,reasons
        v=lambda n:self.v(obs,n)
        f=lambda n:self.flag(obs,n)
        stage=(np.rint(v('room_div100')*100)<25)
        hard=self.effect(obs,'hard_lock')>0
        clumsy=self.effect(obs,'clumsy')>0
        scope=stage&((hard&clumsy) if self.opening_variant=='double_lock' else ((v('keys_div20')<8/20-1e-6)|hard))
        multiplier=np.maximum(0,1+.25*f('owned_gem.rabbit')+.25*f('owned_gem.devil')-
            .2*f('owned_gem.hero')-.2*f('owned_gem.bull')-.2*f('owned_gem.deceit')+self.effect(obs,'broken_armor'))
        damage=np.where(self.effect(obs,'one_hit')>0,0,self.first_hit_max*multiplier)
        recovery=self.effect(obs,'recovery')*np.where(f('owned_gem.rusty'),1.4,1)
        safe=(v('hp')>damage+1e-6)&(np.minimum(1,v('hp')-damage+recovery)-self.effect(obs,'poison')>1e-6)
        counted=np.ones(len(obs),bool) if self.count_before_episode is None else self.lab.env.episode_indices<self.count_before_episode
        for side in range(2):
            other=1-side
            use=(scope&safe&f('phase.doors')&f(f'doors.{side}.kind.monster')&
                ~f(f'doors.{side}.trap')&~f(f'doors.{side}.cursed_trap')&
                (v(f'doors.{other}.key_cost_div6')>0)&(actions==self.ai[f'door_{other}'])&masks[:,self.ai[f'door_{side}']])
            actions[use]=self.ai[f'door_{side}'];self.counts['early_doors_avoided']+=int((use&counted).sum())
        use=(scope&safe&f('phase.encounter')&f('encounter.monster')&
             (actions==self.ai['flee'])&masks[:,self.ai['fight']])
        actions[use]=self.ai['fight'];self.counts['early_fights_added']+=int((use&counted).sum())
        if not masks[np.arange(len(obs)),actions].all():raise RuntimeError('Illegal opening-key action')
        return actions,targets,reasons
