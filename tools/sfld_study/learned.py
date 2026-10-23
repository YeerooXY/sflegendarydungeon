"""Experimental visible-state projection for the first bounded automatic pilot.

Start in room one's decision screen, with no earlier dungeon actions. History
is persisted by Controller, never reconstructed from a server seed. This is a
deployment adapter, not a claim that the trained simulator matches live play.
"""
from copy import deepcopy
import math
from pathlib import Path

import numpy as np

from .automatic import CONTAINERS, EFFECTS, GEMS
from .schema import StudyError, action
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[2]


def update_history(v, memory):
    if memory is None:
        if (v['room']!=1 or v['status']!=1 or v['run_number_event']!=1 or
            v['gems'] or v['blessings'] or v['curses'] or v['keys']!=0):
            raise StudyError('Start this pilot at the first room choice of the event; earlier history is required otherwise.')
        memory={'schema':1,'room':1,'turn':0,'actions':0,'offered':[], 'shops':[],
                'effect_starts':{},'gold_units':0,'gold_rewards':0,'mushrooms_found':0,
                'run_start_seconds':v['elapsed_seconds'],'last':None,
                'budget_cap':v.get('budget_remaining',0),'paid_shops':[],'reroll_spent':0,'shop_rerolls':0}
    m=deepcopy(memory)
    if m.get('schema')!=1 or v['run_number_event']!=1:
        raise StudyError('Run transition needs pilot validation before automatic restart.')
    if not m['room']<=v['room']<=m['room']+1:
        raise StudyError('Room history is discontinuous; stop and reconcile.')
    if v['room']!=m['room']:
        m['turn']+=1; m['room']=v['room'];m['shop_rerolls']=0
    if v['gem_offers']:
        m['offered']=sorted(set(m['offered'])|set(v['gem_offers']))
    if v['encounter_type']==315 and v['stage'] not in m['shops']:
        m['shops'].append(v['stage'])
    last=m['last']
    if v['encounter_type']==315 and v['offers'] and any(o['key_price']>0 for o in v['offers']) and v['room'] not in m['paid_shops']:
        m['paid_shops'].append(v['room'])
    if last and last['choice']['kind']=='interact' and last['choice']['code']==80:
        m['reroll_spent']+=1;m['shop_rerolls']+=1
    if last and last['choice']['kind']=='interact' and last['choice']['code']==60 and last['encounter']==901:
        # One relative payout count, not account balance / ten million. Exact
        # live payout scaling is not calibrated; actual silver stays in the journal.
        m['gold_units']+=1; m['gold_rewards']+=1
    if last and v['mushrooms'] is not None and last['mushrooms'] is not None:
        m['mushrooms_found']+=max(0,v['mushrooms']-last['mushrooms'])
    for group in ('blessings','curses'):
        if v[group] is None: raise StudyError('Missing active effects.')
        starts={}
        for effect in v[group]:
            eid=str(effect['id']); old=m['effect_starts'].get(group,{}).get(eid)
            if old and effect['remaining']<=old['remaining'] and effect['power']==old['power']:
                start=old['start']
            else:
                start=m['turn']+(1 if last and last['room']==v['room'] else 0)
            starts[eid]={'start':start,'remaining':effect['remaining'],'power':effect['power']}
        m['effect_starts'][group]=starts
    return m


