"""Build a six-source (or arbitrary manifest) review index with explicit coverage."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .batch import clock, select_clips, subtract_intervals
from .media import load_sources, seconds, write_json
from .temporal import build_timeline, observation_counts


def build_catalog(root, plan):
    root = Path(root).resolve()
    config = json.loads(Path(plan).read_text(encoding='utf-8'))
    if config.get('schema') != 1:
        raise ValueError('Unknown review plan schema.')
    summaries = []; all_observations = []
    for entry in config['sources']:
        ident = entry['source_id']
        scenes = sorted(root.glob(entry['scene_glob'])) if entry.get('scene_glob') else []
        candidates = sorted(root.glob(entry['candidate_glob'])) if entry.get('candidate_glob') else []
        observations = root/entry['observations'] if entry.get('observations') else None
        hud = root/entry['hud_suggestions'] if entry.get('hud_suggestions') else None
        result = build_timeline(root, ident, scenes, candidates, observations, hud)
        review = observation_counts(result['observations'])
        all_observations.extend(result['observations'])
        expected = [[seconds(a), seconds(b)] for a, b in entry['windows']]
        downloaded = [[c['start'], c['end']] for c in result['clips']]
        detailed = []
        for path in candidates:
            marker = path.parent/'scan.json'
            if marker.exists():
                scan = json.loads(marker.read_text(encoding='utf-8'))
                # Clip scan can use a gate internally; this is not a full-frame audit.
                clip = next((c for c in result['clips'] if c['start'] == scan['source_start']), None)
                if clip:
                    detailed.append([clip['start'], clip['end']])
        source = result['source']
        summary = {'source_id': ident, 'uploader': source.get('uploader'), 'url': source['url'],
                   'upload_date': source.get('upload_date'), 'requested_intervals': expected,
                   'requested_seconds': sum(b-a for a,b in expected),
                   'missing_downloads': subtract_intervals(expected, downloaded),
                   'missing_ungated_scan': subtract_intervals(expected, result['coverage']['ungated_scan_intervals']),
                   'missing_detailed_clip_scan': subtract_intervals(expected, detailed) if candidates else None,
                   'candidates': len(result['candidates']), 'observation_coverage': review['coverage'],
                   'review_page': ident+'-review.html', 'review_notes': entry.get('notes', ''),
                   'candidate_review_complete': entry.get('candidate_review_complete', False),
                   'full_temporal_audit_complete': entry.get('full_temporal_audit_complete', False)}
        summaries.append(summary)
        write_json(root/(ident+'-reviewed-counts.json'), review)
    combined = observation_counts(all_observations)
    write_json(root/'reviewed-counts.json', combined)
    (root/'reviewed-observations.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n'
                                                        for r in all_observations), encoding='utf-8')
    report = {'schema': 2, 'sources': summaries, 'total_requested_seconds': sum(s['requested_seconds'] for s in summaries),
              'total_candidates': sum(s['candidates'] for s in summaries), 'reviewed_counts': combined['coverage'],
              'all_requested_downloads_complete': all(not s['missing_downloads'] for s in summaries),
              'full_temporal_audit_complete': all(s['full_temporal_audit_complete'] for s in summaries),
              'interpretation': 'Candidate counts are not unique rooms. Full temporal audit requires reviewing misses, merges, repeats and context.'}
    write_json(root/'review-summary.json', report)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>Dungeon VOD review</title><style>body{font:16px system-ui;background:#101c22;color:#e8e7df;max-width:1300px;margin:35px auto;padding:20px}a{color:#edc676}table{border-collapse:collapse;width:100%}td,th{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #52626a}small{color:#b7c7cb}p{line-height:1.6}</style>',
             '<h1>Dungeon VOD review</h1>',
             f'<p>{len(summaries)} sources · {clock(report["total_requested_seconds"])} requested footage · {report["total_candidates"]:,} candidate views.</p>',
             f'<p>{combined["coverage"].get("decision",0):,} reviewed decisions; {combined["coverage"].get("eligible_decisions",0):,} eligible ordinary door pairs; {combined["coverage"].get("eligible_mystery_outcomes",0):,} linked mystery outcomes.</p>',
             f'<p>Chosen side verified in {combined["coverage"].get("decision_choice_verified",0):,} decisions; '
             f'both trap flags checked in {combined["coverage"].get("decision_traps_complete",0):,}. '
             'Candidate review does not mean every action or trap has been checked. Unknown means unverified, not absent or impossible to recover.</p>',
             '<p>Open a source to inspect consecutive frames, play the downloaded video and annotate each decision. '
             'Candidate views can repeat a room or contain several rooms. Unknown stone sets stay excluded from estimates.</p>',
             '<table><tr><th>Source</th><th>Window</th><th>Extraction</th><th>Reviewed observations</th><th>Audit status</th></tr>']
    for s in summaries:
        c = s['observation_coverage']
        status = ('Complete' if s['full_temporal_audit_complete'] else
                  'Candidate review complete; exhaustive recall not established' if s['candidate_review_complete'] else
                  'Review in progress: see source notes')
        window = ', '.join(clock(a)+'–'+clock(b) for a,b in s['requested_intervals'])
        scan = 'Full window, ungated' if not s['missing_ungated_scan'] else 'Candidates; ungated gaps remain'
        parts.append(f'<tr><td><a href="{html.escape(s["review_page"],quote=True)}">{html.escape(s["uploader"] or s["source_id"])}</a><br><small>{html.escape(s["source_id"])} · {html.escape(s["upload_date"] or "unknown date")}</small></td>'
                     f'<td>{window}</td><td>{s["candidates"]:,} candidates<br><small>{scan}</small></td>'
                     f'<td>{c.get("records",0):,} records<br><small>{c.get("eligible_decisions",0):,} eligible door pairs; {c.get("eligible_mystery_outcomes",0):,} linked mystery outcomes</small></td>'
                     f'<td>{status}<br><small>{html.escape(s["review_notes"])}</small></td></tr>')
    parts.append('</table><p><a href="review-summary.json">Coverage JSON</a> · <a href="reviewed-observations.jsonl">Reviewed observations</a> · '
                 '<a href="reviewed-counts.json">Conditional counts</a> · <a href="screening.html">Original screening</a></p>'
                 '<p>Export browser annotations to retain them in a file. Statistics count only explicitly verified fields and identities. '
                 'Dates and sources stay separate; footage uploaded in 2024 is not silently pooled with 2026 footage.</p></html>')
    (root/'review-all.html').write_text('\n'.join(parts), encoding='utf-8')
    return report
