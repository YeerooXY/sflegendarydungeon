"""Compare spatial and whole-sprite door recognition on frozen native evidence."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import time

from sfld_vod.media import sha256, write_json
from sfld_vod.recognition_benchmark import summarize_predictions

ROOT = Path(__file__).resolve().parents[1]
_readers = {}


def evaluate(job):
    import cv2
    from sfld_vod.door_recognition import MaskedDoorRecognizer, stable_doors
    from sfld_vod.spatial_doors import SpatialDoorRecognizer
    cv2.setNumThreads(1)
    row = job['record']
    key = job['layout']
    if key not in _readers:
        _readers[key] = {'whole_sprite': MaskedDoorRecognizer(key, ROOT/'out/vod-art'),
                         'spatial_regions': SpatialDoorRecognizer(key, ROOT/'out/vod-art')}
    predictions = {m: {'frames': [], 'seconds': 0.} for m in _readers[key]}
    for proof in row['images']:
        image = Path(job['root']) / proof['image']
        if sha256(image) != proof['sha256']:
            raise ValueError('Changed benchmark evidence: ' + str(image))
        frame = cv2.imread(str(image))
        if frame is None:
            raise ValueError('Unreadable benchmark evidence: ' + str(image))
        for name, reader in _readers[key].items():
            start = time.perf_counter()
            result = reader.doors(frame)
            predictions[name]['seconds'] += time.perf_counter() - start
            predictions[name]['frames'].append(result)
    for prediction in predictions.values():
        prediction['stable'] = stable_doors(prediction['frames'])
    return {**row, 'predictions': predictions}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, action='append', required=True)
    p.add_argument('--layouts', type=Path, default=ROOT/'out/vods/layouts')
    p.add_argument('--partition', choices=('development', 'test', 'all'), default='development')
    p.add_argument('--workers', type=int, default=12)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.workers <= 16:
        raise ValueError('Use 1-16 workers.')
    jobs, ids, splits = [], set(), {}
    for path in args.manifest:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        root = Path(manifest.get('evidence_root', ROOT/'out/vods'))
        for row in manifest['records']:
            source = row['source_id']
            if source in splits and splits[source] != row['partition']:
                raise ValueError('Source appears in different benchmark partitions.')
            splits[source] = row['partition']
            if args.partition != 'all' and row['partition'] != args.partition:
                continue
            if row['observation_id'] in ids:
                raise ValueError('Repeated benchmark observation.')
            ids.add(row['observation_id'])
            jobs.append({'record': row, 'root': str(row.get('evidence_root', root)),
                         'layout': str(row.get('layout', args.layouts/(source+'.json')))})
    results = []
    began = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate, job) for job in jobs]
        for f in as_completed(futures):
            results.append(f.result())
    results.sort(key=lambda r: r['observation_id'])
    summary = summarize_predictions(results)
    output = {'schema': 1, 'partition': args.partition,
              'inputs': {str(p): sha256(p) for p in args.manifest},
              'code': {p: sha256(ROOT/'tools'/p) for p in
                       ('sfld_vod/door_recognition.py', 'sfld_vod/spatial_doors.py', 'benchmark_spatial_doors.py')},
              'elapsed_seconds': time.perf_counter()-began,
              'summary': summary, 'records': results,
              'limitations': ['Selected reviewed images, not whole-video room discovery recall.',
                              'Region checks are correlated; match scores are not probabilities.',
                              'Development measurements are not an independent accuracy estimate.']}
    write_json(args.output, output)
    for partition, group in summary.items():
        print(json.dumps({'partition': partition, 'offers': group['offers'],
                          'methods': {k: {level: {metric: value for metric, value in v[level].items()
                                                  if metric not in ('confusion','per_class')}
                                           for level in ('frames','stable_offer_sides')}
                                      for k,v in group['methods'].items()}}))


if __name__ == '__main__':
    main()
