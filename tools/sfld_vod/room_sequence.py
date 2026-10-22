"""Counter evidence and conservative sequence reconstruction for public footage.

A displayed room is an anchor, not a unique encounter ID: retries, arena
phases, account changes and video edits can repeat it. No missing number is
interpolated into an observation and no candidate is deleted here.
"""
from __future__ import annotations

from collections import Counter
from bisect import bisect_left,bisect_right
import hashlib


VERSION = '1.0.0'
SEQUENCE_DIR = 'room-sequence-v'+VERSION


class NativeCounterReader:
    """Read only the counter band, without shrinking the game image first."""
    def __init__(self):
        from .hud import CounterReader
        self.engine = CounterReader().engine

    def read(self, crop):
        import cv2
        import numpy as np
        from .hud import parse_counter
        # Both variants retain every digit and the denominator. A facecam over
        # the numerator must result in unknown, not a guessed leading digit.
        enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
        _,binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        variants = [('color', enlarged), ('threshold', cv2.cvtColor(binary,cv2.COLOR_GRAY2BGR))]
        readings=[]
        for name, image in variants:
            result=self.engine(image,use_det=False,use_cls=False,use_rec=True)
            for text,score in zip(result.txts or [], result.scores or []):
                readings.append({'variant':name,'text':text,'score':round(float(score),4),
                                 'room':parse_counter(text)})
        accepted=[r for r in readings if r['room'] is not None and r['score']>=.85]
        values={r['room'] for r in accepted}
        value=next(iter(values)) if len(accepted)>=2 and len(values)==1 else None
        return {'room':value,'readings':readings,'status':'suggestion' if value is not None else 'unknown'}


