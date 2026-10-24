"""The frozen neural adviser for visible simulator JSON. No sampling or RNG calls."""
from pathlib import Path
import threading
import numpy as np
from .practical import Practical
from .player_ideas import profile_values
from .visual_advice import annotate, correct_action
from .visual_ratings import rate_stones

ROOT=Path(__file__).resolve().parents[2]
CHECKPOINT=ROOT/'models/adviser-v6-2026-10-24.pt'
CHECKPOINT_SHA='896e593f5d74b0fd1c75f996f6090b48f1bffdbbec78f0dcb4a1b694455fd9e0'

def encode(o,names,actions):
    x={n:0. for n in names}
    def put(n,v):
        if n not in x:raise ValueError('Unknown neural feature: '+n)
        x[n]=float(v)
    def category(prefix,value):
        if value is not None:put(prefix+'.'+value,1)
    phase=o['phase'];visible=o['resume_phase'];room=o['room'];turn=o['turn'];maximum=o['max_actions']
    category('phase',phase);category('resume_phase',visible)
    category('stage',str(min(4,(room-1)//25+1)))
    fields={'terminal':o['terminal'],'hp':o['hp'],'keys_div20':o['keys']/20,'room_div100':room/100,
        'first_event_run':o['run_number']==1,'run_number_div10':o['run_number']/10,'completed_runs_div10':o['completed_runs']/10,
        'turn_div100':turn/100,'elapsed_hours_div168':o['elapsed_hours']/168,'hours_left_div168':o['hours_left']/168,
        'deadline_hours_div168':(o['hours_left']+o['elapsed_hours'])/168,'run_elapsed_hours_div24':o['run_elapsed_hours']/24,
        'budget_div500':(o['mushrooms_left']+o['mushrooms_spent'])/500,'mushrooms_left_div500':o['mushrooms_left']/500,
        'mushrooms_spent_div500':o['mushrooms_spent']/500,'run_mushrooms_spent_div100':o['run_mushrooms_spent']/100,
        'mushrooms_found_div100':o['mushrooms_found']/100,'recovery_mushrooms_div500':o['recovery_mushrooms']/500,
        'reroll_mushrooms_div100':o['reroll_mushrooms']/100,'heal_step_price_div100':o['heal_step_price']/100,
        'heal_full_price_div100':o['heal_full_price']/100,'paid_heals_div20':o['paid_heals']/20,
        'shop_rerolls_div20':o['shop_rerolls']/20 if visible in ('shop','curse_shop') else 0,
        'reroll_shop_visits_div20':o['reroll_shop_visits']/20,'reroll_bank_used_div100':o['reroll_bank_used']/100,
        'trial_depth_div5':o['trial_depth']/5,'trial_seen':o['trial_seen'],'pending_trial_reward_div5':o['pending_trial_reward']/5,
        'flooded_seconds_div10':o.get('flooded_seconds',0)/10,'deaths_div100':o['deaths']/100,'actions_div10000':o['actions']/10000,
        'action_limit_left_fraction':(maximum-min(maximum,o['actions']))/maximum if maximum else 0,
        'epics_div100':o['epics']/100,'legendaries_div10':o['legendaries']/10,'gold_reward_units_div100':o['gold_reward_units']/100,
        'lucky_coins_div100':o['lucky_coins']/100}
    for n,v in fields.items():put(n,v)
    for i,value in enumerate(o['shops_seen'],1):put(f'shops_seen.{i}',value)
    resources=('wood','stone','souls','metal','arcane','hourglasses')
    for n,v in zip(resources,o['resources']):put(f'resources.{n}_div100',v/100)
    if visible=='encounter' and o.get('encounter')=='sated_chest' and o.get('donated_resource') is not None:
        category('donated_resource','none' if o['donated_resource']<0 else resources[o['donated_resource']])
    for gem in o['gems']:category('owned_gem',gem)
    for gem in o['offered_gems']:category('offered_gem',gem)
    def effect(prefix,e):
        put(prefix+'.present',1);category(prefix+'.kind',e['kind']);put(prefix+'.magnitude',e['magnitude'])
        put(prefix+'.remaining_div10',e['remaining']/10);put(prefix+'.starts_in_turns_div10',(e['starts_at']-turn)/10)
        put(prefix+'.active',e['remaining']>0 and e['starts_at']<=turn);category(prefix+'.clock',e['clock'])
    for group in ('blessings','curses'):
        for i,e in enumerate(o[group]):effect(f'{group}.{i}',e)
    for i,d in enumerate(o.get('doors',[])):
        prefix=f'doors.{i}';category(prefix+'.kind',d['kind'])
        for key in ('trap','cursed_trap','available'):put(prefix+'.'+key,d[key])
        put(prefix+'.key_cost_div6',d['key_cost']/6)
    category('encounter',o.get('encounter'))
    for i,offer in enumerate(o.get('offers',[])):
        put(f'offers.{i}.keys_div6',offer['keys']/6);effect(f'offers.{i}.effect',offer['effect'])
    for i,g in enumerate(o.get('gem_offers',[])):category(f'gem_offers.{i}',g)
    mapped={};mask=np.zeros((1,len(actions)),bool);seen=[]
    for i,name in enumerate(actions):
        if name.startswith('wait_'):
            if name=='wait_deadline':hours=o['hours_left']
            else:
                if name=='wait_boss_safe' and room%25:continue
                target=min(1,max(.2,o['boss_damage_max']+.001)) if name=='wait_boss_safe' else int(name.rsplit('_',1)[1])/100
                hours=min(o['hours_left'],max(0,target-o['hp'])*o['scenario']['recovery_hours'])
            a=next((a for a in o['legal_actions'] if a['kind']=='wait' and abs(a['value']-hours)<=1e-10),None)
        else:
            kind=name;index=0
            for prefix,native in (('door_','choose_door'),('buy_','buy'),('gem_','choose_gem')):
                if name.startswith(prefix):kind=native;index=int(name[-1])
            a=next((a for a in o['legal_actions'] if a['kind']==kind and a['index']==index),None)
        if a is not None and not any(a['kind']==b['kind'] and a['index']==b['index'] and abs(a['value']-b['value'])<=1e-10 for b in seen):
            mask[0,i]=True;mapped[i]=a;seen.append(a)
    return np.array([[x[n] for n in names]],np.float32),mask,mapped

class VisualPolicy:
    def __init__(self):
        import torch
        from train_rl import load_checkpoint,sha
        if sha(CHECKPOINT)!=CHECKPOINT_SHA:raise ValueError('Visual policy checkpoint identity changed')
        torch.set_num_threads(2);self.torch=torch
        saved,self.model=load_checkpoint(CHECKPOINT,torch.device('cpu'));self.model.eval()
        schema=saved['metadata']['environment_signature']['schema'];self.names=schema['observation_names'];self.actions=schema['action_names']
        self.rules={};self.profiles={}

    def advise(self,o,config,profile):
        if o['terminal']:return
        key=str(profile)
        if key not in self.rules:
            # Learned actions plus the tested soldier-search and OHW downgrade
            # guards. No mandatory refresh allowance, key reserve or wait gate.
            self.profiles[key]=profile_values(profile)
            self.rules[key]=Practical(self.names,self.actions,'learned',self.profiles[key])
        if hasattr(self.model,'shop_config'):
            raise ValueError('The unrestricted adviser cannot load a shop-constrained policy')
        obs,mask,mapping=encode(o,self.names,self.actions)
        with self.torch.inference_mode():
            proposed=self.model.actions(self.torch.as_tensor(obs),self.torch.as_tensor(mask),greedy=True)[0].numpy()
        actual,_,_=self.rules[key].apply(obs,mask,proposed);trained=mapping[int(actual[0])]
        annotate(o)
        chosen,reason,rule=correct_action(o,trained)
        if o['phase']=='recovery':
            from .late_recovery import recovery_action
            chosen,reason,rule=recovery_action(o,chosen,self.profiles[key])
        o['suggestions'].append({'strategy':'neural','action':chosen})
        o['neural_adviser']={'checkpoint_sha256':CHECKPOINT_SHA,'case':'unrestricted_budget_policy','gold_per_unit':10000000,
                             'trained_action':trained,'adjustment':rule,'reason':reason}
        if o['phase']=='gems':
            o['stone_ratings']=rate_stones(self,o,obs,self.profiles[key])

_policy=None
_lock=threading.Lock()
def warmup():
    global _policy
    with _lock:
        if _policy is None:_policy=VisualPolicy()

def advise(o,config,profile):
    warmup()
    with _lock:
        _policy.advise(o,config,profile)
