"""Publish a complete fine-tuning comparison with hashed models and evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from render_rl_finetune import build_report, render


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, destination = args.study.resolve(strict=True), args.output.resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError('Publication must be a new directory outside the working study')
    build_report(source, source / 'publication-check.html')
    files = {p for p in source.glob('*.json') if p.is_file()}
    files.update(source / name for name in ('parent-checkpoint.pt', 'selected-checkpoint.pt', 'sfld_rl.dll'))
    for job in read(source / 'training-totals.json')['jobs']:
        directory = (source / job['name']).resolve(strict=True)
        if directory.parent != source:
            raise ValueError('Training path escapes study')
        files.update(directory / name for name in ('run.json', 'curve.jsonl', 'best.json', 'latest.json', 'training-summary.json'))
        for pointer in ('best.json', 'latest.json'):
            metadata = read(directory / pointer)
            checkpoint = (directory / metadata['checkpoint']).resolve(strict=True)
            if checkpoint.parent != directory or digest(checkpoint) != metadata['sha256']:
                raise ValueError('Invalid checkpoint path or hash')
            files.add(checkpoint)
    for folder in ('source', 'orchestrator-revisions', 'diagnostics'):
        files.update(p for p in (source / folder).rglob('*') if p.is_file())
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for path in sorted(files):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        checksum = digest(path)
        if digest(target) != checksum:
            raise RuntimeError(f'Copy hash mismatch: {relative}')
        manifest[relative.as_posix()] = checksum
    root = Path(__file__).resolve().parents[1]
    support = ['tools/render_rl_finetune.py', 'tools/render_rl_study.py', 'tools/publish_rl_finetune.py',
               'tests/test_rl_finetune_report.py', 'tests/test_rl_finetune_study.py', 'tests/test_rl_training.py']
    for relative in support:
        target = destination / 'supporting-sources' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        checksum = digest(root / relative)
        if digest(target) != checksum:
            raise RuntimeError(f'Supporting source copy hash mismatch: {relative}')
        manifest[target.relative_to(destination).as_posix()] = checksum
    report = destination / 'report.html'
    report.write_text(render(build_report(destination, report)), encoding='utf-8')
    manifest['report.html'] = digest(report)
    (destination / 'publication-manifest.json').write_text(json.dumps({
        'schema': 1, 'files': manifest,
        'note': 'Training dependency snapshot, not a complete repository. Any orchestration correction is recorded separately; native rules and trained model inputs remain frozen.'
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Published {len(manifest)} verified files to {destination}')


if __name__ == '__main__':
    main()
