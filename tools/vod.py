"""Download selected VOD windows and extract reviewable dungeon observations."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sfld_vod.media import acquire, load_sources, probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "fetch"):
        child = commands.add_parser(name)
        child.add_argument("--sources", type=Path, default=Path("examples/twitch-vods.json"))
        child.add_argument("--directory", type=Path, default=Path("out/vods"))
        child.add_argument("--only", help="One source ID from the manifest")
    fetch = commands.choices["fetch"]
    fetch.add_argument("--height", type=int, choices=(160,360,480,720,1080), default=360)
    fetch.add_argument("--chunk-seconds", type=int, default=600)
    fetch.add_argument("--workers", type=int, default=2)
    fetch.add_argument("--pilot-seconds", type=int)
    calibration = commands.add_parser("calibrate")
    calibration.add_argument("--video", type=Path, required=True)
    calibration.add_argument("--output", type=Path, required=True)
    calibration.add_argument("--crop", type=float, nargs=4, required=True)
    calibration.add_argument("--at", type=float, required=True)
    calibration.add_argument("--source-id", required=True)
    calibration.add_argument("--source-start", type=float, default=0)
    anchor=commands.add_parser('add-anchor',help='Add a visually verified alternative HUD appearance')
    anchor.add_argument('--layout',type=Path,required=True)
    anchor.add_argument('--image',type=Path,required=True,help='Full-stream audit image; do not use a cropped candidate image')
    scan = commands.add_parser("scan")
    scan.add_argument("--video", type=Path, required=True)
    scan.add_argument("--layout", type=Path, required=True)
    scan.add_argument("--output", type=Path, required=True)
    scan.add_argument("--art", type=Path, default=Path("out/vod-art"))
    scan.add_argument("--source-start", type=float, default=0)
    scan.add_argument("--source-url")
    scan.add_argument("--fps", type=float, default=8)
    gate = commands.add_parser("gate-batch")
    gate.add_argument("--directory", type=Path, default=Path("out/vods"))
    gate.add_argument("--workers", type=int, default=2)
    gate.add_argument("--sources", type=Path, default=Path("examples/twitch-vods.json"))
    batch=commands.add_parser('scan-batch')
    batch.add_argument('--directory',type=Path,default=Path('out/vods'))
    batch.add_argument('--art',type=Path,default=Path('out/vod-art'))
    batch.add_argument('--workers',type=int,default=2)
    batch.add_argument('--max-clips-per-source',type=int,default=1,help='0 explicitly scans all downloaded clips')
    batch.add_argument('--fps',type=float,default=8)
    audit=commands.add_parser('audit')
    audit.add_argument('--directory',type=Path,default=Path('out/vods'))
    audit.add_argument('--samples',type=int,default=8,help='Per source, per retained/rejected stratum')
    audit.add_argument('--seed',type=int,default=42)
    review = commands.add_parser("review")
    review.add_argument("--directory", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("--directory", type=Path, required=True)
    report.add_argument("--reviews", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    scenes = commands.add_parser('scenes', help='Scan whole clips using reviewed scene references; no HUD pruning')
    scenes.add_argument('--video', type=Path, required=True)
    scenes.add_argument('--layout', type=Path, required=True)
    scenes.add_argument('--config', type=Path, required=True)
    scenes.add_argument('--output', type=Path, required=True)
    scenes.add_argument('--source-start', type=float, default=0)
    scenes.add_argument('--source-url')
    scenes.add_argument('--fps', type=float, default=8)
    scenes.add_argument('--gap-frames', type=int, default=2)
    timeline = commands.add_parser('timeline', help='Build a source sequence review page with video and adjacent frames')
    timeline.add_argument('--directory', type=Path, default=Path('out/vods'))
    timeline.add_argument('--source-id', required=True)
    timeline.add_argument('--scenes', type=Path, nargs='*', default=[])
    timeline.add_argument('--candidates', type=Path, nargs='*', default=[])
    timeline.add_argument('--observations', type=Path)
    temporal_report = commands.add_parser('observations-report', help='Count field-verified, deduplicated observations')
    temporal_report.add_argument('--observations', type=Path, required=True)
    temporal_report.add_argument('--output', type=Path, required=True)
    serving = commands.add_parser('serve', help='Serve local review evidence with seekable video')
    serving.add_argument('--directory', type=Path, default=Path('out/vods'))
    serving.add_argument('--port', type=int, default=8789)
    catalog = commands.add_parser('catalog', help='Build all source review pages from an explicit scan manifest')
    catalog.add_argument('--directory', type=Path, default=Path('out/vods'))
    catalog.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'catalog':
        from sfld_vod.catalog import build_catalog
        result = build_catalog(args.directory, args.plan)
        print(f"{len(result['sources'])} sources, {result['total_candidates']} candidates: {args.directory/'review-all.html'}")
        return
    if args.command == 'serve':
        from sfld_vod.serve import serve
        serve(args.directory, args.port)
        return
    if args.command == 'scenes':
        from sfld_vod.scenes import scan_scenes
        result = scan_scenes(args.video, args.layout, args.config, args.output,
                             args.source_start, args.source_url, args.fps, args.gap_frames)
        print(f"{len(result['epochs'])} unreviewed scene candidates; {result['decoded_frames']} decoded samples.")
        return
    if args.command == 'timeline':
        from sfld_vod.temporal import build_timeline
        result = build_timeline(args.directory, args.source_id, args.scenes, args.candidates, args.observations)
        print(f"{len(result['candidates'])} candidates: {args.directory/(args.source_id+'-review.html')}")
        return
    if args.command == 'observations-report':
        from sfld_vod.temporal import observation_counts
        from sfld_vod.review import read_jsonl
        from sfld_vod.media import write_json
        result = observation_counts(read_jsonl(args.observations))
        write_json(args.output, result)
        print(f"Saved field-verified counts to {args.output}")
        return
    if args.command=='add-anchor':
        from sfld_vod.vision import add_anchor
        settings=add_anchor(args.layout,args.image)
        print(f"Saved {1+len(settings.get('additional_anchors',[]))} HUD references.")
        return
    if args.command=='audit':
        from sfld_vod.audit import build_audit
        result=build_audit(args.directory,args.samples,args.seed)
        print(f"Saved {len(result['samples'])} audit frames to {args.directory/'audit.html'}")
        return
    if args.command=='scan-batch':
        from sfld_vod.batch import scan_batch
        result=scan_batch(args.directory,args.art,args.workers,args.max_clips_per_source,args.fps)
        print(f"{sum(r['candidates'] for r in result['runs'])} review candidates; {len(result['failures'])} failures.")
        if result['failures']:
            raise RuntimeError('Some detailed scans failed; inspect candidates-index.json.')
        return
    if args.command == "gate-batch":
        from sfld_vod.batch import gate_batch
        result=gate_batch(args.directory,args.workers,load_sources(args.sources))
        print(f"Screened {len(result['sources'])} sources; timeline: {args.directory/'screening.html'}")
        return
    if args.command == "review":
        from sfld_vod.review import make_review
        print(make_review(args.directory))
        return
    if args.command == "report":
        from sfld_vod.review import read_jsonl,reviewed_counts
        from sfld_vod.media import write_json
        result=reviewed_counts(read_jsonl(args.directory/'candidates.jsonl'),read_jsonl(args.reviews))
        write_json(args.output,result);print(f"Saved reviewed counts to {args.output}")
        return
    if args.command == "calibrate":
        from sfld_vod.vision import calibrate
        calibrate(args.video,args.output,args.crop,args.at,args.source_id,args.source_start)
        print(f"Saved layout and calibration image to {args.output.parent}")
        return
    if args.command == "scan":
        from sfld_vod.vision import scan
        result = scan(args.video,args.layout,args.output,args.art,args.source_start,args.source_url,args.fps)
        print(f"{result['candidates']} review candidates in {result['processing_seconds']:.1f}s; "
              f"{result['template_classifications']} classifications / {result['sampled_frames']} sampled frames.")
        return
    sources = load_sources(args.sources)
    if args.only:
        sources = [s for s in sources if s["id"] == args.only]
        if not sources:
            parser.error("Unknown source ID")
    if args.command == "probe":
        for source in sources:
            info = probe(source, args.directory, refresh=True)
            print(f"{source['id']}: {info['duration']/3600:.2f} hours; {info['upload_date']}; {info['uploader']}")
    else:
        acquire(sources, args.directory, args.height, args.chunk_seconds, args.workers, args.pilot_seconds)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
