"""Export explicitly reviewed native container facts; never classify footage."""
from pathlib import Path
import json,hashlib,collections,cv2,numpy as np

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'out/vods';OUT=BASE/'container-audit-2026-10-08'
# key, initial style, interaction, reward events, before/after HUD frame, proof frames.
# Gold listed after item cards is a sale, not a second loot roll.
FACTS=[
 ('O27','bronze','opened',['nothing'],0,9,[5,9]),
 ('O83','wooden_box','opened',['gold:1939797'],0,16,[6,9,16]),
 ('O93','silver','opened',['nothing'],0,13,[8,13]),
 ('O129','bronze','opened',['item:1','item_sale_gold:4000000'],0,29,[7,11,19,29]),
 ('O153','wooden_box','opened',['nothing'],0,10,[6,10]),
 ('O181','epic','opened',['item:1','item_sale_gold:4000000'],0,29,[8,12,19,29]),
 ('O226','bronze','opened',['nothing'],0,9,[6,9]),
 ('O235','bronze','opened',['item:1','item_sale_gold:4000000'],0,27,[7,11,18,27]),
 ('O245','bronze','opened',['item:1','item_sale_gold:2000000'],0,25,[5,10,16,25]),
 ('O266','wooden_box','opened',['persistent_blessing'],0,23,[6,8,12,15,19,23]),
 ('O284','silver','opened',['gold:14373478'],0,17,[8,11,17]),
 ('O338','wooden_box','opened',['gold:24726338'],0,15,[6,8,15]),
 ('O366','wooden_box','opened',['item:3','item_sale_gold:12000000'],0,26,[6,10,17,26]),
 ('R14','epic','opened',['item:3','item_pocketed:3'],0,28,[7,11,19,28]),
 ('R47','silver','opened',['gold:45000000'],0,16,[6,9,16]),
 ('R50','wooden_box','opened',['persistent_blessing','gold:24726338'],0,28,[6,8,14,21,28]),
 ('O389','wooden_box','opened',['gold:37089507'],0,16,[7,9,16]),
 ('O418','silver','opened',['gold:45000000'],0,14,[4,7,14]),
 ('O424','wooden_box','opened',['gold:30907922'],0,15,[6,8,15]),
 ('R80','wooden_box','opened',['item:3','item_sale_gold:10000000'],0,26,[6,10,16,26]),
 ('O430','silver','opened',['nothing'],0,12,[8,12]),
 ('O443','wooden_box','opened',['item:3','item_sale_gold:12000000'],0,26,[6,10,17,26]),
 ('O500','silver','awakened_mimic',[],0,20,[2,6,20]),
 ('O621','wooden_box','opened',['nothing'],0,10,[6,10]),
 ('O627','bronze','opened',['instant_healing:24448540','gold:37089507'],0,25,[8,10,16,19,25]),
 ('O315','wooden_box','opened',['gold:30907922'],0,14,[5,7,14]),
 ('O316','silver','skipped',[],0,11,[2,8,11]),
 ('O318','bronze','opened',['gold:24726338'],0,14,[5,7,14]),
 ('O324','barrel','opened',['curse_refresh'],0,22,[6,12,17,22]),
 ('O326','corpse','unknown',[],0,9,[2,6,9]),
 ('O335','silver','awakened_mimic',[],0,19,[2,5,19]),
]

# Manually inspected enlarged native before/after HUDs. Labels describe art,
# not an inferred canonical effect identity. A null counter is deliberately unread.
HUD={
 'O27':([('flexing_arms',8)],[('flexing_arms',8)],[],[]),
 'O83':([('armed_figure',None),('pointing_hand',2)],[('armed_figure',None),('pointing_hand',2)],[],[]),
 'O93':([],[],[],[]),
 'O129':([('hand_golden_key',8)],[('hand_golden_key',8)],[],[]),
 'O153':([],[],[],[]),'O181':([],[],[],[]),'O226':([],[],[],[]),
 'O235':([('gray_haired_figure',4)],[('gray_haired_figure',4)],[('green_face_bottle',4)],[('green_face_bottle',4)]),
 'O245':([],[],[],[]),
 'O266':([('pointing_hand',4),('gray_haired_figure',4)],[('brown_head_cluster',10),('pointing_hand',4),('gray_haired_figure',4)],[('gray_armor',6)],[('gray_armor',6)]),
 'O284':([],[],[],[]),
 'O338':([('armed_figure',None)],[('armed_figure',None)],[],[]),
 'O366':([('pointing_hand',1)],[('pointing_hand',1)],[('green_face_arrow',2)],[('green_face_arrow',2)]),
 'R14':([],[],[],[]),'R47':([],[],[],[]),'R50':([],[('hand_golden_key',4)],[],[]),
 'O389':([],[],[],[]),'O418':([('pointing_hand',4)],[('pointing_hand',4)],[],[]),
 'O424':([],[],[('green_face_arrow',4)],[('green_face_arrow',4)]),'R80':([],[],[],[]),
 'O430':([('hand_golden_key',1)],[('hand_golden_key',1)],[],[]),
 'O443':([('hand_golden_key',7)],[('hand_golden_key',7)],[],[]),
 'O500':([],[],[],[]),'O621':([('hand_golden_key',4)],[('hand_golden_key',4)],[],[]),
 'O627':([('hand_golden_key',4)],[('hand_golden_key',4)],[],[]),
 'O315':([('hand_silver_key',2),('gray_haired_figure',1)],[('hand_silver_key',1),('gray_haired_figure',1)],[],[]),
 'O316':([('hand_silver_key',1),('gray_haired_figure',1)],[('gray_haired_figure',1)],[],[]),
 'O318':([('gray_haired_figure',1)],[('gray_haired_figure',1)],[],[]),
 'O324':([],[],[('green_face_bottle',4)],[('green_face_bottle',10)]),
 'O326':([],[],[('green_face_bottle',9)],[('green_face_bottle',9)]),'O335':([],[],[],[]),
}

