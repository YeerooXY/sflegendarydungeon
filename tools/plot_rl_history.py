"""Standalone Matplotlib figures from the verified history-training archive.

Plotting uses a separate optional environment; it never changes training inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, required=True)
    args = p.parse_args()
    directory = args.study.resolve()
    summary = read(directory / 'summary.json')
    verification = read(directory / 'verification.json')
    if hashlib.sha256((directory / 'summary.json').read_bytes()).hexdigest() != verification['summary_sha256']:
        raise ValueError('Summary differs from the verified artifact')
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'savefig.facecolor': 'white', 'figure.facecolor': 'white'})
    rows = summary['global_comparisons']; x = np.arange(len(rows))
    old = [r['baseline']['completions']['mean'] for r in rows]
    new = [r['candidate_result']['completions']['mean'] for r in rows]
    old_error = [1.96 * r['baseline']['completions']['standard_error'] for r in rows]
    new_error = [1.96 * r['candidate_result']['completions']['standard_error'] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), layout='constrained')
    axes[0].bar(x - .19, old, .36, yerr=old_error, capsize=3, color='#8291a8', label='Previous policy')
    axes[0].bar(x + .19, new, .36, yerr=new_error, capsize=3, color='#167c80', label='New overall policy')
    axes[0].set_ylabel('Mean completions in seven days')
    axes[0].set_title('Fresh final events · 95% marginal intervals')
    axes[0].legend(frameon=False, fontsize=9)
    gain = [r['paired_improvement']['mean'] for r in rows]
    error = [2.575829 * r['paired_improvement']['standard_error'] for r in rows]
    axes[1].axhline(0, color='#8291a8', linewidth=1)
    axes[1].errorbar(x, gain, yerr=error, fmt='o', color='#167c80', capsize=5, markersize=7)
    axes[1].set_ylabel('Additional completions per week')
    axes[1].set_title('Matched policy difference · 99% intervals')
    for ax in axes:
        ax.set_xticks(x, [str(r['budget']) for r in rows]); ax.set_xlabel('Weekly mushroom cap')
        ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    fig.suptitle('More training with offered-stone history', fontweight='bold')
    for extension in ('png', 'svg', 'pdf'):
        fig.savefig(directory / ('final-comparison.' + extension), dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5), layout='constrained')
    colors = {'steady': '#167c80', 'long-credit': '#8762a6', 'exploration': '#c47829', 'extension': '#2458a3'}
    for job in summary['training']['jobs']:
        rows = [json.loads(line) for line in (directory / job['name'] / 'curve.jsonl').read_text().splitlines()]
        checks = [r for r in rows if r.get('validation_score') is not None]
        origin = checks[0]['validation_score']
        ax.plot([r['steps'] / 1e6 for r in checks], [r['validation_score'] - origin for r in checks],
                marker='o', markersize=3, label=job['name'], color=colors[job['name']])
    ax.axhline(0, color='#8291a8', linewidth=1)
    ax.set(xlabel='Additional training decisions in this branch (millions)',
           ylabel='Mean weekly clears minus this branch’s starting score',
           title='Internal validation during training')
    ax.grid(alpha=.2); ax.legend(frameon=False)
    fig.supxlabel('Each branch uses its own validation cohort; extension starts from the shared-validation winner.\nThese curves are selection data, not the final performance comparison.', fontsize=9)
    for extension in ('png', 'svg', 'pdf'):
        fig.savefig(directory / ('training-curves.' + extension), dpi=180)
    plt.close(fig)
    record = {'matplotlib': matplotlib.__version__, 'numpy': np.__version__,
              'plotter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'summary_sha256': verification['summary_sha256'],
              'figures': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()
                          if p.name.startswith(('final-comparison.', 'training-curves.'))}}
    (directory / 'figures.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'figures': list(record['figures'])}))


if __name__ == '__main__':
    main()
