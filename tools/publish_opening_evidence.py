"""Record targeted Gorgoss footage and maintainer screenshots without redistributing art."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run():
    work=ROOT/'out/vods/gorgoss-details-2026-10-12/frames'
    frames=[json.loads((work/f'{i:03}/index.json').read_text()) for i in range(3)]
    def ev(i,n,role):return dict(frames[i]['frames'][n],role=role)
    records=[
        {'id':'gorgoss_opening_destiny_2026_10_12','source_url':'https://www.twitch.tv/videos/2878333727?t=43m38s',
         'room':1,'gems':[],'pair':['destiny','monster'],'wheel_reward':'curse','initial_contents':'bronze_chest',
         'evidence':[ev(0,2,'pair'),ev(0,20,'wheel_reward'),ev(0,25,'initial_contents')],
         'run_class':'first_event_run_supported_by_subsequent_free_room_five_shop',
         'sampling':'targeted_counterexample; excluded from the outcome-blind wheel sample'},
        {'id':'gorgoss_crate_one_hit_2026_10_12','source_url':'https://www.twitch.tv/videos/2878333727?t=45m0s',
         'room':4,'gems':[],'interaction':'crate_opening','reward':'one_hit','remaining':8,
         'evidence':[ev(1,29,'blessing_received'),ev(1,62,'subsequent_shop_and_active_effect')],
         'sampling':'targeted_counterexample; not a container-rate sample'},
        {'id':'gorgoss_free_shop_2026_10_12','source_url':'https://www.twitch.tv/videos/2878333727?t=45m15s',
         'room':5,'active_one_hit_remaining':8,
         'offers':[{'kind':'one_hit','remaining':4,'keys':0},{'kind':'escape','remaining':5,'keys':0}],
         'evidence':[ev(1,62,'offers_and_active_effect')],
         'claim':'The eight-room effect was already active; the free shop offers four, not eight.'},
        {'id':'gorgoss_opening_blessing_2026_10_12','source_url':'https://www.twitch.tv/videos/2878333727?t=99m20s',
         'room':3,'gems':[],'pair':['blessing','monster'],'run_class':'not_independently_established',
         'evidence':[ev(2,65,'pair_room_counter_and_stones')],
         'claim':'A Blessing Door appears within the first five rooms; this frame alone does not establish event run number.'},
    ]
    for number,claim,room,gems in [(59,'Blessing Door beside visible monster at room one.',1,[]),
                                   (60,'Mystery beside Blessing Door while Pendant and Thirsty are held.',71,['pendant','thirsty'])]:
        p=ROOT.parent/f'image copy {number}.png'
        records.append({'id':f'maintainer_image_{number}_2026_10_12','source_kind':'user_supplied_screenshot',
                        'local_filename':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                        'claim':claim,'room':room,'gems':gems,'run_class':'maintainer_report_only',
                        'redistributed':False})
    target=ROOT/'data/vods/opening-corrections-2026-10-12.jsonl'
    target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf8')
    inventory=ROOT/'docs/mechanics-evidence.json';data=json.loads(inventory.read_text())
    data['last_updated_on']='2026-10-12'
    data['sources']['opening_and_pendant_2026_10_12']={
        'kind':'native_video_and_maintainer_screenshots','source':'https://www.twitch.tv/videos/2878333727',
        'dataset':str(target.relative_to(ROOT)).replace('\\','/'),'implemented_in':'0.6.10',
        'observed':['Destiny and Blessing doors in the first five rooms','Free shop offers four-room OHW when eight already active','Pendant permits a special physical door beside mystery'],
        'maintainer_report':['No stage-one shop after the first completed event run'],
        'provisional':['Opening weights 80% mystery, 8% Destiny, 8% Blessing, 4% hungry','Pendant pair/style conditioning formula'],
        'trap_rule':'Existing first-five trap exclusion retained'}
    data['sources']['barrel_native_followup_2026_10_12']={
        'kind':'native_sequence_review','dataset':'data/vods/barrel-followup-2026-10-12.jsonl',
        'new_openings':32,'new_skips':1,'finding':'Time Traveler alone can yield a barrel blessing.',
        'working_formula':'1.5 times the base curse probability; provisional, not measured',
        'isolated_traveler_blessings':1,'isolated_traveler_openings':5}
    data['sources']['destiny_native_followup_2026_10_12']={
        'kind':'outcome_blind_offers_with_native_sequence_review','dataset':'data/vods/destiny-followup-2026-10-12.jsonl',
        'offers':35,'opened':12,'rewards':{'curse':7,'blessing':3,'lucky_coins':2},
        'exact_live_probabilities_established':False,'default_profile_changed':False}
    inventory.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    probe=json.loads((ROOT/'out/opening-one-hit-guard-2026-10-12.json').read_text())
    assert len(probe['rows'])==54 and all(r['remaining']==7 and r['actions'][-1]['kind']=='buy' and r['actions'][-1]['index']==1 for r in probe['rows'])
    (ROOT/'docs/opening-one-hit-guard-2026-10-12.json').write_text(json.dumps(probe,indent=2)+'\n')
    print({'targeted_records':len(records),'shop_challenge_positions':len(probe['rows'])})


if __name__=='__main__':run()
