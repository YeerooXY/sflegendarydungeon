"""Connect an interactive process's exact public state to final-stone rollouts."""
import hashlib
import threading
import numpy as np
from .effect_lab import EffectLab
from .env import VectorEnv
from .stone_advisor import StoneAdvisor
from .visual_policy import encode

_lock = threading.Lock()
_adviser = None


def compare(planning_text, observation, library, profile, config):
    global _adviser
    # One model per local host; serialize expensive comparisons across windows.
    # This seed depends only on the visible state, never the game seed or RNG.
    seed = int(hashlib.sha256(('sfld-final-stone-runtime-v1|'+planning_text).encode()).hexdigest()[:16],16)
    with _lock:
        if _adviser is None: _adviser = StoneAdvisor()
        with VectorEnv(library,[profile],1,budgets=[config['budget']],auto_reset=False,
                       deadline_hours=config['deadline_hours'],max_actions=observation['max_actions'],
                       event_start_hour=config['event_start_hour'],sleep_until_hour=config['sleep_until_hour']) as env:
            env.reset();lab=EffectLab(env)
            with lab.planning_fixture(planning_text) as snapshot: lab.restore(0,snapshot,0)
            obs,masks=lab.observe();native=lab.visible()
            # Presentation outcomes and suggestion lists can differ. All other
            # native public fields and the actor inputs must match exactly.
            fields=set(native)-{'outcomes','suggestions'}
            if any(native[k]!=observation.get(k) for k in fields):
                changed=[k for k in fields if native[k]!=observation.get(k)]
                raise RuntimeError('Planning state differs from the current game: '+', '.join(sorted(changed)))
            expected,expected_masks,_=encode(observation,env.observation_names,env.action_names)
            if not np.array_equal(obs,expected) or not np.array_equal(masks,expected_masks):
                raise RuntimeError('Planning observations or actions differ from the current game')
            result,_=_adviser.compare(env,samples=config['stone_advice_samples'],planning_seed=seed)
    keys=('action','estimated_best','supported_best','contenders','rankings','confidence','samples_per_offer',
          'trajectories','seconds','planning_seed','checkpoint_sha256','profile_sha256','library_sha256','objective')
    answer=dict(status='ready',**{k:result[k] for k in keys})
    # Browser JSON numbers cannot exactly represent all unsigned 64-bit seeds.
    answer['planning_seed']=str(answer['planning_seed'])
    return answer
