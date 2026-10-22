"""Freeze and extract every reviewed Spying/no-Lodestone transition for Lootenant.

This script selects by previously reviewed stones only, never key outcomes.
Manual visual dispositions live in a separate ledger.
"""
from pathlib import Path
import json
import sys
import cv2
import numpy as np
from sfld_vod.batch import select_clips
from sfld_vod.vision import rect
from sfld_vod.media import sha256

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'out/vod-hunt-2026-10-12'
OUT=ROOT/'out/spying-key-audit-2026-10-18/lootenant'
DATA=ROOT/'data/vods/spying-key-audit-2026-10-18'
SOURCE='2878439002'

def run_id(t):
    if t<2700:return SOURCE+':partial-spying-2183'
    if t<3900:return SOURCE+':opening-2942'
    if t<5000:return SOURCE+':opening-4198'
    return SOURCE+':partial-spying-10513'

def main():
    ledger=ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl'
    records=sorted([r for r in map(json.loads,ledger.read_text().splitlines()) if r['source_id']==SOURCE and r.get('status')=='decision'],key=lambda r:r['start'])
    selected=[]
    for i,r in enumerate(records):
        if not (r.get('stone_set_complete') and 'spying' in r.get('gems',[]) and 'lodestone' not in r.get('gems',[])):continue
        nxt=records[i+1] if i+1<len(records) else None
        selected.append({**r,'audit_run_id':run_id(r['start']),'next_start':nxt['start'] if nxt else None,'next_room':nxt['room'] if nxt else None})
    assert len(selected)==153
    DATA.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    manifest={'schema':1,'source_id':SOURCE,'source_url':'https://www.twitch.tv/videos/'+SOURCE,'selection_basis':'Every decision offer in the reviewed ledger with a complete stone set including Spying and excluding Lodestone; all 153 fixed before inspecting rewards. Boss/trial/retry offers remain for explicit exclusions.','candidate_count':len(selected),'candidate_offer_ids':[r['id'] for r in selected],'reviewed_offer_ledger_sha256':sha256(ledger),'original_60_ledger_sha256':sha256(ROOT/'data/vods/opening-audit-2026-10-17/key-drops.json'),'previous_lootenant_audit_sha256':sha256(ROOT/'data/vods/key-drop-extension-2026-10-17/lootenant.json'),'segments':[]}
    for rid in dict.fromkeys(r['audit_run_id'] for r in selected):
        seg=[r for r in selected if r['audit_run_id']==rid]
        manifest['segments'].append({'run_id':rid,'start':seg[0]['start'],'end':seg[-1]['end'],'candidate_count':len(seg)})
    p=DATA/'lootenant-scope.json'
    if p.exists():assert json.loads(p.read_text())==manifest,'Frozen scope changed'
    else:p.write_text(json.dumps(manifest,indent=2)+'\n')
    clips=select_clips(WORK/SOURCE/'720p')
    crop=json.loads((WORK/'layouts'/f'{SOURCE}.json').read_text())['crop']
    cv2.setNumThreads(1);cap=None;current=None;output=[]
    for ordinal,r in enumerate(selected):
        ident=f"{ordinal:03d}-{r['start']:.3f}-r{r['room']:02d}"
        if r['next_start'] is None:
            times=[r['end']-.1]+np.linspace(r['end']+.1,r['end']+15,8).tolist()
        else:
            times=[r['end']-.1]+np.linspace(r['end']+.1,min(r['next_start']-.1,r['end']+20),7).tolist()+[r['next_start']+.12]
        evidence=[];thumbs=[]
        for n,t in enumerate(times):
            found=next(((p,c) for p,c in clips if c['source_start']<=t<c['source_end']),None)
            if found is None:continue
            p,c=found
            if p!=current:
                if cap:cap.release()
                cap=cv2.VideoCapture(str(p.parent/c['file']));current=p
            cap.set(cv2.CAP_PROP_POS_MSEC,(t-c['source_start'])*1000);ok,raw=cap.read()
            if not ok:continue
            actual=c['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            x,y,w,h=rect(crop,raw.shape[1],raw.shape[0]);native=raw[y:y+h,x:x+w]
            dest=OUT/f'{ident}-{n}.png';cv2.imwrite(str(dest),native)
            evidence.append({'image':dest.relative_to(ROOT).as_posix(),'source_seconds':t,'decoded_source_seconds':actual,'sha256':sha256(dest),'video':(p.parent/c['file']).relative_to(ROOT).as_posix(),'video_sha256':c['sha256']})
            tile=np.zeros((340,350,3),np.uint8);tile[:250]=cv2.resize(native,(350,250));tile[250:320]=cv2.resize(native[int(h*.83):],(350,70));cv2.putText(tile,f'{n}: {t:.3f}',(4,337),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1,cv2.LINE_AA);thumbs.append(tile)
        sheet=OUT/f'{ident}.jpg'
        if thumbs:cv2.imwrite(str(sheet),np.concatenate(thumbs,axis=1),[cv2.IMWRITE_JPEG_QUALITY,97])
        output.append({'ordinal':ordinal,'offer_id':r['id'],'source_id':SOURCE,'run_id':r['audit_run_id'],'room':r['room'],'stage':r['stage'],'start':r['start'],'end':r['end'],'next_start':r['next_start'],'next_room':r['next_room'],'offered_doors':r['doors'],'previously_reviewed_gems':r['gems'],'previously_reviewed_stone_set_complete':r['stone_set_complete'],'sheet':sheet.relative_to(ROOT).as_posix(),'evidence':evidence,'verification':'unreviewed'})
        print(ordinal,ident,flush=True)
    if cap:cap.release()
    (OUT/'index.json').write_text(json.dumps(output,indent=2)+'\n');print('Finished',len(output),flush=True)

if __name__=='__main__':main()
