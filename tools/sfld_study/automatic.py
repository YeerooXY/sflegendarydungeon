"""Bounded automatic pilot, projected from the inspected client UI logic.

Static inspection is not live validation. Unknown screens stop with their numeric
capture preserved. Future step values and server random seeds never reach policy.
"""
from .pilot import ReviewedPilotDriver
from .protocol import AUTOMATIC_CLIENT_VERSION, capture, decode, numeric
from .schema import StudyError, action, observation, timestamp

GEMS = {2:'rabbit',18:'moonstone',9:'spying',6:'pendant',8:'greasy',11:'gambler',
        3:'greed',20:'hero',19:'time_traveler',15:'thirsty',22:'pearl',13:'blood',
        4:'explorer',17:'misadventurer',21:'devil',7:'deceit',10:'lodestone',
        1:'bull',16:'hick',23:'rusty'}
EFFECTS = {1:'raider',2:'one_hit',3:'escape',4:'disarm',5:'lockpick',6:'key_moment',
           7:'elixir',8:'recovery',101:'broken_armor',102:'poison',103:'clumsy',
           104:'gold_hangover',105:'hard_lock'}
DOORS = {1:'monster',2:'monster',3:'monster',4:'boss',5:'boss',1000:'wall',
         1001:'mystery',1002:'locked',1003:'unlocked',1004:'epic',1005:'double_locked',
         1006:'golden',1007:'sacrifice',1008:'cursed',1009:'shop',1010:'blessing',1011:'destiny'}
CONTAINERS = {0:('bronze_chest','bronze'),1:('silver_chest','silver'),2:('epic_chest','epic'),
              100:('wooden_box','crate'),101:('wooden_box','crate'),102:('wooden_box','crate'),
              200:('other','crate'),201:('other','crate'),202:('other','crate'),
              300:('warrior_skeleton','skeleton'),301:('mage_skeleton','skeleton'),
              400:('barrel','barrel'),500:('mimic','mimic'),600:('sacrificial_chest','sacrifice_chest'),
              601:('cursed_chest','cursed_chest'),602:('prize_chest','prize'),603:('sated_chest','sated_chest')}


def interaction(code):
    return action({'kind':'interact','code':code})


