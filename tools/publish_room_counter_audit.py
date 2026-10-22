"""Publish the visually checked obstruction inventory and counter coverage."""
from collections import Counter
from html import escape
import json
from pathlib import Path
from sfld_vod.media import write_json
from vod_room_library import ROOT,LIBRARY_ROOTS
from sfld_vod.room_sequence import SEQUENCE_DIR
from vod_room_sequence import inventory_jobs

OUT=ROOT/'out/vod-room-library-audit'
PUBLIC=ROOT/'data/vods/room-counter-2026-10-12'

# Read manually from the retained first/last native HUD samples. None means
# no exact reference number established, never an inferred counter value.
READINGS={
 'youtube-R4BaFbNCKMg':(None,None),'youtube-fA4hH6ZnuS0':(None,None),
 '2877355041':(10,None),'2880307016':(16,19),'2885500891':(26,80),'youtube-DCb80VikGM0':(1,13),
 '2881887759':(7,42),'2877771092':(6,79),'2878333727':(4,None),'2881714434':(6,44),
 'youtube-DGjXHJwpBpk':(14,93),'youtube-_rkFzsZJ_sg':(7,93),'youtube-ebPi6T3ZnfM':(None,93),
 'youtube-jI45ZmPGuA8':(16,69),'youtube-y-_kM8vi0rQ':(13,88),
 '2878439002':(14,8),'2879175331':(27,11),'2880182419':(31,94),'2881864875':(90,61),
 '2877408140':(None,None),'2878297081':(None,None),'2879246515':(None,None),'2880085656':(None,None),'2881289951':(None,None),
 '2877350009':(42,71),'2878232329':(31,45),'2879181344':(39,66),'2880184828':(4,66),
 '2880968138':(28,3),'2881960605':(9,16),'2882669649':(48,84),'2883711077':(52,64),
 '2884397465':(31,37),'2885348704':(48,4),'2885567293':(None,14),
 '2877531246':(15,6),'2879510384':(40,61),'2876542901':(1,34),
 '2877419504-left':(30,39),'2877419504-right':(19,49),'2880176202':(16,None),
 '2881023333':(69,None),'2881884794':(1,None),'2882738740':(10,None),'2883589492':(1,None),
 '2883910762':(74,74),'youtube-z9-ZQXSYilE':(None,None),
 'youtube-3Wj28RH0P8w':(1,99),'youtube-l5sDUG3vMG4':(7,71),
 '2876672007':(9,86),'2877357594':(None,None),'2878460304':(33,97),'2879135287':(None,60),
 '2880311940':(30,51),'2881153716':(8,21),'2881859569':(16,7),'2882868478':(40,64),'2883483527':(25,59),
}


def classify(row):
    sid=row['source_id'];creator=row['uploader']
    if creator=='BlackGreyGreen':
        return 'numerator_covered','Decorative overlay covers the room-number digits in both checked samples; /100 remains visible.',True
    if creator=='Maexel':
        return 'mascot_sign_overlap','Mascot/sign covers or clips the counter. Some fragments are readable, but a missing leading digit cannot be excluded automatically.',True
    if sid in {'2881023333','2881884794','2882738740','2883589492'}:
        return 'relocated_counter','Bottom counter overlaps the frame/caption. A readable duplicate counter is displayed at the top left; a dedicated reader uses that copy.',False
    if sid=='2880176202':
        return 'bottom_edge_clipped','Official/Tuny layout clips the bottom of the counter. At least one number is human-readable, but automatic anchors require review.',True
    if sid=='youtube-z9-ZQXSYilE':
        return 'left_edge_overlap','A narrow vertical overlay cuts into the first digit. Exact numerators are not established in the checked samples; automatic anchors are withheld.',True
    if sid=='2877355041':
        return 'intermittent_caption_overlap','Clear earlier; a Zero Death Run caption overlaps the counter in the later checked sample.',False
    if sid=='2877357594':
        return 'banner_overlap','A large announcement banner covers the counter in both checked samples. This does not prove it covers the entire recording.',False
    if sid=='2879135287':
        return 'intermittent_banner_overlap','A banner covers the first checked counter; the later checked frame has a clear 60/100.',False
    if sid=='2883910762':
        return 'clear_in_checked_frames','Counter clear in checked frames. The source contains an embedded-video segment; sequence/run identity still needs review.',False
    if sid in {'2878333727','youtube-ebPi6T3ZnfM','2885567293'}:
        return 'clear_when_hud_present','A checked frame has no dungeon HUD during a transition/interior; a different checked frame has a clear counter. Absence is not an obstruction.',False
    return 'clear_in_checked_frames','Counter is unobstructed in both checked native samples. This is a sampled audit, not a claim that every frame is readable.',False


