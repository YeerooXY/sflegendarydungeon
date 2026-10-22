"""Publish acquisition coverage and an explicitly unverified review queue."""
from __future__ import annotations

import json
from pathlib import Path

from sfld_vod.batch import select_clips
from sfld_vod.media import sha256,write_json
from vod_monster_mystery import VERSION,SCAN_DIR,EVIDENCE_DIR

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'out/vod-older-2026-10-12'


def clock(value):
    value=round(value)
    return f'{value//3600:02}:{value%3600//60:02}:{value%60:02}'


def publish():
    catalog_path=WORK/'monster-mystery-candidates.json'
    catalog=json.loads(catalog_path.read_text())
    plan=json.loads((ROOT/'data/vods/trap-role-study-plan-2026-10-12.json').read_text())
    sources=[];evidence_checked=0
    for source in catalog['sources']:
        sid=source['id'];folder=WORK/sid
        if source['candidates']!=source['enriched_candidates']:
            raise ValueError('Native evidence extraction is incomplete: '+sid)
        clips=select_clips(folder/'720p')
        inventory=[]
        for marker,clip in clips:
            video=marker.parent/clip['file']
            if sha256(video)!=clip['sha256']:
                raise ValueError('Downloaded file changed: '+str(video))
            inventory.append({'source_start':clip['source_start'],'source_end':clip['source_end'],
                              'path':str(video.relative_to(ROOT)).replace('\\','/'),
                              'sha256':clip['sha256'],'bytes':clip['bytes'],
                              'video':clip['video'],'transport_warning':clip['transport_warning'],
                              'duration_delta_seconds':clip['duration_delta_seconds']})
        scans=[json.loads(p.read_text()) for p in sorted((folder/SCAN_DIR).glob('*/scan.json'))]
        for row in [r for r in catalog['candidates'] if r['source_id']==sid]:
            for e in row.get('evidence',[]):
                if sha256(WORK/e['image'])!=e['sha256']:
                    raise ValueError('Native evidence changed: '+e['image'])
                evidence_checked+=1
        sources.append({**source,'downloaded_seconds':sum(c['source_end']-c['source_start'] for c in inventory),
                        'clips':inventory,'downloaded_sections':len(inventory),'scanned_sections':len(scans),
                        'download_bytes':sum(c['bytes'] for c in inventory),
                        'scanned_frames':sum(s['scanned_frames'] for s in scans),
                        'scan_worker_seconds':sum(s['processing_seconds'] for s in scans),
                        'mode_from_title':'ultimate' if 'ultimate' in source['title'].lower() else 'ordinary_or_unknown',
                        'missing_scan_intervals':[[c['source_start'],c['source_end']] for c in inventory
                                                  if not any(s['source_start']==c['source_start'] and s['source_end']==c['source_end'] for s in scans)]})
    benchmark_path=ROOT/f'data/vods/monster-mystery-detector-benchmark-2026-10-12-v{VERSION}.json'
    benchmark=json.loads(benchmark_path.read_text())
    report={'schema':1,'date':'2026-10-12','detector_version':VERSION,'workers':8,
            'hardware':'AMD Ryzen 9 9950X: 16 cores / 32 logical processors; 64 GB installed RAM',
            'sources':sources,'total_downloaded_seconds':sum(s['downloaded_seconds'] for s in sources),
            'total_scanned_seconds':sum(s['scanned_seconds'] for s in sources),
            'candidate_views':len(catalog['candidates']),
            'candidates_with_monster_color_support':sum(r['color_support']['monster_color_supported'] for r in catalog['candidates']),
            'native_evidence_files_checked':evidence_checked,
            'verified_new_trap_observations':0,'queue_sha256':sha256(catalog_path),
            'benchmark':benchmark['summary'],'study_plan':plan,
            'limitations':['Candidate views are not unique room-choice observations and are not an odds denominator.',
                           'The detector was adjusted using the existing positive benchmark; its final recall is not held-out accuracy.',
                           'Partial-sprite and red-glow fallback deliberately accept extra false positives to recover obscured trapped pairs.',
                           'Every new trap, room, run, stone and mode label still requires review; reviewed candidate metadata is stored separately.',
                           'Upload date is not the event date. December 2024 footage uploaded in January 2025 must not be treated as a January event.',
                           'Record Standard/Ultimate mode as a stratum. Officially the gameplay is the same and rewards are multiplied; mode is not a blanket exclusion from trap-generation evidence.',
                           'No new completion average or simulator probability was produced by this acquisition pass.']}
    write_json(ROOT/'docs/older-vod-trap-hunt-2026-10-12.json',report)
    public=ROOT/'data/vods/older-trap-hunt-2026-10-12'
    public.mkdir(parents=True,exist_ok=True)
    write_json(public/'sources.json',{'schema':1,'sources':sources})
    with (public/'candidates.jsonl').open('w',encoding='utf-8') as stream:
        for row in catalog['candidates']:
            compact={k:row.get(k) for k in ('id','source_id','start','end','best_time','score','monster_side',
                                           'occlusion_fallback','mode_suggestion','room_suggestion','gem_suggestions',
                                           'nearby_candidate_ids','color_support','status','video_sha256','evidence')}
            compact['evidence_root']='out/vod-older-2026-10-12'
            stream.write(json.dumps(compact)+'\n')
    ordinary_seconds=sum(s['downloaded_seconds'] for s in sources if s['mode_from_title']!='ultimate')
    lines=['# Older POV search and trap-placement study — 2026-10-12','',
           f'Downloaded **{len(sources)} older public POVs ({clock(report["total_downloaded_seconds"])})** and scanned '
           f'**{clock(report["total_scanned_seconds"])}** at 8 fps with eight parallel workers. '
           f'The queue contains **{report["candidate_views"]} unverified candidate views**. '
           'These include opening rooms, transitions, false positives and repeated views; they are not that many unique eligible pairs. '
           f'Of them, **{report["candidates_with_monster_color_support"]}** have red visible inside the proposed monster doorway. '
           'The remaining dark/low-color views are also retained; this cue is a review priority, not a verified label.', '',
           f'One source explicitly advertises Ultimate mode. The other recordings total **{clock(ordinary_seconds)}**, '
           'but titles alone do not establish their mode. Playa describes identical gameplay with larger Ultimate rewards, '
           'so both modes can inform trap generation; mode is retained as a stratum. No older footage has yet been pooled into the current trap estimate. '
           '[Official Ultimate guide](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/201-legendary-dungeon-ultimate-1670924829/?p=ios).', '',
           'The local [quick voting page](http://127.0.0.1:8795/monster-mystery.html) shows a native image with '
           'the predicted door types and trap labels above it. **C** accepts all shown labels, **I** flags at '
           'least one incorrect label, and **D** flags a duplicate; each advances immediately. In the correction '
           'pass, **1/2** selects a side, letter keys choose its door type, **T** toggles a trap, **?** leaves trap '
           'presence unknown, and **Enter** saves and advances. **0** marks a non-choice screen. '
           '**Backspace** undoes the latest action. Votes save locally; use **Export votes** to retain a file.', '',
           'The [detailed review](http://127.0.0.1:8795/monster-mystery-detail.html) retains the earlier annotations, '
           'video stepping, stone fields and time-sampled control frames. Quick votes confirm image labels only; '
           'room identity, stones and outcome still need their own evidence before inclusion in probability estimates.', '',
           'The [shared room-counter pass](ROOM_COUNTER_AUDIT_2026_10_12.md) now covers every downloaded source. '
           'In the detailed review, use **Room counter coverage** to distinguish non-target rooms from unread gaps, and switch between '
           'proposed counter groups and every detector view. Grouping retains all originals and does not '
           'establish a unique visit or automatically confirm an observation.', '',
           '## Sources','',
           '| Creator / original source | Uploaded | Downloaded | Scanned | Candidate views |',
           '| --- | --- | ---: | ---: | ---: |']
    for s in sources:
        date=s['upload_date']
        if date and len(date)==8:
            date=f'{date[:4]}-{date[4:6]}-{date[6:]}'
        lines.append(f'| [{s["uploader"]} / {s["id"]}]({s["url"]}) | {date} | {clock(s["downloaded_seconds"])} | {clock(s["scanned_seconds"])} | {s["candidates"]} |')
    lines+=['','Jurik’s 2023/2024/2025 first-run recordings, BlackGreyGreen’s December 2024 recordings '
            '(uploaded January 2025), and VoiD’s 2025 sessions remain separate by source and event. '
            'Smeky’s explicitly Ultimate session is a separate mode stratum. '
            'The [acquisition manifest](../examples/vod-older-2026-10-12.json) and '
            '[machine-readable report](older-vod-trap-hunt-2026-10-12.json) contain the full inventory.','',
            '## What 25% does and does not imply','',
            '**25% per pair** is a reasonable provisional value: the previous shared-pair model fitted 24.68%, '
            'with a model-conditional 95% interval of approximately 20.57–29.12%. '
            'This rounded value is a working assumption, not proof of a programmed constant.', '',
            '| Candidate mechanism | One eligible option: any trap | Two eligible options: any trap | Monster share in trapped monster/mystery pairs |',
            '| --- | ---: | ---: | ---: |',
            '| Sequential 25% rolls, monster generated first, stop after first trap | 25% | 43.75% | 57.14% |',
            '| One 25% pair roll, uniform eligible placement | 25% | 25% | 50% |',
            '| One 25% pair roll, separately biased placement | 25% | 25% | Whatever the separate placement rule specifies |','',
            'The existing 200-versus-200 eligible-choice audit favored one pair roll over equal sequential rolls. '
            'A 57.14% placement bias could coexist with one pair roll, but would require an additional rule. '
            'A fixed 25% per-pair chance corresponds to a 25% per-option chance with one eligible option and '
            '12.5% per eligible option when there are two; those denominators must not be mixed.', '',
            '## How much data distinguishes 57.14% from 50%?','',
            'Exact two-sided binomial tests below use a fixed sample and a 5% false-positive limit. '
            '“Power” is the chance of detecting a difference if 57.14% is the true monster share. '
            'It is not the probability that a conclusion or model is correct.', '',
            '| Desired detection power | Readable trapped pairs | All monster/mystery choices at a 25% trap rate | Allowing for the previous readable yield (43/214) |',
            '| --- | ---: | ---: | ---: |']
    for r in plan['power_table']:
        if r['sides']==2 and r['alpha']==.05:
            lines.append(f'| {r["target_power"]:.0%} | {r["n_trapped_pairs"]:,} | {r["total_monster_mystery_at_pair_trap_25"]:,} | {r["total_monster_mystery_at_prior_readable_rate"]:,} |')
    lines+=['','Thus **1,000 total monster/mystery choices** would yield about 250 trapped pairs and only '
            '**62% detection power** under these assumptions. **1,000 readable trapped pairs** would give about '
            '**99.45% power**. The distinction is substantial.', '',
            'Estimating the percentage precisely is a different task: approximate 95% margins of ±3 and ±2 '
            'percentage points require about **1,046** and **2,352 trapped pairs**, respectively. '
            'No finite sample guarantees the exact constant 57.142857%. Shared runs/sources or different event rules '
            'can increase the effective sample requirement.', '',
            'The current verified placement count remains **24 monster / 19 mystery**, or **55.8%** with a '
            'Wilson 95% interval of **41.1–69.6%**. It permits both 50% and 57.14%. '
            'Current ordinary footage contains roughly 3–4% monster/mystery choices; this targeted older queue '
            'cannot establish whether older events have a different prevalence.', '',
            '## Recognition and review safeguards','',
            'The initial full-door matcher found 21/24 monster-trapped, 17/19 mystery-trapped, and 152/154 '
            'untrapped pairs in the existing positive corpus. Trap overlays caused differential misses. '
            'The revised matcher adds partial image regions and a permissive red-glow fallback. '
            'Its final development check is:', '',
            '| Known stratum | Found / available |', '| --- | ---: |']
    for k,v in benchmark['summary'].items():lines.append(f'| {k.replace("_"," ")} | {v["found"]}/{v["total"]} |')
    lines+=['','This corpus was used to improve the detector, so these results are **not held-out accuracy**. '
            'The tradeoff is more false positives for human review. Time-sampled control frames, source/event '
            'strata and unknown labels remain necessary. Candidate counts never directly set simulator odds.', '',
            f'{evidence_checked:,} native evidence references and all retained video fingerprints were checked during publication. '
            'Videos and artwork stay in ignored `out/`; the public data contain source URLs, timestamps, hashes and suggestions.', '',
            '## Reproduce','', '```powershell',
            'out/vod-env/Scripts/python tools/vod.py fetch --sources examples/vod-older-2026-10-12.json --directory out/vod-older-2026-10-12 --height 720 --chunk-seconds 600 --workers 3',
            'out/vod-env/Scripts/python tools/vod_monster_mystery.py scan --workers 8',
            'out/vod-ocr-env/Scripts/python tools/vod_monster_mystery.py enrich --workers 4',
            'out/vod-env/Scripts/python tools/vod_monster_mystery.py catalog',
            'out/vod-env/Scripts/python tools/prepare_quick_vod_review.py --workers 12',
            'out/vod-env/Scripts/python tools/vod_monster_mystery.py catalog',
            'python tools/plan_trap_role_study.py',
            'out/vod-env/Scripts/python tools/publish_older_vod_trap_hunt.py',
            'out/vod-env/Scripts/python tools/vod.py serve --directory out/vod-older-2026-10-12 --port 8795',
            '```','',
            'Source layout coordinates are preserved in [the layout manifest](../examples/vod-older-layouts-2026-10-12.json). '
            'The scanner needs these schema-1 files under the working directory’s `layouts/` folder. '
            'Cached work units are fingerprinted; interrupted sections can be rerun. '
            'Do not run two scan coordinators over the same version/source/section at once.','']
    lines+=['## Parser feedback','',
            'Use `tools/import_quick_vod_reviews.py exported-votes.json --directory out/vod-older-2026-10-12 '
            '--output out/review-imports/session-name` to validate an export and produce image labels and a '
            'door confusion report. Original predictions and reviewed corrections remain separate. '
            'Uncorrected incorrect votes and duplicates are excluded from confirmed labels. '
            'A no-match trap result remains unknown in the detector output; the voting screen may propose '
            '“No trap” as a hypothesis requiring explicit human confirmation. These proposals never count '
            'as measured absences automatically.','']
    (ROOT/'docs/OLDER_VOD_TRAP_HUNT_2026_10_12.md').write_text('\n'.join(lines),encoding='utf-8')
    layouts={p.stem:json.loads(p.read_text()) for p in sorted((WORK/'layouts').glob('*.json'))}
    write_json(ROOT/'examples/vod-older-layouts-2026-10-12.json',{'schema':1,'layouts':layouts})
    print(json.dumps({k:report[k] for k in ('total_downloaded_seconds','total_scanned_seconds','candidate_views','native_evidence_files_checked','verified_new_trap_observations')},indent=2))


if __name__=='__main__':publish()
