"""Prepare auditable native evidence for the October 12 new-source review.

Predictions and contact sheets are review aids, never verified observations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sfld_vod.media import sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'


def prepare(source_id):
    import cv2
    from sfld_vod.recognize import prepare as native_prepare
    from sfld_vod.hud import CounterReader, GemReader
    from sfld_vod.door_recognition import MaskedDoorRecognizer, stable_doors

    cv2.setNumThreads(1)
    folder = WORK / source_id
    scenes = sorted((folder / 'scenes').glob('*/scenes.json'))
    queue_path = folder / 'native-input/queue.json'
    if not queue_path.exists():
        native_prepare(WORK, source_id, scenes, queue_path.parent)
    queue = json.loads(queue_path.read_text())
    epochs = {}
    for path in scenes:
        for e in json.loads(path.read_text())['epochs']:
            epochs[e['id']] = {**e, 'scene_file': str(path.relative_to(WORK))}
    out = folder / 'review-preparation'
    out.mkdir(exist_ok=True)
    target = out / 'suggestions.json'
    result = {'schema': 1, 'source_id': source_id, 'queue_sha256': sha256(queue_path),
              'rows': [], 'unresolved_candidates': queue['unresolved_candidates'],
              'complete': False, 'verified_observations': 0}
    if target.exists():
        result = json.loads(target.read_text())
        if result['queue_sha256'] != sha256(queue_path):
            raise ValueError('Native input changed')
    done = {r['id'] for r in result['rows']}
    counter = CounterReader()
    gems = GemReader(ROOT / 'out/vod-art')
    doors = MaskedDoorRecognizer(WORK / 'layouts' / f'{source_id}.json', ROOT / 'out/vod-art')
    began = time.monotonic()
    for index, item in enumerate(queue['items']):
        ident = item['observation_id']
        if ident in done:
            continue
        frames = [cv2.imread(str(WORK / e['image'])) for e in item['evidence']]
        if any(f is None for f in frames):
            raise ValueError('Missing native frame')
        counter_suggestions = [counter.read(f) for f in frames]
        readings = [r['displayed_room'] for r in counter_suggestions]
        result['rows'].append({
            'id': ident, 'index': index, 'source_id': source_id,
            'start': item['start'], 'end': item['end'], 'evidence': item['evidence'],
            'scene': epochs[ident], 'counter_suggestions': counter_suggestions,
            'room_agreement': readings[0] is not None and readings[0] == readings[1],
            'possible_merged_rooms': all(v is not None for v in readings) and readings[0] != readings[1],
            'doors': stable_doors([doors.doors(f) for f in frames]),
            'gems': gems.read(frames[0]), 'verified_fields': [],
            'seek_drift': [abs(e['source_seconds'] - e['decoded_source_seconds']) for e in item['evidence']],
        })
        if len(result['rows']) % 20 == 0:
            write_json(target, result)
            print(f'{source_id}: {len(result["rows"])}/{len(queue["items"])} prepared', flush=True)
    result['complete'] = True
    result['processing_seconds_this_pass'] = time.monotonic() - began
    write_json(target, result)
    print(f'{source_id}: preparation complete', flush=True)


def sheets(source_id, page_size=24):
    import cv2
    import numpy as np
    folder = WORK / source_id / 'review-preparation'
    rows = json.loads((folder / 'suggestions.json').read_text())['rows']
    output = folder / 'sheets'
    output.mkdir(exist_ok=True)
    for start in range(0, len(rows), page_size):
        subset = rows[start:start+page_size]
        width, height, cols = 400, 396, 4
        canvas = np.zeros((((len(subset)+cols-1)//cols)*height, width*cols, 3), np.uint8)
        for number, row in enumerate(subset):
            native = cv2.imread(str(WORK / row['evidence'][0]['image']))
            game = cv2.resize(native, (640,448))
            image = cv2.resize(game, (width,280))
            x, y = number % cols * width, number // cols * height
            canvas[y:y+280,x:x+width] = image
            # Counter and stones remain at native scale in an enlarged HUD strip.
            hud = game[324:448,:112]
            hud = cv2.resize(hud,(100,110))
            canvas[y+280:y+390,x:x+100] = hud
            room = [r['displayed_room'] for r in row['counter_suggestions']]
            ds = '/'.join(str(d['kind']) for d in row['doors'])
            label = f'{row["index"]} | {row["start"]:.2f} | R{room} {ds}'
            cv2.putText(canvas,f'{row["index"]} | {row["start"]:.2f} | R{room}',(x+104,y+302),cv2.FONT_HERSHEY_SIMPLEX,.41,(235,235,235),1,cv2.LINE_AA)
            cv2.putText(canvas,ds,(x+104,y+326),cv2.FONT_HERSHEY_SIMPLEX,.42,(235,235,235),1,cv2.LINE_AA)
            gemtext = ','.join(row['gems']['gems_suggested']) or 'gems unknown/empty'
            cv2.putText(canvas,gemtext,(x+104,y+350),cv2.FONT_HERSHEY_SIMPLEX,.40,(180,215,255),1,cv2.LINE_AA)
        dest = output / f'{start:04d}.jpg'
        cv2.imwrite(str(dest),canvas,[cv2.IMWRITE_JPEG_QUALITY,96])
    print(f'{source_id}: {len(rows)} candidates in contact sheets',flush=True)


def record_page(source_id, start, labels, gems, overrides=None):
    """Save explicit visual judgements; never promote predictions automatically.

    Each label is left/right plus optional L/R/B trap suffix, or x for a
    repeated/invalid candidate. Room and special phase are supplied in overrides.
    Every call is a manual review action, not an automatic classification step.
    """
    aliases = {'m':'mystery','e':'monster','k':'locked','c':'cursed','s':'sacrifice',
               'h':'hungry','w':'wall','b':'blessing','d':'destiny','g':'golden',
               'u':'unlocked','l':'double_locked','p':'epic','o':'shop','z':'boss',
               't':'trial','q':'exit_trial','?':None}
    folder = WORK / source_id / 'review-preparation'
    suggestions = json.loads((folder/'suggestions.json').read_text())['rows']
    by_index = {r['index']:r for r in suggestions}
    out = folder/'manual-offers.json'
    saved = json.loads(out.read_text()) if out.exists() else []
    existing = {r['index']:r for r in saved}
    labels = labels.split()
    for delta, text in enumerate(labels):
        index = start+delta
        row = by_index[index]
        changes = (overrides or {}).get(index,{})
        extra = text[2:]
        room = changes.get('room',row['counter_suggestions'][0]['displayed_room'] if row['room_agreement'] else None)
        stones = gems.get(index, gems.get('default')) if isinstance(gems,dict) else gems
        item = {'schema':1,'id':row['id'],'index':index,'source_id':source_id,
                'start':row['start'],'end':row['end'],'room':room,
                'stage':(room-1)//25+1 if room is not None and room>0 else None,
                'status':'excluded' if text=='x' else 'decision',
                'reason':changes.get('reason','repeated candidate' if text=='x' else None),
                'doors':[] if text=='x' else [
                    {'kind':aliases[text[0]],'trap':None if '?' in extra else ('L' in extra or 'B' in extra)},
                    {'kind':aliases[text[1]],'trap':None if '?' in extra else ('R' in extra or 'B' in extra)}],
                'gems':stones,'stone_set_complete':stones is not None,
                'phase':changes.get('phase','opening' if room is not None and room<=5 else 'ordinary'),
                'run_class':changes.get('run_class'),'run_id':changes.get('run_id'),
                'chosen_side':None,'initial_contents':None,'reward':None,
                'evidence':row['evidence'],'verification':'manual_native_offer_still',
                'outcome_verified':False,'notes':changes.get('notes',[])}
        item.update({k:v for k,v in changes.items() if k not in ['reason','room','phase','notes']})
        if index in existing and existing[index]!=item:
            raise ValueError(f'Index {index} already reviewed differently; apply an explicit correction.')
        existing[index]=item
    write_json(out,sorted(existing.values(),key=lambda r:r['index']))
    print(f'{source_id}: {len(existing)} manually reviewed candidate views',flush=True)


def burst(source_id, start, end, name, fps=4):
    """Decode a requested native sequence, retaining missing timestamps explicitly."""
    import cv2
    import numpy as np
    from sfld_vod.vision import rect
    from sfld_vod.batch import select_clips
    cv2.setNumThreads(1)
    if not 0 <= start < end or end-start > 180 or Path(name).name != name:
        raise ValueError('Expected a safe name and a bounded positive interval')
    output = WORK/source_id/'review-preparation/bursts'/name
    output.mkdir(parents=True,exist_ok=True)
    marker = output/'index.json'
    if marker.exists():
        existing = json.loads(marker.read_text())
        if existing['start']!=start or existing['end']!=end or existing['fps']!=fps:
            raise ValueError('Existing burst has different boundaries')
        return existing
    clips = select_clips(WORK/source_id/'720p')
    crop = json.loads((WORK/'layouts'/f'{source_id}.json').read_text())['crop']
    frames = []; missing = []; cap = None; current = None
    try:
        for i in range(round((end-start)*fps)+1):
            stamp = start+i/fps
            found = next(((p,c) for p,c in clips if c['source_start']<=stamp<c['source_end']),None)
            if found is None:
                missing.append(stamp); continue
            path,clip=found
            if path!=current:
                if cap is not None: cap.release()
                cap=cv2.VideoCapture(str(path.parent/clip['file'])); current=path
            cap.set(cv2.CAP_PROP_POS_MSEC,(stamp-clip['source_start'])*1000)
            ok,raw=cap.read()
            if not ok:
                missing.append(stamp);continue
            decoded=clip['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            if abs(decoded-stamp)>.10:
                missing.append(stamp);continue
            x,y,w,h=rect(crop,raw.shape[1],raw.shape[0])
            native=raw[y:y+h,x:x+w]
            target=output/f'{i:04d}.jpg'
            cv2.imwrite(str(target),native,[cv2.IMWRITE_JPEG_QUALITY,97])
            frames.append({'index':i,'source_seconds':stamp,'decoded_source_seconds':decoded,
                           'image':target.relative_to(WORK).as_posix(),'sha256':sha256(target),
                           'video_sha256':clip['sha256'],'native_height':raw.shape[0]})
    finally:
        if cap is not None: cap.release()
    result={'source_id':source_id,'start':start,'end':end,'fps':fps,'frames':frames,
            'missing_timestamps':missing,'verification':'unreviewed sequence evidence'}
    write_json(marker,result)
    for offset in range(0,len(frames),24):
        group=frames[offset:offset+24]
        sheet=np.zeros((((len(group)+3)//4)*248,1280,3),np.uint8)
        for j,item in enumerate(group):
            im=cv2.imread(str(WORK/item['image']))
            im=cv2.resize(im,(320,224))
            x,y=j%4*320,j//4*248
            sheet[y:y+224,x:x+320]=im
            cv2.putText(sheet,f'{item["index"]} | {item["source_seconds"]:.3f}',(x+4,y+241),cv2.FONT_HERSHEY_SIMPLEX,.43,(240,240,240),1,cv2.LINE_AA)
        cv2.imwrite(str(output/f'sheet-{offset:04d}.jpg'),sheet,[cv2.IMWRITE_JPEG_QUALITY,97])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare','sheets'])
    parser.add_argument('source_id')
    args = parser.parse_args()
    if args.source_id not in {s['id'] for s in json.loads((ROOT/'examples/vod-hunt-2026-10-12.json').read_text())['sources']}:
        parser.error('Expected a downloaded source ID')
    (prepare if args.command == 'prepare' else sheets)(args.source_id)