def main():
    data=json.loads((OUT/'audit.json').read_text());reviewed=[]
    coverage={};blocks=[];room_times={}
    for root in LIBRARY_ROOTS:
        path=ROOT/root/SEQUENCE_DIR/'catalog.json'
        if path.exists():
            catalog=json.loads(path.read_text())
            for s in catalog['sources']:coverage[s['source_id']]=s
            blocks.extend(catalog['blocks'])
            for span in catalog['spans']:
                if span['stable']:room_times.setdefault(span['sequence_id'],{}).setdefault(span['room'],span['start'])
    jobs=[j for root in LIBRARY_ROOTS for j in inventory_jobs(ROOT/root)]
    unique_intervals={(j['source_id'],j['start'],j['end']) for j in jobs}
    scan_summary={'downloaded_video_seconds':sum(end-start for _,start,end in unique_intervals),
                  'scheduled_view_seconds':sum(j['end']-j['start'] for j in jobs),
                  'scheduled_sections':len(jobs),'catalog_sources':len(coverage),
                  'sampled_view_seconds':sum(s.get('sampled_seconds',0) for s in coverage.values()),
                  'stable_counter_spans':sum(s['stable_counter_spans'] for s in coverage.values()),
                  'tentative_sequence_blocks':len(blocks),
                  'candidate_views':sum(s['candidates'] for s in coverage.values()),
                  'anchored_candidate_views':sum(s['anchored_candidates'] for s in coverage.values()),
                  'proposed_candidate_groups':sum(s['candidate_review_groups'] for s in coverage.values())}
    for row in data['rows']:
        if row['view_id'] not in READINGS:raise ValueError('Unreviewed source '+row['view_id'])
        status,note,veto=classify(row)
        frames=[]
        for slot,expected in zip((0,2),READINGS[row['view_id']]):
            frames.append({**row['samples'][slot],'sample_index':slot,'observed_counter':expected,
                           'counter_location':'top' if status=='relocated_counter' else 'bottom',
                           'reviewer':'Codex visual inspection','obstruction_status':status})
        reviewed.append({k:row[k] for k in ('source_id','view_id','uploader','url','upload_date','library_root')}|
                        {'status':status,'note':note,'automatic_anchor_veto':veto,
                         'evidence_root':'out/vod-room-library-audit','reviewed_samples':frames,
                         'counter_coverage':coverage.get(row['source_id'])})
    payload={'schema':1,'date':'2026-10-12','sources':len({r['source_id'] for r in reviewed}),
             'views':len(reviewed),'visually_checked_samples':sum(len(r['reviewed_samples']) for r in reviewed),
             'scope':'Two selected native frames per POV, plus inspection of relocated top-counter examples. Not an every-frame obstruction audit.',
             'rows':reviewed,'status_counts':dict(Counter(r['status'] for r in reviewed)),
             'scan_summary':scan_summary}
    PUBLIC.mkdir(parents=True,exist_ok=True);write_json(PUBLIC/'visibility-reviewed.json',payload)
    write_json(OUT/'visibility-reviewed.json',payload)
    lines=['# Room-counter visibility audit — 2026-10-12','',
           '**57 downloaded videos, 58 separate POVs, 116 manually checked native frames.** '
           'The split-screen official stream has separate left/right player tracks. '
           'The full counter pass covers all acquired video intervals, including non-target room types; '
           'it does not imply that every minute of the original public VOD was downloaded.','',
           'The audit checks who obscures the counter. A clear sample is not proof that every frame is readable. '
           'Some counters disappear in transitions, and some videos switch characters, show replays or start mid-run. '
           'Those cases must not be silently merged by the same room number.','',
           '## Findings','',
           '- **BlackGreyGreen:** both December 2024 uploads hide the numerator behind an overlay.',
           '- **Maexel:** mascot/sign overlap in all five recordings checked; sometimes only fragments are visible.',
           '- **Zsombey:** announcement banners in the September 18 recording and intermittently in the September 20 recording. Other checked recordings show clear counters.',
           '- **DORZER:** the September 18 recording has an overlapping Zero Death Run caption in a later sample; other checked sources have clear counters.',
           '- **Official channel / Tuny:** a partly clipped lower counter on September 21. September 22–25 recordings have a readable copy at the top left, which the alternate reader uses.',
           '- **Smeky:** a narrow left-edge overlay cuts into the first digit. Automatic grouping is withheld pending review.',
           '- **Clear in checked dungeon frames:** N3utr4LSF, VoiD, Jurik Joriksson, Lootenant, SenseiMZee, Gorgoss, Fuxelbau, Jarzuul and Nils. Official-channel layouts vary by host.','',
           '## Every downloaded source','',
           '| Creator / source | Uploaded | Counter status | Notes |','| --- | --- | --- | --- |']
    for r in reviewed:
        date=r['upload_date'];date=f'{date[:4]}-{date[4:6]}-{date[6:]}' if date and len(date)==8 else date
        lines.append(f'| [{r["uploader"]} / {r["view_id"]}]({r["url"]}) | {date} | {r["status"].replace("_"," ")} | {r["note"]} |')
    lines+=['','## Shared sequence processing','',
            'Use `tools/vod_room_library.py scan --workers 12`, then `tools/scan_relocated_counters.py`, '
            'then `tools/refine_room_counters.py --workers 12`, then `tools/vod_room_library.py build`. '
            'Use the OCR virtual environment for scans/refinement. Each downloaded interval is sampled at 8 fps. '
            'The reader crops native pixels, requires the /100 denominator, checks two image variants, '
            'and requires agreement across time. Unchanged text can reuse an OCR result for speed, '
            'but those cache hits cannot count as independent confirmation.','',
            'A counter decrease or long visibility gap creates a new tentative sequence. It does not automatically '
            'prove a new run or character. Missing numbers are listed, never invented. Candidate views sharing '
            'a stable counter span are grouped for navigation while all originals remain available. Boss retries, '
            'arena phases and boundary-crossing candidates stay separate or explicitly unresolved.','',
            'Sources with persistent/partial numerator obstruction retain their OCR evidence, but automatic '
            'anchors are withheld. Other analyses can consume the same per-source counter catalog rather than '
            'running disconnected one-frame OCR. A readable counter alone does not establish that the offered '
            'door pair was visible, so full decision coverage still requires scene review.','',
            'Run `tools/refresh_room_counter_reviews.py` to update existing general review pages while preserving '
            'their annotations and browser-draft revision. Run `tools/vod_monster_mystery.py catalog` to update '
            'the targeted pair queue. Both consume the same shared counter tracks.','',
            '## Completed processing','',
            f'- Downloaded footage: {scan_summary["downloaded_video_seconds"]/3600:.3f} hours; '
            f'{scan_summary["scheduled_view_seconds"]/3600:.3f} player-view hours including the split screen.',
            f'- Counter catalogs: {scan_summary["catalog_sources"]} sources; '
            f'{scan_summary["sampled_view_seconds"]/3600:.3f} decoded player-view hours.',
            f'- Stable counter spans: {scan_summary["stable_counter_spans"]:,}. '
            'These include repeated displays, interiors and non-choice screens; they are not unique decisions.',
            f'- Targeted pair views anchored: {scan_summary["anchored_candidate_views"]:,} / '
            f'{scan_summary["candidate_views"]:,}; navigation retains '
            f'{scan_summary["proposed_candidate_groups"]:,} proposed groups including unresolved single views.',
            '- Sequence boundaries and unread gaps remain explicit. No additional probability observations '
            'are accepted solely from these automatic counter readings.','',
            '[Machine-readable visibility reviews](../data/vods/room-counter-2026-10-12/visibility-reviewed.json).', '']
    (ROOT/'docs/ROOM_COUNTER_AUDIT_2026_10_12.md').write_text('\n'.join(lines),encoding='utf-8')
    html=['<!doctype html><meta charset="utf-8"><title>Room counter visibility — all videos</title>',
          '<style>body{font:16px system-ui;background:#10191f;color:#eee;padding:24px}a{color:#edc65d}article{padding:18px;margin:16px 0;background:#1b2c36}img{max-width:310px;max-height:250px}figure{display:inline-block;margin:8px}p{max-width:1100px;line-height:1.5}</style>',
          '<h1>Room counter visibility — all 57 videos</h1><p>58 POVs. Two native samples per POV were checked manually. Click an image for its full game frame. This is an obstruction audit; sampled visibility does not prove complete room coverage.</p>',
          '<p>Expand a sequence to see counters throughout the acquired footage. Green numbers were read; red numbers were not read between anchors. Links open the original video near the evidence. A sequence is tentative, not a verified run.</p>']
    for r in reviewed:
        html.append(f'<article><h2><a href="{escape(r["url"],quote=True)}">{escape(r["uploader"])} / {escape(r["view_id"])}</a></h2><p>{escape(r["status"].replace("_"," "))}: {escape(r["note"])}</p>')
        for p in r['reviewed_samples']:
            html.append(f'<figure><a href="{escape(p["game"],quote=True)}"><img src="{escape(p["hud"],quote=True)}" alt="Native lower HUD"></a><figcaption>{p["source_seconds"]:.3f}s · exact number: {p["observed_counter"] if p["observed_counter"] is not None else "not established"}</figcaption></figure>')
        matching=[b for b in blocks if b['source_id']==r['view_id']]
        html.append(f'<p>{len(matching)} tentative counter sequence(s).</p>')
        for block in matching:
            html.append(f'<details><summary>Sequence {block["id"].split(":")[-1]} · {block["start"]:.1f}–{block["end"]:.1f}s · '
                        f'{len(block["observed_rooms"])} room numbers read · {len(block["missing_between_anchors"])} unread between anchors</summary><p>')
            first=room_times[block['id']]
            for number in range(block['first_room'],block['last_room']+1):
                stamp=first.get(number)
                seen=stamp is not None
                if stamp is None:stamp=next(g['start'] for g in block['gaps'] if number in g['rooms'])
                url=r['url']+('&' if '?' in r['url'] else '?')+f't={int(stamp)}s'
                color='#69b6a4' if seen else '#d87777'
                title='Counter read' if seen else 'Counter not read between anchors; inspect this interval'
                html.append(f'<a href="{escape(url,quote=True)}" title="{title}" style="display:inline-block;margin:4px;padding:5px;border:1px solid {color}">{number}</a>')
            html.append('</p></details>')
        html.append('</article>')
    (OUT/'index.html').write_text('\n'.join(html),encoding='utf-8')
    print(json.dumps({k:payload[k] for k in ('sources','views','visually_checked_samples','status_counts')},indent=2))


if __name__=='__main__':main()
