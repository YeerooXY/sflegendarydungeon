"""Check native offer proofs for contradictions with existing door labels.

This is an independent quality-control pass, not automatic relabeling. A first
frame agreeing with the ledger needs no further contradiction check. An accepted
disagreement must repeat in a second frame before becoming a review candidate.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
from .media import sha256, write_json

_recognizers = {}


def check(job):
    import cv2
    from .door_recognition import MaskedDoorRecognizer, stable_doors
    cv2.setNumThreads(1)
    path = Path(job['record']); record = json.loads(path.read_text(encoding='utf-8'))
    output = Path(job['output']) / path.name
    identity = {'record_sha256': sha256(path), 'art_sha256': sha256(Path(job['art'])/'manifest.json'),
                'layout_sha256': sha256(job['layout']), 'threshold': .52, 'margin': .10}
    if output.exists():
        old = json.loads(output.read_text(encoding='utf-8'))
        if old['inputs'] == identity:
            return old
    result = {'observation_id': record['observation_id'], 'source_id': record['source_id'],
              'source_seconds': record['source_seconds'], 'inputs': identity,
              'expected': [d['kind'] for d in record['doors']], 'status': 'unavailable',
              'contradictions': [], 'evidence': record.get('evidence', [])}
    if record['status'] != 'unresolved' and record.get('evidence'):
        key = (job['layout'], job['art'])
        if key not in _recognizers:
            _recognizers[key] = MaskedDoorRecognizer(*key)
        detector = _recognizers[key]
        samples = []
        for index, proof in enumerate(record['evidence']):
            image = path.parent/proof['image']
            if sha256(image) != proof['sha256']:
                raise ValueError('Native door proof hash mismatch.')
            samples.append(detector.doors(cv2.imread(str(image))))
            if index == 0 and not any(p['kind'] and p['kind'] != result['expected'][i]
                                      for i, p in enumerate(samples[0])):
                break
        result['first_frame'] = samples[0]
        if len(samples) == 2:
            agreed = stable_doors(samples)
            result['stable_predictions'] = agreed
            result['contradictions'] = [{'side': i, 'expected': result['expected'][i], 'suggested': p['kind']}
                                       for i, p in enumerate(agreed) if p['kind'] and p['kind'] != result['expected'][i]]
            result['status'] = 'review_disagreement' if result['contradictions'] else 'unresolved_disagreement'
        else:
            result['status'] = ('single_frame_agrees' if all(p['kind'] == result['expected'][i]
                               for i, p in enumerate(samples[0])) else 'partially_recognized_no_contradiction')
    write_json(output, result)
    return result


def run(scan_directories, art, output, workers):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    (output/'records').mkdir(exist_ok=True)
    inputs = {}
    for directory in scan_directories:
        directory = Path(directory)
        manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        for row in manifest['records']:
            token = hashlib.sha256(row['observation_id'].encode()).hexdigest()[:24]
            path = directory/'records'/(token+'.json')
            if path.exists():
                inputs[row['observation_id']] = {'record': str(path), 'layout': row['layout'],
                    'art': str(art), 'output': str(output/'records')}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, inputs.values(), chunksize=8))
    from collections import Counter
    summary = {'schema': 1, 'scope': 'Quality-control suggestions only; canonical labels unchanged.',
               'checked_inventory_records': len(results), 'statuses': dict(Counter(r['status'] for r in results)),
               'disagreements': [r for r in results if r['contradictions']]}
    write_json(output/'summary.json', summary)
    (output/'predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in results), encoding='utf-8')
    return {k: v for k, v in summary.items() if k != 'disagreements'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan', type=Path, action='append', required=True)
    p.add_argument('--art', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    if not 1 <= a.workers <= 16: p.error('Use 1-16 workers.')
    print(json.dumps(run(a.scan, a.art, a.output, a.workers)))


if __name__ == '__main__': main()
