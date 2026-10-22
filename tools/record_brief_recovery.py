"""Record explicit human-reviewed labels for the 70 short candidate sequences."""
import json
from pathlib import Path
from sfld_vod.media import write_json

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'out/vod-hunt-2026-10-12'
ALIASES = dict(m='mystery',e='monster',k='locked',c='cursed',s='sacrifice',h='hungry',
               w='wall',b='blessing',d='destiny',g='golden',u='unlocked',o='shop',z='boss',
               t='trial',q='exit_trial')
LABELS = ("x x mm mkL x mk me mk sm wmR hm mc mh mc km ekL es km "
          "mk ek cmR km mm x x x x mc ms em zw zw gk mm ow he "
          "km sm zw x mh um gk x x x x x be hm tq qt x he mw x "
          "x x x x x x x x x x x x um x").split()
ROOMS = {2:24,3:21,5:10,6:3,8:26,9:29,10:30,11:32,12:33,13:34,14:35,
         15:36,16:37,17:39,18:40,19:43,20:44,21:45,22:46,27:47,28:48,29:49,
         30:50,31:50,32:64,33:66,34:67,35:69,36:70,37:71,38:75,40:95,41:96,42:13,68:98}
rows = json.loads((WORK/'brief-recovery/index.json').read_text())
assert len(rows)==len(LABELS)==70
out=[]
for row,label in zip(rows,LABELS):
    i=row['review_index']; room=ROOMS.get(i); gems=None
    if i in [2,3,5,6,7,42,48,54]: gems=[]
    if 8<=i<=31: gems=['moonstone']
    if 32<=i<=38: gems=['moonstone','spying']
    if i in [40,41]: gems=['greed','moonstone','spying']
    if i==49: gems=['pearl']
    if i in [50,51]: gems=['pearl','spying']
    if i==53: gems=['moonstone']
    if i==68: gems=['spying','bull','rabbit']
    phase='opening' if room and room<=5 else 'ordinary'
    if i in [30,31,38]: phase='boss'
    if i==34: phase='shop'
    if i in [50,51]: phase='trial_round'
    if i in [7,48,54]: phase='unknown_stage1'
    stage=(room-1)//25+1 if room else {7:1,48:1,49:2,50:3,51:3,53:4,54:1}.get(i)
    reason='Not a new decision: room interior, transition, menu or repeated view.' if label=='x' else None
    out.append({'schema':1,'id':row['observation_id'],'source_id':row['source_id'],
                'index':f'brief-{i}','start':row['start'],'end':row['end'],
                'room':room,'stage':stage,'status':'excluded' if label=='x' else 'decision',
                'reason':reason,'phase':phase,'gems':gems,'stone_set_complete':gems is not None,
                'doors':[] if label=='x' else [{'kind':ALIASES[label[j]],'trap':side in label[2:]} for j,side in enumerate(['L','R'])],
                'evidence':row['burst']['frames'],'verification':'manual_native_temporal_offer',
                'outcome_verified':False,'run_class':None,'run_id':None,
                'notes':'Recovered from an originally rejected short candidate; surrounding native frames reviewed.'})
write_json(WORK/'brief-recovery/manual-offers.json',out)
print('Reviewed',len(out),'short candidates;',sum(r['status']=='decision' for r in out),'additional decisions (includes forced rooms).')
