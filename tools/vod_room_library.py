"""Apply the shared counter pipeline to every canonical downloaded VOD root."""
import argparse
from pathlib import Path
from vod_room_sequence import inventory_jobs,execute_jobs,build

ROOT=Path(__file__).resolve().parents[1]
LIBRARY_ROOTS=['out/vods','out/vod-discovery','out/vod-hunt-2026-10-12','out/vod-older-2026-10-12']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['scan','build'])
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--fps',type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.workers<=16 or not 1<=args.fps<=16:raise ValueError('Use 1–16 workers and fps.')
    if args.command=='scan':
        jobs=[j for r in LIBRARY_ROOTS for j in inventory_jobs(ROOT/r,fps=args.fps)]
        print(f'{len(jobs)} nonoverlapping per-view work units; duplicated acquisition pilots excluded.',flush=True)
        execute_jobs(jobs,args.workers)
    else:
        for root in LIBRARY_ROOTS:build(ROOT/root)


if __name__=='__main__':main()
