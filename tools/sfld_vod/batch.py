"""Resume coarse screening and build a source-linked pruning timeline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from bisect import bisect_right
import html
import json
from pathlib import Path
import time

from .media import seconds, sha256, write_json
from .vision import gate_video, merge_intervals
from . import VERSION


def clock(seconds):
    seconds=int(seconds)
    return f"{seconds//3600:02}:{seconds%3600//60:02}:{seconds%60:02}"


def subtract_intervals(windows, covered):
    """Half-open interval subtraction, also used for audit sampling."""
    result=[]
    for start,end in merge_intervals(windows):
        cursor=start
        for a,b in merge_intervals(covered):
            if b<=cursor or a>=end:
                continue
            if a>cursor:
                result.append([cursor,min(a,end)])
            cursor=max(cursor,b)
        if cursor<end:
            result.append([cursor,end])
    return result


def select_clips(folder):
    """Select a complete nonoverlapping cover, ignoring redundant proof clips.

    A bounded proof can span the seam of two full sections. It is redundant
    when their union covers it, even though neither section contains it alone.
    Partial overlaps that cannot be covered without losing footage still fail.
    """
    candidates=[]
    for marker in Path(folder).glob("*.json"):
        clip=json.loads(marker.read_text(encoding="utf-8"))
        if clip.get("schema")==1 and "file" in clip:
            candidates.append((marker,clip))
    ordered=sorted(candidates,key=lambda v:(v[1]['source_end'],v[1]['source_start'],str(v[0])))
    ends=[c['source_end'] for _,c in ordered]
    best=[(0.,())]
    for index,(_,clip) in enumerate(ordered):
        start,end=clip['source_start'],clip['source_end']
        previous=bisect_right(ends,start,0,index)
        take=(best[previous][0]+end-start,best[previous][1]+(index,))
        skip=best[-1]
        best.append(take if (take[0],-len(take[1]))>(skip[0],-len(skip[1])) else skip)
    selected=sorted((ordered[i] for i in best[-1][1]),key=lambda v:v[1]['source_start'])
    if subtract_intervals([(c['source_start'],c['source_end']) for _,c in candidates],
                          [(c['source_start'],c['source_end']) for _,c in selected]):
        raise ValueError(f"Partially overlapping downloads in {folder}; use one nonoverlapping set.")
    return selected


def screened_end(clip, gate):
    end=clip['source_start']+gate['duration']
    # Rounded FPS metadata produced 0.000667-second gaps in the Twitch proxies.
    # Preserve larger shortfalls; sub-millisecond header rounding is not a gap.
    if abs(end-clip['source_end'])<.001:
        return clip['source_end']
    return min(end,clip['source_end'])


def gate_batch(root, workers=2, sources=None):
    if not 1<=workers<=4:
        raise ValueError("Use 1-4 screening workers.")
    root=Path(root).resolve(); tasks=[]; skipped=[]; results=[]
    for source_path in sorted(root.glob("*/source.json")):
        source=json.loads(source_path.read_text(encoding="utf-8")); ident=source["id"]
        layout=root/"layouts"/(ident+".json")
        if not layout.exists():
            skipped.append({"source_id":ident,"reason":"Layout has not been calibrated."});continue
        if sources is not None and ident not in {s['id'] for s in sources}:
            continue
        for marker,clip in select_clips(source_path.parent/"360p"):
            video=marker.parent/clip["file"];output=source_path.parent/"gates"/marker.stem
            actual_hash=sha256(video)
            if actual_hash!=clip['sha256']:
                raise ValueError(f'Downloaded section changed: {video}')
            cached=output/"gate.json"
            if cached.exists():
                gate=json.loads(cached.read_text(encoding="utf-8"))
                if gate["layout_sha256"]==sha256(layout) and gate["video_sha256"]==actual_hash:
                    results.append((source,clip,gate));continue
            tasks.append((source,clip,video,layout,output))
    began=time.perf_counter(); failures=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(gate_video,video,layout,output):(source,clip)
                 for source,clip,video,layout,output in tasks}
        for future in as_completed(futures):
            source,clip=futures[future]
            try:
                gate=future.result();results.append((source,clip,gate))
                kept=sum(b-a for a,b in gate["intervals"])
                print(f"{source['id']} {clock(clip['source_start'])}: retain {kept:.0f}/{gate['duration']:.0f}s; "
                      f"scan {gate['processing_seconds']:.1f}s",flush=True)
            except Exception as error:
                failures.append({"source_id":source["id"],"start":clip["source_start"],"error":str(error)})
    summaries=[]
    for ident in sorted({s["id"] for s,_,_ in results}):
        clips=[(s,c,g) for s,c,g in results if s["id"]==ident]
        source=clips[0][0]
        kept=merge_intervals([(c["source_start"]+a,min(c["source_end"],c["source_start"]+b)) for _,c,g in clips for a,b in g["intervals"]])
        scanned=merge_intervals([(c['source_start'],screened_end(c,g)) for _,c,g in clips])
        # Approximate stream-copy timestamps need extra context when re-fetching.
        refetch=merge_intervals([(max(a-10,x),min(b+10,y)) for a,b in kept for x,y in scanned
                                if max(a-10,x)<min(b+10,y)])
        duration=sum(b-a for a,b in scanned)
        requested=next((s for s in sources or [] if s['id']==ident),None)
        expected=[[seconds(a),min(source['duration'],seconds(b)) if b is not None else source['duration']]
                  for a,b in requested['windows']] if requested else None
        summaries.append({"source_id":ident,"url":source["url"],"uploader":source["uploader"],
                          "upload_date":source["upload_date"],"screened_seconds":duration,
                          "candidate_dungeon_seconds":sum(b-a for a,b in kept),
                          "intervals":kept,"screened_intervals":scanned,"clips":len(clips),
                          "refetch_intervals":refetch,
                          "requested_intervals":expected,
                          "missing_intervals":subtract_intervals(expected,scanned) if expected else None,
                          "decode_warnings":sum(bool(g["decode_warnings"]) for _,_,g in clips),
                          "transport_warnings":sum(bool(c["transport_warning"]) for _,c,_ in clips)})
    report={"schema":1,"sources":summaries,"missing_layouts":skipped,"failures":failures,
            "missing_sources":[s['id'] for s in sources or [] if s['id'] not in {r['source_id'] for r in summaries}],
            "new_processing_seconds":time.perf_counter()-began,
            "interpretation":"Unvalidated screening: retained spans may include other screens; rejected spans need recall auditing. Upload dates are not verified game versions. No footage is deleted."}
    write_json(root/"screening.json",report)
    # Direct input for a selective 720p fetch; no signed download URLs persist.
    write_json(root/'retained-sources.json',{'schema':1,'sources':[
        {'id':s['source_id'],'url':s['url'],'windows':s['refetch_intervals'],
         'note':'Unvalidated first-pass selection. Audit excluded footage before estimating odds.'}
        for s in summaries if s['intervals']]})
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Dungeon VOD screening</title>',
           '<style>body{font:16px system-ui;background:#101c22;color:#e8e7df;max-width:1100px;margin:35px auto;padding:20px}a{color:#edc676}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #52626a}summary{cursor:pointer;padding:12px}li{margin:8px}p{line-height:1.6}.bar{display:flex;height:28px;background:#27383f}.keep{background:#d5b664}</style>',
           '<h1>Dungeon VOD screening</h1><p>Candidate dungeon intervals. This first pass identifies footage for closer inspection; it does not measure room odds. Check rejected spans too. Original downloads remain intact.</p>',
           '<table><tr><th>Source</th><th>Upload date</th><th>Screened</th><th>Retained</th><th>Pruned</th></tr>']
    for s in summaries:
        pruned=s['screened_seconds']-s['candidate_dungeon_seconds']
        parts.append(f"<tr><td>{html.escape(s['uploader'] or '')} / {html.escape(s['source_id'])}</td><td>{html.escape(s['upload_date'] or 'unknown')}</td><td>{clock(s['screened_seconds'])}</td><td>{clock(s['candidate_dungeon_seconds'])}</td><td>{clock(pruned)}</td></tr>")
    parts.append('</table><p><a href="audit.html">Inspect sampled retained and rejected footage</a> · <a href="candidates.html">Review door candidates</a></p>')
    for s in summaries:
        parts.append(f"<details><summary>{html.escape(s['uploader'] or s['source_id'])}: {len(s['intervals'])} retained intervals</summary><ul>")
        for a,b in s['intervals']:
            separator='&' if '?' in s['url'] else '?'
            stamp=f'{int(a)}s' if 'youtube.com' in s['url'] else f'{int(a)//3600}h{int(a)%3600//60}m{int(a)%60}s'
            url=html.escape(s['url']+separator+'t='+stamp,quote=True)
            parts.append(f'<li><a href="{url}" target="_blank" rel="noopener">{clock(a)} - {clock(b)}</a></li>')
        parts.append('</ul></details>')
    missing=[{'source_id':s['source_id'],'missing_intervals':s['missing_intervals']} for s in summaries if s['missing_intervals']]
    if skipped or failures or missing or report['missing_sources']:
        parts.append('<h2>Incomplete work</h2><pre>'+html.escape(json.dumps(skipped+failures+missing+report['missing_sources'],indent=2))+'</pre>')
    parts.append('</html>')
    (root/"screening.html").write_text('\n'.join(parts),encoding="utf-8")
    return report


def scan_batch(root, art, workers=2, max_clips_per_source=1, fps=8):
    """Bounded detailed pilot. Zero clip limit explicitly requests every clip."""
    from .vision import scan
    from .review import make_review
    if not 1<=workers<=4 or max_clips_per_source<0:
        raise ValueError('Use 1-4 workers and a nonnegative clip limit.')
    root=Path(root).resolve(); work=[]
    for source_path in sorted(root.glob('*/source.json')):
        source=json.loads(source_path.read_text(encoding='utf-8'));ident=source['id']
        layout=root/'layouts'/(ident+'.json')
        if not layout.exists():
            continue
        clips=select_clips(source_path.parent/'360p')
        if max_clips_per_source:
            clips=clips[:max_clips_per_source]
        for marker,clip in clips:
            if sha256(marker.parent/clip['file'])!=clip['sha256']:
                raise ValueError(f'Downloaded section changed: {marker.parent/clip["file"]}')
            output=source_path.parent/'scans'/'360p'/('v'+VERSION)/marker.stem
            completed=output/'scan.json'
            if completed.exists():
                old=json.loads(completed.read_text(encoding='utf-8'))['inputs']
                layout_hash=sha256(layout)
                if old['layout_sha256']!=layout_hash or old['fps']!=fps:
                    output=output.with_name(output.name+f'-{layout_hash[:12]}-{fps:g}fps')
            work.append((source,clip,marker.parent/clip['file'],layout,output))
    results=[];failures=[];began=time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(scan,video,layout,output,art,clip['source_start'],source['url'],fps):output
                 for source,clip,video,layout,output in work}
        for future in as_completed(futures):
            output=futures[future]
            try:
                result=future.result();make_review(output)
                results.append({**result,'review':(output/'review.html').relative_to(root).as_posix()})
                print(f"{result['source_id']} {clock(result['source_start'])}: {result['candidates']} candidates; "
                      f"{result['processing_seconds']:.1f}s",flush=True)
            except Exception as error:
                failures.append({'output':str(output),'error':str(error)})
    report={'schema':1,'detector_version':VERSION,'runs':results,'failures':failures,
            'new_processing_seconds':time.perf_counter()-began,'max_clips_per_source':max_clips_per_source,
            'meaning':'Unreviewed visual candidates from a detailed pilot, not all decisions in the supplied VODs.'}
    write_json(root/'candidates-index.json',report)
    page=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Door candidate review</title>',
          '<style>body{background:#101c22;color:#e8e7df;font:17px system-ui;margin:40px}a{color:#edc676}li{margin:20px}</style>',
          '<h1>Door candidate review</h1><p>Candidate extraction needs human review and duplicate removal. This detailed pilot covers the first downloaded section of each source by default. <a href="screening.html">Screening timeline</a></p><ul>']
    for result in sorted(results,key=lambda r:(r['source_id'],r['source_start'])):
        page.append(f"<li><a href='{html.escape(result['review'],quote=True)}'>{html.escape(result['source_id'])} "
                    f"at {clock(result['source_start'])}</a>: {result['candidates']} candidates</li>")
    page.append('</ul><pre>'+html.escape(json.dumps(failures,indent=2))+'</pre></html>')
    (root/'candidates.html').write_text('\n'.join(page),encoding='utf-8')
    return report
