"""Recover ambiguous counter OCR from saved native bands without video decoding."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
from pathlib import Path
import re

from sfld_vod.media import sha256,write_json
from sfld_vod.room_sequence import NativeCounterReader,RefinedCounterReader,SEQUENCE_DIR
from vod_room_library import ROOT,LIBRARY_ROOTS

_native=_refined=None


def refine(path):
    import cv2
    global _native,_refined
    cv2.setNumThreads(1);path=Path(path);target=path.with_name('refined.json')
    digest=sha256(path)
    if target.exists():
        old=json.loads(target.read_text())
        if old['refinement_source_sha256']!=digest:raise ValueError('Refinement inputs changed: '+str(path))
        return old['refinement_summary']
    data=json.loads(path.read_text());root=next(p for p in path.parents if (p/'layouts').is_dir())
    visibility=json.loads((ROOT/'data/vods/room-counter-2026-10-12/visibility-reviewed.json').read_text())
    view=data.get('view_id',data['source_id'])
    veto=next((r['automatic_anchor_veto'] for r in visibility['rows'] if r['view_id']==view),False)
    if _native is None:
        _native=NativeCounterReader();_refined=object.__new__(RefinedCounterReader);_refined.engine=_native.engine
    replacements={};changed=attempted=rescued=0
    for proof in data['reads']:
        original=proof['counter'];effective=original
        ch=round(proof['crop_in_source'][3]*proof['native_height'])
        distorted=ch<32 and proof.get('counter_crop_scaling')!='aspect_preserved'
        numeric=any(r.get('room') is not None or re.search(r'\d[/／]\d',r.get('text','')) for r in original['readings'])
        if not veto and numeric and (distorted or original['room'] is None):
            image=cv2.imread(str(root/proof['image']))
            if image is None or sha256(root/proof['image'])!=proof['sha256']:raise ValueError('Missing or changed counter proof.')
            if distorted:
                # The first prototype enlarged a short band to 32 px high
                # without enlarging its width. Restore the native aspect ratio.
                image=cv2.resize(image,(round(image.shape[1]*image.shape[0]/ch),image.shape[0]),interpolation=cv2.INTER_CUBIC)
                effective=_native.read(image)
            if effective['room'] is None:effective=_refined.read(image)
            attempted+=1
        updated={**proof,'counter':effective,'counter_raw':original,
                 'refinement':'native_aspect_and_color_crops' if distorted else 'color_crop_fallback' if effective is not original else 'unchanged'}
        replacements[proof['image']]=updated
        changed+=effective['room']!=original['room']
        rescued+=original['room'] is None and effective['room'] is not None
    current=None
    for sample in data['samples']:
        if sample['fresh']:
            current=replacements[sample['proof']['image']];sample['proof']=current
        if current is None:raise ValueError('Cached counter sample precedes the first reading.')
        sample['room']=current['counter']['room']
    data['reads']=list(replacements.values())
    data['refinement_source_sha256']=digest
    data['refinement_summary']={'source_id':data['source_id'],'source_start':data['source_start'],
                                'attempted':attempted,'changed':changed,'rescued_unknown_reads':rescued,'anchor_veto':veto}
    write_json(target,data)
    return data['refinement_summary']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.workers<=16:raise ValueError('Use 1–16 workers.')
    files=[p for root in LIBRARY_ROOTS for p in (ROOT/root).glob('*/'+SEQUENCE_DIR+'/**/scan.json')]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending=[pool.submit(refine,str(p)) for p in files]
        for future in as_completed(pending):print(json.dumps(future.result()),flush=True)


if __name__=='__main__':main()
