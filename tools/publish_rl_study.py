"""Copy the reviewable RL evidence and selected checkpoints into a new directory.

Large training logs and superseded checkpoints stay in the working study. The
published source snapshot records the training inputs; it is not a full repo.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from render_rl_study import build_report, render


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, destination = args.study.resolve(strict=True), args.output.resolve()
    if destination.exists() or destination == source or source in destination.parents:
        raise ValueError('Publication must be a new directory outside the working study')
    # Validate complete final evidence before making the publication directory.
    for filename in ('common-validation.json', 'final-learner.json', 'final-teacher.json'):
        if not (source / filename).is_file():
            raise ValueError(f'Missing final evidence: {filename}')
    build_report(source, source / 'publication-check.html')
    plan = json.loads((source / 'study-plan.json').read_text(encoding='utf-8'))
    paths = set()
    for pattern in ('study-plan.json', 'source-manifest.json', 'verification.json', 'native-throughput.json',
                    'candidate-selections.json', 'common-validation*.json', 'final-*.json',
                    'behavior-audit.json', 'behavior-samples.jsonl', 'paired-results.json',
                    'selected-checkpoint.pt', 'sfld_rl.dll',
                    'run_study.py', 'evaluate_study.py', '*-status.json'):
        paths.update(p for p in source.glob(pattern) if p.is_file())
    for job in plan['jobs']:
        directory = source / job['name']
        if directory.resolve().parent != source:
            raise ValueError('Run name escapes study directory')
        for name in ('run.json', 'curve.jsonl', 'training-summary.json', 'best.json', 'latest.json'):
            paths.add(directory / name)
        for name in ('best.json', 'latest.json'):
            pointer = json.loads((directory / name).read_text(encoding='utf-8'))
            checkpoint = (directory / pointer['checkpoint']).resolve(strict=True)
            if checkpoint.parent != directory.resolve() or digest(checkpoint) != pointer['sha256']:
                raise ValueError('Checkpoint pointer or hash mismatch')
            paths.add(checkpoint)
    for folder in ('source', 'distillation'):
        directory = source / folder
        if directory.exists():
            paths.update(p for p in directory.rglob('*') if p.is_file() and p.suffix != '.npz')
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for path in sorted(paths):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        expected = digest(path)
        if digest(target) != expected:
            raise RuntimeError(f'Copy hash mismatch: {relative}')
        manifest[relative.as_posix()] = expected
    report = destination / 'report.html'
    report.write_text(render(build_report(destination, report)), encoding='utf-8')
    manifest['report.html'] = digest(report)
    (destination / 'publication-manifest.json').write_text(json.dumps({
        'schema': 1, 'files': manifest,
        'note': 'Source snapshot is the frozen training dependency set, not a complete repository checkout.'
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Published {len(manifest)} verified files to {destination}')


if __name__ == '__main__':
    main()
