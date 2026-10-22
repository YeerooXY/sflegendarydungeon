"""Reproducible time-stratified spot checks of retained AND rejected footage."""
from __future__ import annotations

import html
import json
from pathlib import Path
import random

from .batch import clock, select_clips, subtract_intervals
from .media import sha256, write_json


def sample_times(intervals, count, rng):
    """Stratified uniform-in-duration times, not uniform-in-interval times."""
    total=sum(b-a for a,b in intervals)
    if total<=0:
        return []
    result=[]
    for i in range(count):
        offset=(i+rng.random())*total/count
        for a,b in intervals:
            if offset<b-a:
                result.append(a+offset);break
            offset-=b-a
    return result


def build_audit(root, count=8, seed=42):
    import cv2
    import numpy as np
    if not 1<=count<=100:
        raise ValueError('Choose 1-100 samples per source and screening stratum.')
    root=Path(root).resolve(); folder=root/'audit';folder.mkdir(exist_ok=True)
    report=json.loads((root/'screening.json').read_text(encoding='utf-8'))
    rng=random.Random(seed); records=[]
    page=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Screening audit</title>',
          '<style>body{background:#101c22;color:#e8e7df;font:16px system-ui;margin:30px}img{max-width:100%}a{color:#edc676}article{padding:12px}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr))}</style>',
          '<h1>Screening spot checks</h1><p>Samples from retained and rejected time, chosen before inspection. Check that rejected frames really are outside the dungeon. These still images cannot establish recall of brief decisions between samples. <a href="screening.html">Back to timeline</a></p>']
    for source in report['sources']:
        ident=source['source_id']; tiles=[]
        clips=select_clips(root/ident/'360p')
        strata={'retained':source['intervals'],
                'rejected':subtract_intervals(source['screened_intervals'],source['intervals'])}
        page.append('<h2>'+html.escape(ident)+'</h2><section>')
        for stratum,intervals in strata.items():
            for index,t in enumerate(sample_times(intervals,count,rng)):
                marker,clip=next((m,c) for m,c in clips if c['source_start']<=t<c['source_end'])
                video=marker.parent/clip['file'];capture=cv2.VideoCapture(str(video))
                try:
                    capture.set(cv2.CAP_PROP_POS_MSEC,(t-clip['source_start'])*1000)
                    ok,frame=capture.read()
                finally:
                    capture.release()
                if not ok:
                    raise ValueError(f'Cannot decode audit frame {ident} at {t}.')
                frame=cv2.resize(frame,(640,360),interpolation=cv2.INTER_AREA)
                filename=f'{ident}-{stratum}-{index:03}.jpg'
                cv2.imwrite(str(folder/filename),frame,[cv2.IMWRITE_JPEG_QUALITY,90])
                records.append({'source_id':ident,'source_url':source['url'],'source_seconds':t,
                                'stratum':stratum,'image':'audit/'+filename,'image_sha256':sha256(folder/filename),
                                'clip_sha256':clip['sha256'],'actual_dungeon':None})
                tile=np.zeros((205,320,3),dtype=np.uint8);tile[:180]=cv2.resize(frame,(320,180))
                cv2.putText(tile,f'{stratum} {index}: {clock(t)}',(4,197),cv2.FONT_HERSHEY_SIMPLEX,.42,(230,230,230),1)
                tiles.append(tile)
                page.append(f'<article><p>{stratum} {index}: {clock(t)}</p><img loading="lazy" src="audit/{filename}" alt="Full stream frame"></article>')
        page.append('</section>')
        if tiles:
            while len(tiles)%4:
                tiles.append(np.zeros_like(tiles[0]))
            sheet=np.concatenate([np.concatenate(tiles[i:i+4],axis=1) for i in range(0,len(tiles),4)],axis=0)
            cv2.imwrite(str(folder/(ident+'-contact.jpg')),sheet)
    page.append('</html>');(root/'audit.html').write_text('\n'.join(page),encoding='utf-8')
    result={'schema':1,'seed':seed,'samples_per_stratum':count,'screening_sha256':sha256(root/'screening.json'),
            'samples':records,'interpretation':'Time-weighted stratified spot checks, not validated event-detection recall.'}
    write_json(root/'audit.json',result)
    return result