def encode(v, memory, names, actions):
    """Named encoding and exact menu mapping; no native random game is advanced."""
    if not isinstance(v.get('budget_remaining'),int) or not 0<=v['budget_remaining']<=5:
        raise StudyError('The pilot accepts a zero-to-five mushroom reroll budget.')
    for key in ('room','stage','hp_current','hp_max','keys','gems','elapsed_seconds','event_seconds_left'):
        if v.get(key) is None: raise StudyError('Missing live policy input: '+key)
    if not v['hp_max'] or not 1<=v['room']<=100: raise StudyError('Invalid room or health.')
    m=update_history(v,memory)
    features={n:0. for n in names}; mapped={}
    def put(n,value):
        if n not in features: raise StudyError('Policy feature schema changed: '+n)
        features[n]=float(value)
    def map_action(name,chosen):
        if name not in actions: raise StudyError('Policy action schema changed.')
        mapped[actions.index(name)]=chosen
    typ=v['encounter_type']; phase=v['screen_kind']
    if typ==323: phase='curse_shop'
    if phase not in ('doors','encounter','shop','curse_shop','gems','recovery'):
        raise StudyError('This transition must be handled before policy inference.')
    # While healing, the simulator shows the screen the character returns to.
    resume=v.get('resume_phase') if phase=='recovery' else phase
    if resume not in ('doors','encounter'):resume=phase
    put('phase.'+phase,1);put('resume_phase.'+resume,1)
    hp=v['hp_current']/v['hp_max']; elapsed=v['elapsed_seconds']/3600
    for name,val in {'hp':hp,'keys_div20':v['keys']/20,'room_div100':v['room']/100,
        'stage.'+str(v['stage']):1,'first_event_run':1,'run_number_div10':.1,
        'turn_div100':m['turn']/100,'elapsed_hours_div168':elapsed/168,
        'hours_left_div168':v['event_seconds_left']/604800,
        'deadline_hours_div168':(v['elapsed_seconds']+v['event_seconds_left'])/604800,
        'run_elapsed_hours_div24':(v['elapsed_seconds']-m['run_start_seconds'])/86400,
        'heal_step_price_div100':(v['recovery'] or {}).get('partial_cost',None) or 10,
        'heal_full_price_div100':math.ceil((1-hp)*48-1e-12)/100,
        'actions_div10000':m['actions']/10000,
        'action_limit_left_fraction':(100000-m['actions'])/100000,
        'gold_reward_units_div100':m['gold_units']/100,
        'mushrooms_found_div100':m['mushrooms_found']/100}.items(): put(name,val)
    # Partial price is observed, or ten at the fresh unpaid event origin.
    put('heal_step_price_div100',features['heal_step_price_div100']/100)
    for n,value in {'budget_div500':m['budget_cap']/500,'mushrooms_left_div500':v['budget_remaining']/500,
                   'mushrooms_spent_div500':m['reroll_spent']/500,'reroll_mushrooms_div100':m['reroll_spent']/100,
                   'run_mushrooms_spent_div100':m['reroll_spent']/100,'reroll_bank_used_div100':m['reroll_spent']/100,
                   'shop_rerolls_div20':m['shop_rerolls']/20,'reroll_shop_visits_div20':len(m['paid_shops'])/20}.items():put(n,value)
    for s in m['shops']: put('shops_seen.'+str(s),1)
    for g in v['gems']: put('owned_gem.'+GEMS[g],1)
    for g in m['offered']: put('offered_gem.'+GEMS[g],1)
    def effect(prefix,e,start):
        eid=e.get('id',e.get('effect_id')); kind=EFFECTS[eid]
        magnitude=1 if eid in (2,4,5) else 2 if eid==105 else e['power']/100
        remaining=e.get('remaining',e.get('duration'))
        if remaining is None or e['power'] is None: raise StudyError('Unknown effect strength/duration.')
        put(prefix+'.present',1);put(prefix+'.kind.'+kind,1);put(prefix+'.magnitude',magnitude)
        put(prefix+'.remaining_div10',remaining/10);put(prefix+'.starts_in_turns_div10',(start-m['turn'])/10)
        put(prefix+'.active',remaining>0 and start<=m['turn'])
        put(prefix+'.clock.'+{4:'trap',5:'door',6:'fight'}.get(eid,'room'),1)
    for group in ('blessings','curses'):
        for i,e in enumerate(v[group]): effect(f'{group}.{i}',e,m['effect_starts'][group][str(e['id'])]['start'])
    menu=v['legal_actions'] or []
    for a in menu:
        if a['mushroom_quote']!=0 and not (a['kind']=='interact' and a['code']==80 and a['mushroom_quote']==1 and a['quote_source']=='ui'):
            raise StudyError('Only the inspected one-mushroom shop reroll is supported.')
    menu=[a for a in menu if a['mushroom_quote']<=v['budget_remaining'] and
          (not a['mushroom_quote'] or v['mushrooms'] is not None and v['mushrooms']>=a['mushroom_quote'])]
    def door_features():
        for d in v['doors']:
            prefix='doors.'+str(d['index']); put(prefix+'.kind.'+d['kind'],1)
            put(prefix+'.trap',d['trap']>0);put(prefix+'.cursed_trap',d['trap_kind']=='curse')
            put(prefix+'.key_cost_div6',d['key_cost']/6);put(prefix+'.available',not d['blocked'])
    if phase=='recovery':
        # Doors are encoded only when the save showed both destination doors.
        if resume=='doors' and len(v.get('doors') or [])==2: door_features()
        if resume=='encounter': put('encounter.boss',1)
        for name,a in v['recovery_menu']:
            if a in menu: map_action(name,a)
    elif phase=='doors':
        if len(v['doors'] or [])!=2: raise StudyError('Expected two visible choices.')
        door_features()
        for a in menu:
            side=(a['code']&3)-1
            if side not in (0,1): raise StudyError('Unknown door action.')
            map_action('door_'+str(side),a)
    elif phase in ('shop','curse_shop'):
        for i,o in enumerate(v['offers']):
            price=o['key_price'] if phase=='shop' else o['keys_received']
            put(f'offers.{i}.keys_div6',price/6); effect(f'offers.{i}.effect',o,0)
            for a in menu:
                if a.get('effect_id')==o['effect_id']:map_action('buy_'+str(i),a)
        for a in menu:
            if a.get('code')==51:map_action('skip',a)
            if a.get('code')==80:map_action('reroll',a)
    elif phase=='gems':
        for i,g in enumerate(v['gem_offers']):
            put('gem_offers.'+str(i)+'.'+GEMS[g],1)
            map_action('gem_'+str(i),next(a for a in menu if a.get('gem_id')==g))
    else:
        encounter='monster' if typ in (1,2,3) else 'boss' if typ in (4,5) else None
        if typ==100:encounter=CONTAINERS[v['encounter_variant']][1]
        if encounter is None: raise StudyError('Unmapped encounter cannot be inferred.')
        put('encounter.'+encounter,1)
        for a in menu:
            name={20:'fight',21:'flee',40:'interact',42:'skip'}.get(a.get('code'))
            if name is None:raise StudyError('Unmapped encounter action.')
            map_action(name,a)
    if not mapped: raise StudyError('No mapped policy actions.')
    vector=np.asarray([[features[n] for n in names]],dtype=np.float32)
    mask=np.zeros((1,len(actions)),dtype=bool);mask[0,list(mapped)]=True
    return vector,mask,mapped,m


