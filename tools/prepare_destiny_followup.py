"""Reproduce the outcome-blind Destiny offer sample and native review windows."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
from sfld_vod.container_reanalysis import prepare_opening_cohort

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vods/destiny-followup-2026-10-12'
SEED = 'destiny-followup-2026-10-12-v1'
SOURCES = {'2878232329', '2879181344', '2879246515', '2880176202',
           '2881153716', '2881859569', '2882738740'}
REFINED = [5, 7, 8, 9, 10, 12, 14, 15, 17, 18, 20, 23, 24, 28, 29]


def run(refine=False):
    corpus = ROOT / 'data/vods/observations-2026-10-08.jsonl'
    groups = collections.defaultdict(list)
    for line in corpus.read_text(encoding='utf-8-sig').splitlines():
        r = json.loads(line)
        if (r['source_id'] in SOURCES and r.get('status') == 'decision'
                and any(d.get('kind') == 'destiny' for d in r.get('doors', []))):
            groups[r['source_id']].append(r)
    selected = [r for source, rows in sorted(groups.items()) for r in sorted(
        rows, key=lambda r: hashlib.sha256((SEED + r['id']).encode()).hexdigest())[:5]]
    rows = []
    for r in selected:
        base = ROOT / ('out/vod-discovery' if r['source_id'] in {'2880176202', '2882738740'} else 'out/vods')
        rows.append({'observation_id': r['id'], 'source_id': r['source_id'],
                     'context': r['context'], '_base': str(base),
                     'entry_window_start': r['source_seconds'],
                     'entry_window_end': r['source_seconds'] + 12,
                     'offered_doors': r['doors']})
    manifest = {'schema': 1, 'seed': SEED,
                'corpus_sha256': hashlib.sha256(corpus.read_bytes()).hexdigest(),
                'selection': 'Five outcome-blind hash-selected Destiny offers per current Twitch source. Unchosen and unreadable outcomes retained; no replacements.',
                'pool_sizes': {s: len(rs) for s, rs in groups.items()},
                'maximum_seconds': 12, 'fps': 4, 'rows': rows,
                'source_path_resolution': 'Discovery sources use their existing out/vod-discovery cache; selected IDs and timestamps unchanged.'}
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / 'manifest.json'
    if path.exists():
        old = json.loads(path.read_text())
        if old['corpus_sha256'] != manifest['corpus_sha256'] or old['rows'] != rows:
            raise ValueError('Frozen cohort changed; choose a new study directory')
    else:
        path.write_text(json.dumps(manifest, indent=2) + '\n')
    if refine:
        selected = []
        for index in REFINED:
            r = dict(rows[index]); r['parent_sample_index'] = index
            r['entry_window_start'] += 6 if index == 12 else -.5
            r['entry_window_end'] = r['entry_window_start'] + 4.875
            selected.append(r)
        manifest = {'schema': 1, 'fps': 12, 'maximum_seconds': 5,
                    'selection': 'Refinement of every provisionally selected or ambiguous spin; no new observations.',
                    'rows': selected}
        path = WORK / 'refinement-manifest.json'
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError('Frozen refinement changed')
        path.write_text(json.dumps(manifest, indent=2) + '\n')
    prepare_opening_cohort(path, WORK / ('refined' if refine else 'frames'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--refine', action='store_true')
    run(parser.parse_args().refine)
