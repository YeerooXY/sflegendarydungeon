"""Check counter OCR against the saved, manually read development samples."""
from collections import Counter
import json
import cv2

from sfld_vod.media import sha256,write_json
from sfld_vod.room_sequence import NativeCounterReader,RefinedCounterReader
from sfld_vod.vision import rect
from scan_relocated_counters import BOXES
from vod_room_library import ROOT


def main():
    cv2.setNumThreads(1)
    folder=ROOT/'out/vod-room-library-audit'
    data=json.loads((folder/'visibility-reviewed.json').read_text())
    reader=NativeCounterReader();fallback=object.__new__(RefinedCounterReader);fallback.engine=reader.engine
    rows=[]
    for source in data['rows']:
        for proof in source['reviewed_samples']:
            image=folder/proof['game']
            if sha256(image)!=proof['game_sha256']:raise ValueError('Changed audit frame: '+str(image))
            frame=cv2.imread(str(image))
            box=BOXES[source['source_id']] if proof['counter_location']=='top' else [0,.935,.15,1-.935]
            x,y,w,h=rect(box,frame.shape[1],frame.shape[0]);area=frame[y:y+h,x:x+w]
            result=reader.read(area);method='native_color_threshold'
            if result['room'] is None:
                result=fallback.read(area);method='trimmed_color_fallback'
            expected=proof['observed_counter']
            outcome=('unlabelled' if expected is None else 'abstained' if result['room'] is None
                     else 'matched' if expected==result['room'] else 'wrong')
            rows.append({'view_id':source['view_id'],'sample_index':proof['sample_index'],
                         'expected':expected,'result':result,'method':method,'outcome':outcome,
                         'automatic_anchor_veto':source['automatic_anchor_veto'],
                         'evidence':proof})
    report={'schema':1,'scope':'Development samples used to improve the reader; not held-out accuracy. '
            'Unknown references cannot establish a correct number. Temporal corroboration and obstruction vetoes apply separately.',
            'summary':dict(Counter(r['outcome'] for r in rows)),
            'known_references':sum(r['expected'] is not None for r in rows),'rows':rows}
    write_json(folder/'counter-final-development-check.json',report)
    write_json(ROOT/'data/vods/room-counter-2026-10-12/development-check.json',report)
    print(json.dumps({k:report[k] for k in ('known_references','summary')},indent=2))


if __name__=='__main__':main()