class RefinedCounterReader(NativeCounterReader):
    """Recover small colored text that a binary threshold damaged.

    Two narrower color crops retain the whole counter but exclude neighboring
    HUD trim that the recognizer can mistake for an extra digit/letter.
    """
    def read(self,crop):
        import cv2
        from .hud import parse_counter
        h,w=crop.shape[:2]
        variants=[('trim_color_a',crop[round(h*.08):round(h*.9),:round(w*.86)]),
                  ('trim_color_b',crop[round(h*.14):round(h*.86),:round(w*.82)])]
        readings=[]
        for name,area in variants:
            image=cv2.resize(area,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
            result=self.engine(image,use_det=False,use_cls=False,use_rec=True)
            for text,score in zip(result.txts or [],result.scores or []):
                readings.append({'variant':name,'text':text,'score':round(float(score),4),'room':parse_counter(text)})
        accepted=[r for r in readings if r['room'] is not None and r['score']>=.85]
        values={r['room'] for r in accepted}
        value=next(iter(values)) if len(accepted)>=2 and len(values)==1 else None
        return {'room':value,'readings':readings,'status':'suggestion' if value is not None else 'unknown'}


def fingerprint(crop):
    """Tiny visual key for avoiding OCR on an unchanged counter band."""
    import cv2
    import numpy as np
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    small=cv2.resize(gray,(160,48),interpolation=cv2.INTER_AREA)
    # The animated frame trim is outside the digit band. Compare glyph shape
    # using a relative threshold, so a pulsing border or uniform fade does not
    # repeatedly invoke OCR on an unchanged number. Periodic fresh OCR remains
    # mandatory; a cache hit is never an independent corroborating reading.
    text=small[9:39,:130]
    return text, text>max(90,float(text.max())*.68)


def same_counter(a,b):
    import numpy as np
    return np.count_nonzero(a[1]!=b[1])<=6


def build_spans(samples, source_id, fps=8):
    """Retain all known/unknown spans; bridge only brief same-number blanks."""
    step=1/fps
    spans=[]
    for sample in sorted(samples,key=lambda s:s['time']):
        stamp=sample['time'];room=sample['room']
        if (spans and spans[-1]['room']==room
                and stamp-spans[-1]['end']<=step*1.6):
            span=spans[-1];span['end']=stamp;span['sample_count']+=1
        else:
            span={'room':room,'start':stamp,'end':stamp,'sample_count':1,'proofs':{},'blank_intervals':[]}
            spans.append(span)
        if sample.get('fresh'):
            proof=sample['proof']
            span['proofs'][proof['image']]=proof
    # A fade or OCR abstention need not split an otherwise constant counter.
    # Preserve the blank explicitly so it never becomes fabricated coverage.
    merged=[];i=0
    while i<len(spans):
        current=spans[i];i+=1
        while (i+1<len(spans) and current['room'] is not None and spans[i]['room'] is None
               and spans[i]['end']-spans[i]['start']+step<=.5
               and spans[i+1]['room']==current['room']
               and spans[i]['start']-current['end']<=step*1.6
               and spans[i+1]['start']-spans[i]['end']<=step*1.6):
            blank,following=spans[i:i+2]
            current['blank_intervals'].append([blank['start'],blank['end']])
            current['end']=following['end'];current['sample_count']+=following['sample_count']
            current['proofs'].update(following['proofs']);i+=2
        merged.append(current)
    for span in merged:
        span['id']=source_id+':counter:'+hashlib.sha256(f"{span['start']}:{span['room']}".encode()).hexdigest()[:16]
        span['source_id']=source_id
        span['proofs']=list(span['proofs'].values())
        stamps=sorted(p['source_seconds'] for p in span['proofs'])
        span['stable']=(span['room'] is not None and len(stamps)>=2
                        and stamps[-1]-stamps[0]>=.20 and span['sample_count']>=3)
        span['status']='temporal_agreement' if span['stable'] else 'unknown' if span['room'] is None else 'single_or_brief_read'
        span['fresh_read_count']=len(span['proofs'])
        # Detailed per-sample OCR remains in the immutable section scans.
        # The shared navigation catalog needs representative evidence only.
        if len(span['proofs'])>6:
            span['proofs']=span['proofs'][:3]+span['proofs'][-3:]
    return merged


def sequence_blocks(spans, source_id):
    """Split on decreases and visibility gaps; these are not verified run IDs."""
    blocks=[];previous=None;block=None
    for span in spans:
        if not span['stable']:continue
        reason=None
        if previous is None:reason='first_readable_counter'
        elif span['room']<previous['room']:reason='counter_decrease_reset_account_change_or_ocr_error'
        elif span['start']-previous['end']>120:reason='long_counter_visibility_gap'
        if reason:
            block={'id':f'{source_id}:sequence:{len(blocks)+1:03d}','source_id':source_id,
                   'start':span['start'],'end':span['end'],'boundary_reason':reason,
                   'run_identity':'unverified','span_ids':[],'observed_rooms':[],'gaps':[]}
            blocks.append(block)
        span['sequence_id']=block['id'];block['span_ids'].append(span['id'])
        block['end']=span['end'];block['observed_rooms'].append(span['room'])
        if previous and not reason and span['room']>previous['room']+1:
            block['gaps'].append({'after':previous['room'],'before':span['room'],
                                  'rooms':list(range(previous['room']+1,span['room'])),
                                  'start':previous['end'],'end':span['start'],
                                  'status':'not_read_between_anchors'})
        previous=span
    for block in blocks:
        counts=Counter(block['observed_rooms'])
        block['repeated_room_spans']={str(k):v for k,v in counts.items() if v>1}
        block['observed_rooms']=sorted(counts)
        block['first_room']=min(counts);block['last_room']=max(counts)
        block['missing_between_anchors']=sorted(set(n for g in block['gaps'] for n in g['rooms']))
        block['coverage_claim']='Counter visibility only; does not prove the offered pair was visible or a complete run was recorded.'
    return blocks


def associate_candidates(rows, spans):
    """Link to observed spans; preserve candidates crossing a counter change."""
    known=sorted((s for s in spans if s['stable']),key=lambda s:s['start'])
    starts=[s['start'] for s in known];ends=[s['end'] for s in known]
    def visible_at(span,t):
        # Half-open sample intervals: a sample at the next span's start must
        # not also belong to the previous number. Bridged blanks stay unread.
        return (span['start']-.04<=t<span['end']+.125
                and not any(a-.04<=t<b+.125 for a,b in span.get('blank_intervals',[])))
    output={}
    for row in rows:
        samples=row.get('positive_frames') or [row['best_time']]
        window=known[bisect_left(ends,min(samples)-.125):bisect_right(starts,max(samples)+.04)]
        hits=[s for s in window if any(visible_at(s,t) for t in samples)]
        numbers={s['room'] for s in hits}
        best=[s for s in hits if visible_at(s,row['best_time'])]
        selected=best[0] if len(best)==1 and len(numbers)==1 and len(hits)==1 else None
        output[row['id']]={'room':selected['room'] if selected else None,
                          'sequence_id':selected.get('sequence_id') if selected else None,
                          'span_id':selected['id'] if selected else None,
                          'status':'temporal_agreement' if selected else 'crosses_counter_boundary' if len(hits)>1 else 'unresolved',
                          'possible_rooms':sorted(numbers),'span_ids':[s['id'] for s in hits],
                          'proofs':selected['proofs'][:3] if selected else [],
                          'group_id':selected['id'] if selected else row['id'],
                          'deduplication':'Proposed review group only; all original views retained. Phases and retries still need review.'}
    return output


def attach_shared_sequence(rows,root,source_id,catalog=None):
    """Attach advisory identities to any scene/door/reward review timeline.

    Existing reviewed fields are never changed. Explicit POV IDs are required
    for a split-screen source; an ambiguous source-wide row stays unresolved.
    """
    import json
    from pathlib import Path
    if catalog is None:
        path=Path(root)/SEQUENCE_DIR/'catalog.json'
        if not path.exists():return None
        catalog=json.loads(path.read_text())
    views={r.get('counter_view_id',source_id) for r in rows}
    for view in views:
        originals=[r for r in rows if r.get('counter_view_id',source_id)==view]
        records=[{'id':r['id'],'best_time':r.get('best_time',r['start']),
                  'positive_frames':[r['start'],r.get('best_time',r['start']),r.get('end',r['start'])]} for r in originals]
        spans=[s for s in catalog['spans'] if s['source_id']==view]
        links=associate_candidates(records,spans)
        for row in originals:row['counter_sequence']=links[row['id']]
    return {'version':catalog['version'],
            'sources':[s for s in catalog['sources'] if s['source_id']==source_id],
            'blocks':[b for b in catalog['blocks'] if b.get('video_source_id',b['source_id'])==source_id]}
