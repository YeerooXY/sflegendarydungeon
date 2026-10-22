"""Native evidence benchmarks with source-grouped splits and abstention counts.

The benchmark never edits observations or turns model predictions into truth.
Sprites and source calibration can be shared between partitions; reviewed
frames, visits and runs cannot. Accuracy here is conditional on the selected
reviewed decision frames, not end-to-end discovery of all dungeon rooms.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import time

from .media import sha256, write_json


def build_manifest(labels_path, queue_paths, evidence_root, test_sources):
    root = Path(evidence_root).resolve()
    labels = json.loads(Path(labels_path).read_text(encoding='utf-8'))
    truth = {r['observation_id']: r for r in labels['records']}
    if len(truth) != len(labels['records']):
        raise ValueError('Duplicate verified label IDs.')
    records, seen = [], set()
    for queue_path in queue_paths:
        queue = json.loads(Path(queue_path).read_text(encoding='utf-8'))
        for item in queue['items']:
            ident = item.get('observation_id', item.get('id'))
            if ident in seen or ident not in truth:
                raise ValueError('Duplicate or unlabeled queued observation.')
            seen.add(ident)
            label = truth[ident]
            if item['context'] != label['context']:
                raise ValueError('Queued context differs from reviewed label context.')
            images = []
            allowed_proofs = {e['sha256'] for e in label['evidence']}
            for ev in item['evidence']:
                path = (root / ev['image']).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError('Native evidence must exist within its declared root.')
                if sha256(path) != ev['sha256'] or ev['sha256'] not in allowed_proofs:
                    raise ValueError('Native evidence differs from reviewed image proof.')
                if ev.get('native_height', 0) < 720:
                    raise ValueError('Native source must be at least 720p, without upscaling.')
                images.append({key: ev[key] for key in ('image', 'sha256', 'source_seconds', 'native_height')})
            records.append({'observation_id': ident, 'source_id': label['source_id'],
                'context': label['context'], 'partition': 'test' if label['source_id'] in test_sources else 'development',
                'images': images, 'image_space': 'native_game_crop',
                'doors': [d['kind'] for d in label['doors']],
                'traps': [d['trap'] for d in label['doors']],
                'gems': label['context'].get('gems') if label['context'].get('stone_status') == 'verified' else None})
    if seen != set(truth):
        raise ValueError('Queues must cover every labeled observation; no hard-case omission.')
    manifest = {'schema': 1, 'evidence_root': str(root), 'labels_sha256': sha256(labels_path),
        'queues': [{'sha256': sha256(p), 'name': Path(p).name} for p in queue_paths],
        'split_unit': 'source_id', 'test_sources': sorted(test_sources), 'records': records,
        'note': 'Native reviewed decision windows; fixed source-heldout split, no random neighboring-frame split. Images remain local.'}
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    if manifest.get('schema') != 1 or manifest.get('split_unit') != 'source_id':
        raise ValueError('A source-grouped schema 1 benchmark is required.')
    ids, images = set(), {}
    partitions = defaultdict(set)
    visits = defaultdict(set)
    for record in manifest['records']:
        ident = record['observation_id']
        if ident in ids:
            raise ValueError('Duplicate observation in benchmark.')
        ids.add(ident)
        part = record['partition']
        if part not in ('development', 'test'):
            raise ValueError('Unknown partition.')
        partitions[record['source_id']].add(part)
        ctx = record['context']
        visits[(record['source_id'], ctx.get('run_id'), ctx.get('room'), ctx.get('offer_attempt', 1))].add(part)
        if len(record.get('doors', [])) != 2 or len(record.get('images', [])) < 2:
            raise ValueError('Each offer needs two labels and at least two native frames.')
        for ev in record['images']:
            prior = images.setdefault(ev['sha256'], (ident, part))
            if prior != (ident, part):
                raise ValueError('An image cannot label different offers or cross partitions.')
        if len({e['sha256'] for e in record['images']}) != len(record['images']):
            raise ValueError('Repeated image cannot count as temporal agreement.')
    if any(len(p) > 1 for p in [*partitions.values(), *visits.values()]):
        raise ValueError('Source or visit leakage across partitions.')
    if set(p for parts in partitions.values() for p in parts) != {'development', 'test'}:
        raise ValueError('Both development and heldout test sources are required.')


def classification_metrics(pairs):
    """Unknown is an abstention, retained in recall/coverage denominators."""
    n = accepted = correct = 0
    per_class = defaultdict(Counter)
    confusion = defaultdict(Counter)
    for expected, predicted in pairs:
        if expected is None:
            continue
        n += 1
        per_class[expected]['support'] += 1
        confusion[expected][predicted if predicted is not None else '<unknown>'] += 1
        if predicted is not None:
            accepted += 1
            per_class[expected]['accepted'] += 1
            if predicted == expected:
                correct += 1
                per_class[expected]['correct'] += 1
    return {'total': n, 'accepted': accepted, 'correct': correct, 'incorrect': accepted-correct,
        'unknown': n-accepted, 'coverage': accepted/n if n else None,
        'precision_when_accepted': correct/accepted if accepted else None,
        'correct_fraction_all': correct/n if n else None,
        'per_class': {k: {'support': v['support'], 'accepted': v['accepted'], 'correct': v['correct'],
                         'unknown': v['support']-v['accepted'], 'incorrect': v['accepted']-v['correct']}
                      for k, v in sorted(per_class.items())},
        'confusion': {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())}}


def summarize_predictions(records):
    out = {}
    for partition in ('development', 'test', 'all_descriptive'):
        selected = [r for r in records if partition == 'all_descriptive' or r['partition'] == partition]
        methods = sorted({name for r in selected for name in r['predictions']})
        result = {'offers': len(selected), 'sources': sorted({r['source_id'] for r in selected}), 'methods': {}}
        for method in methods:
            rows = [r for r in selected if method in r['predictions']]
            frame_pairs = [(e, p['kind']) for r in rows for frame in r['predictions'][method]['frames']
                           for e, p in zip(r['doors'], frame)]
            offer_pairs = [(e, p['kind']) for r in rows
                           for e, p in zip(r['doors'], r['predictions'][method]['stable'])]
            exact = sum([p['kind'] for p in r['predictions'][method]['stable']] == r['doors'] for r in rows)
            result['methods'][method] = {'frames': classification_metrics(frame_pairs),
                'stable_offer_sides': classification_metrics(offer_pairs), 'complete_pairs_correct': exact,
                'complete_pairs_total': len(rows), 'complete_pair_fraction': exact/len(rows) if rows else None}
        gems = [r for r in selected if r.get('gems') is not None and 'gem_predictions' in r]
        expected = sum(len(r['gems']) for r in gems)
        suggested = sum(len(r['gem_predictions']['stable_gems']) for r in gems)
        correct = sum(len(set(r['gems']) & set(r['gem_predictions']['stable_gems'])) for r in gems)
        result['gems'] = {'offers_with_verified_full_context': len(gems), 'expected_gem_occurrences': expected,
            'suggested_gem_occurrences': suggested, 'correct_gem_occurrences': correct,
            'incorrect_gem_occurrences': suggested-correct, 'missing_gem_occurrences': expected-correct,
            'complete_stone_sets_asserted': 0,
            'note': 'Recognized gems are partial positive evidence. No unreadable slot is inferred empty.'}
        out[partition] = result
    return out


def run_benchmark(manifest, layouts, art):
    import cv2
    from .vision import Recognizer
    from .hud import GemReader
    from .door_recognition import MaskedDoorRecognizer, stable_doors
    validate_manifest(manifest)
    root = Path(manifest['evidence_root']).resolve()
    readers, records = {}, []
    gem_reader = GemReader(art)
    start = time.perf_counter()
    for item in manifest['records']:
        source = item['source_id']
        if source not in readers:
            layout = Path(layouts) / (source + '.json')
            readers[source] = {'original': Recognizer(layout, art), 'masked_color': MaskedDoorRecognizer(layout, art)}
        frames = []
        for ev in item['images']:
            path = (root / ev['image']).resolve()
            if not path.is_relative_to(root) or sha256(path) != ev['sha256']:
                raise ValueError('Benchmark native evidence changed or escaped its root.')
            frame = cv2.imread(str(path))
            if frame is None or min(frame.shape[:2]) < 320:
                raise ValueError('Benchmark requires actual native game crops.')
            frames.append(frame)
        predictions = {}
        for name, reader in readers[source].items():
            sampled = [reader.doors(cv2.resize(frame, (320, 224), interpolation=cv2.INTER_AREA)) for frame in frames]
            predictions[name] = {'frames': sampled, 'stable': stable_doors(sampled)}
        gem_frames = [gem_reader.read(frame) for frame in frames]
        gem_counts = Counter(g for f in gem_frames for g in set(f['gems_suggested']))
        stable_gems = sorted(g for g, count in gem_counts.items() if count >= 2)
        records.append({**item, 'predictions': predictions,
            'gem_predictions': {'frames': gem_frames, 'stable_gems': stable_gems, 'complete': False}})
    return {'schema': 1, 'benchmark': 'native_door_and_gem_recognition',
        'status': 'unreviewed_predictions_only', 'elapsed_seconds': time.perf_counter()-start,
        'art_manifest_sha256': sha256(Path(art)/'manifest.json'),
        'thresholds': {'original': {'score': .52, 'margin': .10}, 'masked_color': {'score': .52, 'margin': .10}},
        'summary': summarize_predictions(records), 'records': records,
        'limitations': ['Selected native decision frames; this does not measure room discovery recall.',
            'Only two source windows initially; absent classes have no measured accuracy.',
            'Frames from one offer/run are correlated; do not treat them as independent outcomes.',
            'Gem reader never proves empty slots or a complete unmodified stone set.',
            'Predictions must not be copied into verified observations automatically.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--labels', required=True)
    build.add_argument('--queue', action='append', required=True)
    build.add_argument('--evidence-root', required=True)
    build.add_argument('--test-source', action='append', required=True)
    build.add_argument('--output', required=True)
    run = sub.add_parser('run')
    run.add_argument('--manifest', required=True)
    run.add_argument('--layouts', required=True)
    run.add_argument('--art', required=True)
    run.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'build':
        result = build_manifest(args.labels, args.queue, args.evidence_root, set(args.test_source))
    else:
        result = run_benchmark(json.loads(Path(args.manifest).read_text(encoding='utf-8')), args.layouts, args.art)
        result['manifest_sha256'] = sha256(args.manifest)
    write_json(Path(args.output), result)
    print(json.dumps(result.get('summary', {'offers': len(result['records'])}), indent=2))