class LearnedPilot:
    def __init__(self):
        import torch
        from train_rl import load_checkpoint,sha
        from sfld_rl.practical import Practical
        from sfld_rl.player_ideas import profile_values
        if sha(CHECKPOINT)!=CHECKPOINT_SHA: raise StudyError('Pilot checkpoint identity changed.')
        self.torch=torch;self.device=torch.device('cpu')
        saved,self.model=load_checkpoint(CHECKPOINT,self.device);self.model.eval()
        schema=saved['metadata']['environment_signature']['schema']
        self.names=schema['observation_names'];self.actions=schema['action_names']
        # Share the visual adviser's selected checkpoint and the exact tested
        # soldier-search rule. Never add a shop gate to an unrestricted model.
        self.rule=Practical(self.names,self.actions,'learned',profile_values(ROOT/'profiles/synthetic.profile'))

    def choose(self,v,memory):
        if memory is not None and memory.get('checkpoint_sha256')!=CHECKPOINT_SHA:
            raise StudyError('Recorded policy history belongs to a different checkpoint; reconcile before continuing.')
        # UI bookkeeping steps do not correspond to additional simulator turns.
        menu=v['legal_actions'] or []
        if v['status']==0:
            if (memory is not None or v['run_number_event']!=1 or len(menu)!=1 or
                menu[0]['kind']!='start' or v['gems'] or v['blessings'] or v['curses']):
                raise StudyError('Only fresh event entry is supported by this pilot.')
            return {'action':menu[0],'memory':None}
        bookkeeping=(v['status']==100 or v['encounter_type'] in (50,200,250,901,902,903,905,906,907,908,909))
        if bookkeeping:
            if len(menu)!=1:raise StudyError('Ambiguous bookkeeping action.')
            m=update_history(v,memory);chosen=menu[0];reason='client_transition'
        else:
            obs,mask,mapping,m=encode(v,memory,self.names,self.actions)
            with self.torch.inference_mode():
                proposed=self.model.actions(self.torch.as_tensor(obs),self.torch.as_tensor(mask),greedy=True)[0].numpy()
            picked,_,changed=self.rule.apply(obs,mask,proposed)
            chosen=mapping[int(picked[0])]
            reason='guaranteed_escape_soldier_search' if np.any(changed) else 'learned'
            m['actions']+=1
        m['last']={'room':v['room'],'encounter':v['encounter_type'],'choice':chosen,'mushrooms':v['mushrooms']}
        m['reason']=reason;m['checkpoint_sha256']=CHECKPOINT_SHA
        return {'action':chosen,'memory':m}


_policy=None
def choose(visible,memory):
    global _policy
    if _policy is None:_policy=LearnedPilot()
    return _policy.choose(visible,memory)
