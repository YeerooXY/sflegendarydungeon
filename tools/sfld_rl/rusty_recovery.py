"""Conditional recovery experiments for Rusty, with and without Greasy."""
import numpy as np
from .interaction_policy import InteractionPolicy
from .recovery_thresholds import RecoveryThresholds

TARGETS={'control':None,'hp20':.20,'hp30_1':.301,'hp40':.40,'hp50':.50,
         'hp60':.60,'hp80':.80,'hp100':1.,'one_hit':None}
CASES=[
    dict(name='rusty_s2',room=35,gems=['rusty']),
    dict(name='rusty_greed_s3',room=60,gems=['rusty','greed']),
    dict(name='rusty_moon_s3',room=60,gems=['rusty','moonstone']),
    dict(name='rusty_greasy_s3',room=60,gems=['rusty','greasy']),
    dict(name='rusty_greasy_time_s4',room=85,gems=['rusty','greasy','time_traveler']),
    dict(name='rusty_greasy_moon_s4',room=85,gems=['rusty','greasy','moonstone']),
    dict(name='rusty_rabbit_moon_s4',room=85,gems=['rusty','rabbit','moonstone']),
]


def progress(case,keys):
    """Explicit synthetic later-run recovery; rejected offers are recorded too."""
    gems=case['gems'];room=case['room']
    fillers=[x for x in ('explorer','devil','hick','blood','deceit','lodestone','thirsty','misadventurer') if x not in gems]
    history=gems+fillers[:2*len(gems)]
    return '\n'.join(['schema=1',f'room={room}','run_number=2','phase=recovery','resume_phase=doors',
        'hp_percent=0',f'keys={keys}','gems='+','.join(gems),'offered_gems='+','.join(history),
        'shops_seen='+(','.join(str(x) for x in range(2,(room-1)//25+1)) or 'none'),
        'trial_seen='+('true' if room>51 else 'false'),'doors=unknown',''])


class RustyRecovery:
    def __init__(self,env,profile,variant,greasy):
        if variant not in TARGETS:raise ValueError(variant)
        self.base=InteractionPolicy(env,profile,'base');self.ai=self.base.ai
        self.variant=variant;self.greasy=greasy;self.hours=float(profile['recovery_hours'])
        self.risk=RecoveryThresholds(env,profile,'wait_one') if variant=='one_hit' else None

    def apply(self,obs,masks,proposed):
        actions,targets,reasons=self.base.apply(obs,masks,proposed)
        if self.variant=='control':return actions,targets,reasons
        f=lambda n:self.base.flag(obs,n);v=lambda n:self.base.v(obs,n)
        room=np.rint(v('room_div100')*100).astype(int);hp=v('hp')
        active=f('phase.recovery')&f('owned_gem.rusty')&(f('owned_gem.greasy')==self.greasy)&(room%25!=0)
        active&=(actions==self.ai['reenter'])|(actions>=self.ai['wait_hp_20'])
        if not active.any():return actions,targets,reasons
        goal=(self.risk.health_targets(obs,np.full(len(obs),.2))[0] if self.risk else np.full(len(obs),TARGETS[self.variant]))
        can_enter=masks[:,self.ai['reenter']]
        late=can_enter&((goal-hp)*self.hours+1/3600>v('hours_left_div168')*168)
        ready=active&can_enter&((hp>=goal-1e-6)|late)
        wait=active&~ready
        if wait.any():
            waits=masks[wait,self.ai['wait_hp_20']:]
            if not waits.any(axis=1).all():raise RuntimeError('No legal Rusty recovery wait')
            actions[wait]=waits.argmax(axis=1)+self.ai['wait_hp_20'];targets[wait]=goal[wait]
        actions[ready]=self.ai['reenter']
        if not masks[np.arange(len(obs)),actions].all():raise RuntimeError('Illegal Rusty recovery action')
        return actions,targets,reasons