def project(values, version=AUTOMATIC_CLIENT_VERSION):
    """Return visible facts and a menu from the exactly versioned client UI."""
    if version != AUTOMATIC_CLIENT_VERSION:
        raise StudyError('The automatic pilot requires its exact inspected client version.')
    visible, raw, reason = decode(values, version)
    if reason != 'ui_menu_requires_review': return visible, raw, reason
    save = numeric(values['iadungeonsave'],50)
    status = visible['status']
    # Dungeons.get_VisualRoom: Room+1 at Decision, Dead, RoomCompleted.
    visible['room'] = save[17] + (status in (1,100,101))
    visible['stage'] = save[16]+1
    if 10 <= status <= 12:
        # ONLY the currently revealed step. Other step payloads stay private.
        visible['encounter_variant'] = save[22+status-10]
    if visible['gems'] is None or any(g not in GEMS for g in visible['gems']):
        return observation(visible),raw,'unknown_held_stones'
    if any(e['id'] not in EFFECTS for group in ('blessings','curses') for e in visible[group]):
        return observation(visible),raw,'unknown_effect'
    active = {e['id'] for e in visible['blessings']+visible['curses'] if e['remaining']>0}
    menu=[]
    if status == 1:
        visible['screen_kind']='doors'; doors=[]
        for i in range(2):
            typ,trap=save[19+i],save[25+i]
            if typ not in DOORS or trap not in range(10):
                return observation(visible),raw,'unsupported_door_or_trap'
            kind=DOORS[typ]
            # Hard-lock/crowbar command interaction still needs live validation;
            # stop instead of guessing whether to attach WithKey.
            if kind in ('locked','double_locked','epic') and active.intersection({5,105}):
                return observation(visible),raw,'modified_key_door_requires_review'
            cost=2 if kind=='double_locked' else int(kind in ('locked','epic'))
            blocked=kind=='wall' or visible['keys']<cost
            doors.append({'index':i,'type':typ,'kind':kind,'trap':trap,'key_cost':cost,
                          'blocked':blocked,'trap_kind':'none' if not trap else 'curse' if 11 in visible['gems'] else 'damage'})
            if not blocked: menu.append(interaction(i+1+(4 if cost else 0)+(8 if trap and 4 in active else 0)))
        visible['doors']=doors
    elif status == 100:
        menu=[interaction(70)]
    elif status==0:
        visible['screen_kind']='overview'
        if (visible['level'] is None or visible['level']<50 or not visible['hp_max'] or
            visible['hp_current']*5<visible['hp_max'] or visible['theme'] is None):
            return observation(visible),raw,'entry_state_incomplete_or_not_ready'
        menu=[action({'kind':'start','theme':visible['theme']})]
    elif status in (101,102):
        return observation(visible),raw,'recovery_or_restart_requires_review'
    elif 10 <= status <= 12:
        typ=visible['encounter_type']; variant=visible['encounter_variant']
        visible['screen_kind']='encounter'
        if typ in (1,2,3): menu=[interaction(20),interaction(21)]
        elif typ in (4,5): menu=[interaction(20)]
        elif typ==50: menu=[interaction(31 if 4 in active else 30)]
        elif typ==100:
            if variant not in CONTAINERS: return observation(visible),raw,'unknown_container'
            visible['container_kind']=CONTAINERS[variant][0]
            visible['visual_variant']=variant
            if variant>=500:
                return observation(visible),raw,'special_container_requires_review'
            menu=[interaction(40),interaction(42)]
        elif typ in (200,250,902): menu=[interaction(70)]
        elif typ in (901,903,905,906,907,908,909): menu=[interaction(60)]
        elif typ==900:
            return observation(visible),raw,'item_inventory_requires_review'
        elif typ==904:
            visible['screen_kind']='gems'
            offered=visible['gem_offers']
            if not offered or len(offered)!=3 or len(set(offered))!=3 or any(g not in GEMS for g in offered):
                return observation(visible),raw,'unknown_stone_offer'
            menu=[action({'kind':'choose_gem','gem_id':g}) for g in offered]
        elif typ in (315,323):
            visible['screen_kind']='shop'
            offers=visible['offers']
            if offers is None or len(offers)!=2 or any(o['effect_id'] not in EFFECTS for o in offers):
                return observation(visible),raw,'unknown_shop_offers'
            for o in offers:
                if typ==315 and o['key_price']<=visible['keys']:
                    menu.append(action({'kind':'buy_blessing','effect_id':o['effect_id'],'key_price':o['key_price']}))
                elif typ==323:
                    menu.append(action({'kind':'buy_curse','effect_id':o['effect_id'],'keys_received':o['keys_received']}))
            menu.append(interaction(51))
            # MerchantShowOffer checks Mushrooms >= 1 and at least one paid
            # offer. OnMerchantRerollClicked sends interaction 80. Key saving
            # is a policy decision; opening/curse shops never roll.
            if (typ==315 and any(o['key_price']>0 for o in offers)
                and visible['mushrooms'] is not None and visible['mushrooms']>=1):
                menu.append(action({'kind':'interact','code':80,'mushroom_quote':1,'quote_source':'ui'}))
        else: return observation(visible),raw,'special_room_requires_review'
    else: return observation(visible),raw,'unknown_status'
    visible['legal_actions']=menu
    return observation(visible),raw,'automatic_menu' if menu else 'no_available_action'


class AutomaticPilotDriver(ReviewedPilotDriver):
    """Reuse the journal-before-send and no-retry execution boundary."""
    def _decode(self, subject, values):
        version=self.store.meta('game_version') or AUTOMATIC_CLIENT_VERSION
        try:
            visible,raw,reason=project(values,version)
        except StudyError:
            self.store.observe(subject,{'game_version':version},private=capture(values))
            raise
        row=self.store.subject(subject)
        if row['start_utc'] and 'timestamp' in values:
            server_time=numeric(values['timestamp'],1)[0]
            visible['elapsed_seconds']=max(0,server_time-timestamp(row['start_utc']).timestamp())
        completed=self.store.db.execute("SELECT COUNT(*) FROM runs WHERE subject=? AND state='complete'",(subject,)).fetchone()[0]
        visible['run_number_event']=1+completed
        return visible,raw,reason

    def observe(self, subject):
        if not 0<=self.store.meta('global_budget')<=10 or not 0<=self.store.subject(subject)['budget']<=5:
            raise StudyError('This initial automatic pilot allows at most five reroll mushrooms per character, ten in total.')
        if self.store.db.execute("SELECT 1 FROM decisions WHERE subject=? AND state='not_applied' LIMIT 1",(subject,)).fetchone():
            raise StudyError('A reconciled unapplied action requires policy-history review.')
        snapshot,raw=self.snapshot(subject)
        if snapshot['review_reason']!='automatic_menu':
            raise StudyError('Automatic pilot paused: '+snapshot['review_reason']+'. Snapshot saved.')
        self.reviewed_menu=snapshot['observation']['legal_actions']
        return snapshot['observation'],raw
