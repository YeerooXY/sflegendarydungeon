"""Run conservative native trap recognition on frames of one offer."""
import sys
from sfld_vod.trap_detector import main

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'benchmark':
        from sfld_vod.trap_benchmark import main as benchmark_main
        raise SystemExit(benchmark_main(sys.argv[2:]))
    raise SystemExit(main())