def main():
 indexes={}
 for folder in ['containers','greed-transitions']:
  for case in json.loads((OUT/folder/'index.json').read_text()):indexes[case['key']]=(folder,case)
 rows=[]
 for key,style,interaction,tags,before,after,proofs in FACTS:
  folder,case=indexes[key];ev=case['frames'];rewards=[]
  for tag in tags:
   kind,_,amount=tag.partition(':');event={'kind':kind}
   if amount:event['amount']=int(amount)
   rewards.append(event)
  ordinary=style in ['bronze','silver','wooden_box'] and interaction=='opened'
  rows.append({'schema':1,'observation_id':case['observation_id'],'source_id':'2881153716','source_start':ev[0]['source_seconds'],'source_end':ev[-1]['source_seconds'],
   'cohorts':['all_known_containers_in_99_eligible_selected_mystery_cases'] if folder=='containers' else ['complete_greed_stage_2_passage'],
   'context':case['context'],'doors':case['doors'],'selected_side':None,'selected_side_status':'not_audited',
   'initial_style':style,'interaction':interaction,'ordinary_loot_eligible':ordinary,'rewards':rewards,
   'persistent_blessing':True if 'persistent_blessing' in tags else (False if interaction=='opened' else None),
   'instant_healing':True if any(t.startswith('instant_healing:') for t in tags) else (False if interaction=='opened' else None),
   'hud_before':ev[before],'hud_after':ev[after],'hud_review':{'blessings_before':[{'art':a,'counter':n} for a,n in HUD[key][0]],'blessings_after':[{'art':a,'counter':n} for a,n in HUD[key][1]],'curses_before':[{'art':a,'counter':n} for a,n in HUD[key][2]],'curses_after':[{'art':a,'counter':n} for a,n in HUD[key][3]],'effect_names_and_strengths_verified':False,'basis':'Manual native frame sequence and enlarged HUD comparison. Slot count alone is not used to exclude refreshes.'},
   'evidence':[dict(ev[i],role='reviewed_interaction_sequence') for i in sorted(set([0,2,before,after]+proofs))],
   'frame_manifest':(Path('container-audit-2026-10-08')/folder/'index.json').as_posix(),'notes':[]})
  if key=='O338':rows[-1]['cohorts'].append('complete_greed_stage_2_passage')
  if key=='O326':rows[-1]['notes'].append('Corpse remained seated and unchanged. No unique search animation established; hover is insufficient. Excluded from reward denominators.')
  if key=='O316':rows[-1]['notes'].append('Silver chest remains visibly closed through departure; no opening transition. Excluded from reward denominators.')
  if interaction=='awakened_mimic':rows[-1]['notes'].append('Initially ordinary silver chest, then teeth/tongue appear on interaction. Later flee damage is not a loot reward. Excluded from ordinary loot denominators.')
  if key=='O266':rows[-1]['notes'].append('First interaction opens crate and produces blessing card; later interaction produces empty smoke. Preserve one container visit and the full sequence; click count is not asserted.')
  if key=='R50':rows[-1]['notes'].append('First crate opening produces blessing card; later gold appears during the same room visit. Prior-room gold already present at onset is not counted.')
  if key=='O627':rows[-1]['notes'].append('Blue potion/red-cross card, then +24,448,540 healing and direct gold. No persistent HUD slot is added. Health 5,188,418 to29,636,958; max97,794,157. The observed heal is25% of max, rounded.')
  if key=='O324':rows[-1]['notes'].append('Purple curse card matches an already active curse: counter4 refreshes to10. Not a blessing, and barrel has its own reward mechanism.')
  if key in ['O83','O338']:
   rows[-1]['persistent_blessing']=None
   rows[-1]['notes'].append('No blessing card was seen and the same HUD art persists. Existing armed-figure counter/strength is not confidently readable, so a refresh is conservatively unresolved; not a verified no-blessing result.')
  if key in ['O315','O316']:rows[-1]['notes'].append('Silver-key effect counter changes on entry through the locked door, before the container interaction; this is not a new loot blessing.')
  if style=='epic':rows[-1]['notes'].append('Bright ornate golden initial chest. Retain as epic chest, separate from ordinary crate/bronze/silver loot pool.')
 (OUT/'reviewed-interactions.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
 for start in range(0,len(rows),8):
  group=rows[start:start+8];canvas=np.zeros((len(group)*190,1440,3),np.uint8)
  for j,r in enumerate(group):
   cv2.putText(canvas,r['observation_id'].split(':')[-1]+' '+r['initial_style']+' '+r['interaction'],(2,j*190+18),0,.55,(255,255,255),1,cv2.LINE_AA)
   for k,field in enumerate(['hud_before','hud_after']):
    src=cv2.imread(str(BASE/r[field]['image']))
    tile=cv2.resize(src[395:475,210:470],(650,160),interpolation=cv2.INTER_NEAREST)
    canvas[j*190+25:j*190+185,k*720:k*720+650]=tile
  cv2.imwrite(str(OUT/f'hud-{start//8}.jpg'),canvas,[cv2.IMWRITE_JPEG_QUALITY,98])
 print(len(rows),'manual interaction records; HUD sheets prepared for review')

if __name__=='__main__':main()
