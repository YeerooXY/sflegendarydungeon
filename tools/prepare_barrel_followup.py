"""Freeze an outcome-blind native-footage sample of unreviewed barrel visits."""
import collections
import hashlib
import json
from pathlib import Path
from sfld_vod.container_reanalysis import prepare_opening_cohort

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'out/vods/barrel-followup-2026-10-12'


def prepare():
    source = ROOT/'out/vods/full-reanalysis-2026-10-08/containers/inventory.json'
    rows = json.loads(source.read_text())
    seen = set()
    for path in (ROOT/'data/vods').glob('*.jsonl'):
        if not any(t in path.name for t in ('container', 'census')): continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            row = json.loads(line)
            if row.get('interaction') in ('opened', 'skipped', 'awakened') or row.get('reward_review_status', '').startswith('verified'):
                seen.add(row.get('observation_id'))
    groups = collections.defaultdict(list)
    for row in rows:
        if (row['initial_style'] != 'barrel' or row['reward_review_status'] != 'unreviewed'
            or row['observation_id'] in seen or not row['source_id'].isdigit()
            or row['context'].get('stone_status') != 'verified'): continue
        group = ','.join(sorted(set(row['context']['gems']) & {'gambler','time_traveler','thirsty'})) or 'unmodified'
        groups[group].append(row)
    seed = 'sfld-barrel-followup-2026-10-12-v1'
    key = lambda r: hashlib.sha256((seed+'|'+r['observation_id']).encode()).hexdigest()
    selected = [r for group, pool in sorted(groups.items()) for r in sorted(pool,key=key)[:24 if group=='unmodified' else len(pool)]]
    manifest = {'schema': 1, 'seed': seed, 'inventory_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'selection': '24 lowest hashes from unmodified visits; census of remaining modifier strata. Chosen before viewing outcomes. Unknowns/skips retained, no replacements.',
                'stratum_population': {g:len(v) for g,v in groups.items()}, 'maximum_seconds':12, 'fps':8,
                'rows':selected}
    OUT.mkdir(parents=True,exist_ok=True); dest=OUT/'manifest.json'
    if dest.exists() and json.loads(dest.read_text()) != manifest: raise RuntimeError('Frozen cohort changed')
    if not dest.exists(): dest.write_text(json.dumps(manifest,indent=2)+'\n')
    prepare_opening_cohort(dest,OUT/'frames')


if __name__ == '__main__': prepare()
