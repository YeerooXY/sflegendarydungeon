"""Visible-state candidates: shop allowance, keys, trials and patient boss recovery.

These are experiments layered over a frozen policy, not a new trained optimum.
"""
import numpy as np
from .escape_loot import EscapeLoot

CASES = ('learned', 'shop5', 'keys', 'trials', 'trades', 'tactics', 'patient52', 'patient_safe')
REASONS = ('soldier', 'shop', 'reserve_keys', 'late_door', 'trial', 'trade', 'boss_wait', 'free_gold')


class Practical(EscapeLoot):
    def __init__(self, names, actions, case, profile):
        if case not in CASES: raise ValueError('Unknown practical candidate')
        super().__init__(names, actions, 'soldier_only', profile)
        self.variant=case
        self.shop_allowance=5;self.shop_key_reserve=4
        self.recovery_hours=float(profile['recovery_hours'])
        self.trial=np.array([float(x) for x in profile['trial_multipliers'].split(',')])
        self.escape_damage=np.array([[float(v) for v in r.split(':')] for r in profile['escape_damage'].split(',')])
        self.fight_only_damage_stones=profile.get('fight_only_damage_stones','false')=='true'

    def effect(self, obs, kind):
        group='curses' if kind in ('clumsy','poison','broken_armor','hard_lock','gold_hangover') else 'blessings'
        return sum(self.v(obs,f'{group}.{i}.magnitude')*self.flag(obs,f'{group}.{i}.active')*
                   self.flag(obs,f'{group}.{i}.kind.{kind}') for i in range(3))

    def remaining(self, obs, kind):
        group='curses' if kind in ('clumsy','poison','broken_armor','hard_lock','gold_hangover') else 'blessings'
        return sum(np.rint(self.v(obs,f'{group}.{i}.remaining_div10')*10)*self.flag(obs,f'{group}.{i}.active')*
                   self.flag(obs,f'{group}.{i}.kind.{kind}') for i in range(3))

    def apply(self, obs, masks, proposed):
        actual,old=super().apply(obs,masks,proposed)
        targets=np.full(len(obs),-1.,dtype=np.float64)
        reasons=np.zeros((len(obs),len(REASONS)),bool);reasons[:,0]=old[:,0]
        # The unlocked sarcophagus pays gold for one action, with no key and no
        # risk; the policy is trained on clears only and may skip it. Always open.
        free_gold=(self.flag(obs,'phase.encounter')&self.flag(obs,'encounter.sarcophagus')&
                   masks[:,self.ai['interact']]&(actual!=self.ai['interact']))
        actual[free_gold]=self.ai['interact'];reasons[free_gold,REASONS.index('free_gold')]=True
        if self.variant=='learned':
            # A new One Hit Wonder replaces the old one in place. Never pay
            # (even zero keys) for strictly fewer protected rooms than skipping.
            shop=self.flag(obs,'phase.shop')
            if shop.any():
                left=self.remaining(obs,'one_hit')-1
                bonus=self.flag(obs,'owned_gem.time_traveler').astype(int)
                for side in range(2):
                    p=f'offers.{side}.effect'
                    weaker=(shop & (actual==self.ai[f'buy_{side}']) & self.flag(obs,p+'.kind.one_hit') &
                            (left>np.rint(self.v(obs,p+'.remaining_div10')*10)+bonus) & masks[:,self.ai['skip']])
                    actual[weaker]=self.ai['skip'];reasons[weaker,1]=True
                    # The fixed free shop has one safe alternative when there
                    # is a spare slot and no existing escape effect to weaken.
                    other=1-side
                    free_escape=(weaker & self.flag(obs,'first_event_run') &
                        (np.rint(self.v(obs,'room_div100')*100)==5) &
                        self.flag(obs,f'offers.{other}.effect.kind.escape') &
                        (self.v(obs,f'offers.{other}.keys_div6')==0) &
                        (self.effect(obs,'escape')==0) &
                        (sum(self.flag(obs,f'blessings.{i}.present').astype(int) for i in range(3))<3) &
                        masks[:,self.ai[f'buy_{other}']])
                    actual[free_escape]=self.ai[f'buy_{other}']
            return actual,targets,reasons
        v=lambda n:self.v(obs,n)
        f=lambda n:self.flag(obs,n)
        g=lambda n:f('owned_gem.'+n)
        hp=v('hp');room=np.rint(v('room_div100')*100).astype(int);stage=np.clip((room-1)//25,0,3)
        keys=np.rint(v('keys_div20')*20)
        recovery=self.effect(obs,'recovery')*np.where(g('rusty'),1.4,1)
        poison=self.effect(obs,'poison');one_hit=self.effect(obs,'one_hit')>0
        chance=np.clip(self.base_escape+.4*g('rabbit')+.2*g('moonstone')-.3*g('bull')+
                       self.effect(obs,'escape')-self.effect(obs,'clumsy'),0,1)
        mult=np.maximum(0,1+.25*g('rabbit')+.25*g('devil')-.2*g('hero')-.2*g('bull')-.2*g('deceit')+self.effect(obs,'broken_armor'))
        high=self.high[stage]*mult
        escape_base=1+self.effect(obs,'broken_armor') if self.fight_only_damage_stones else mult
        escape_mult=np.maximum(0,escape_base-.4*g('pearl')+.3*g('hick'))
        escape_high=self.escape_damage[stage,1]*escape_mult
        exit_safe=lambda damage: (hp>damage+1e-6)&(np.minimum(1,hp-damage+recovery)-poison>1e-6)
        safe_escape=(chance>=1-1e-6)&exit_safe(0)
        def choose(condition,name,reason):
            use=condition&masks[:,self.ai[name]]&(actual!=self.ai[name])
            actual[use]=self.ai[name];reasons[use,REASONS.index(reason)]=True
        # Five refresh credits per paid shop, carrying forward. Never force
        # five purchases, discard a useful current offer, or reroll below 4 keys.
        shop=f('phase.shop');good=np.zeros((len(obs),2),bool)
        value=np.zeros((len(obs),2))
        normal_left=100-room-(100-room)//25
        for i in range(2):
            p=f'offers.{i}.effect';duration=np.rint(v(p+'.remaining_div10')*10)
            for kind in ('one_hit','recovery','escape','lockpick'):
                kindflag=f(p+'.kind.'+kind)
                useful=kindflag&(normal_left>=2)&(duration>self.remaining(obs,kind))
                if kind=='escape':useful &= chance<1-1e-6
                if kind=='lockpick':useful &= (duration>=4)&(hp>=.5)
                good[:,i]|=useful&masks[:,self.ai[f'buy_{i}']]
            value[:,i]=np.minimum(duration,normal_left)*np.where(f(p+'.kind.one_hit'),.18,
                         np.where(f(p+'.kind.recovery'),v(p+'.magnitude'),.08))
        best=np.argmax(np.where(good,value,-1),axis=1);any_good=good.any(axis=1)
        bank=self.shop_allowance*np.rint(v('reroll_shop_visits_div20')*20)-np.rint(v('reroll_bank_used_div100')*100)
        for i in range(2):choose(shop&any_good&(best==i),f'buy_{i}','shop')
        choose(shop&~any_good&(keys>=self.shop_key_reserve)&(bank>0),'reroll','shop')
        blocked=shop&~any_good&(actual==self.ai['reroll'])&((bank<=0)|(keys<self.shop_key_reserve))
        choose(blocked,'skip','shop')
        if self.variant=='shop5':return actual,targets,reasons
        combined=self.variant in ('tactics','patient52','patient_safe')
        doors=f('phase.doors')
        if self.variant=='keys' or combined:
            seen=np.column_stack([f('shops_seen.'+str(i)) for i in range(1,5)])[np.arange(len(obs)),stage]
            disarm=self.effect(obs,'disarm')>0
            for side in range(2):
                other=1-side;cost=np.rint(v(f'doors.{other}.key_cost_div6')*6)
                untrapped=~f(f'doors.{side}.trap')|disarm
                free=v(f'doors.{side}.key_cost_div6')==0
                # A possible monster must still be survivable; hidden contents
                # are never read. Preserve an escape from a trap+enemy pair.
                safe=exit_safe(np.where(one_hit|safe_escape,0,high))
                eligible=(f(f'doors.{side}.kind.monster')|f(f'doors.{side}.kind.mystery'))&safe
                eligible|=f(f'doors.{side}.kind.golden')&exit_safe(.1)
                reserve=(~seen&(keys-cost<4))|((stage<2)&(keys<8))|(self.effect(obs,'hard_lock')>0)
                choose(doors&free&untrapped&eligible&(cost>0)&reserve,f'door_{side}','reserve_keys')
                choose(doors&(room==99)&f(f'doors.{side}.kind.locked')&~f(f'doors.{side}.trap')&
                       f(f'doors.{other}.kind.golden')&~g('deceit')&(self.effect(obs,'hard_lock')==0)&
                       (hp>self.boss[3,1]+poison+1e-6),f'door_{side}','late_door')
        if self.variant=='trials' or combined:
            depth=np.clip(np.rint(v('trial_depth_div5')*5).astype(int),0,4)
            hit=escape_high*self.trial[depth]
            attempt=safe_escape|(one_hit&exit_safe(0))|((g('rabbit')|(self.effect(obs,'escape')>0))&(chance>=.8-1e-6)&exit_safe(hit))
            for side in range(2):choose(doors&f(f'doors.{side}.kind.trial')&~f(f'doors.{side}.trap')&attempt,f'door_{side}','trial')
            choose(f('phase.encounter')&f('encounter.trial_monster')&~one_hit&safe_escape,'flee','trial')
        if self.variant=='trades' or combined:
            shop=f('phase.curse_shop');gains=np.full((len(obs),2),-np.inf)
            key_value=np.where((keys<4)&(stage<3),.16,.06)/(1+.15*keys)
            for i in range(2):
                p=f'offers.{i}.effect';duration=np.rint(v(p+'.remaining_div10')*10)+g('moonstone')
                damage=high+np.where(f(p+'.kind.broken_armor'),self.high[stage]*v(p+'.magnitude'),0)
                covered=np.maximum(0,self.remaining(obs,'one_hit')-1)
                heals=(hp>damage)&(recovery>=damage+poison)
                covered=np.maximum(covered,np.where(heals,np.maximum(0,self.remaining(obs,'recovery')-1),0))
                harm=np.full(len(obs),np.inf)
                harmless=f(p+'.kind.gold_hangover')
                armor=f(p+'.kind.broken_armor');clumsy=f(p+'.kind.clumsy');toxic=f(p+'.kind.poison');lock=f(p+'.kind.hard_lock')
                harm[harmless]=0
                harm[armor|clumsy]=(v(p+'.magnitude')*high*.45*np.maximum(0,duration-covered))[armor|clumsy]
                harm[lock]=(.15*duration*key_value)[lock]
                harm[toxic]=(v(p+'.magnitude')*duration)[toxic]
                doomed=toxic&(hp+recovery+np.where(self.remaining(obs,'recovery')>1,recovery,0)<=v(p+'.magnitude')+1e-6)
                lost=.15*self.remaining(obs,'one_hit')+recovery*self.remaining(obs,'recovery')
                harm[doomed]=(.21+lost)[doomed]
                gain=np.rint(v(f'offers.{i}.keys_div6')*6)*key_value-harm
                gain[doomed&(v('hours_left_div168')*168<7)]=-np.inf
                gains[:,i]=np.where(masks[:,self.ai[f'buy_{i}']],gain,-np.inf)
            best=np.argmax(gains,axis=1)
            for i in range(2):choose(shop&(best==i)&(gains[:,i]>0),f'buy_{i}','trade')
        if self.variant.startswith('patient'):
            target=.52 if self.variant=='patient52' else min(1,self.boss[3,1]+.001)
            wait=np.maximum(0,target-hp)*self.recovery_hours
            use=f('phase.recovery')&(room==100)&(np.rint(v('paid_heals_div20')*20)>=2)&(v('hours_left_div168')*168>wait+2/3600)
            choose(use&(hp>=target-1e-6),'reenter','boss_wait')
            waiting=use&(hp<target-1e-6);actual[waiting]=self.ai['wait_deadline'];targets[waiting]=target
            reasons[waiting,REASONS.index('boss_wait')]=True
        normal=targets<0
        if not masks[np.flatnonzero(normal),actual[normal]].all():raise RuntimeError('Illegal practical action')
        return actual,targets,reasons
