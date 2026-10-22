"""Private loopback practice server owned by the desktop application's stdin."""
import json
import os
import sys
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from visual_lab import Lab, REPO, handler_for, default_engine


def main():
    # Initialize native numerical libraries before blocking on the Windows
    # control pipe. Their DLL initialization may inspect the standard handles.
    from sfld_rl.visual_policy import warmup
    warmup()
    # Optional per-launch calibration; regular desktop starts keep their defaults.
    engine = Path(os.environ['SFLD_PRACTICE_EXE']) if os.environ.get('SFLD_PRACTICE_EXE') else default_engine()
    profile = Path(os.environ.get('SFLD_PRACTICE_PROFILE') or REPO / 'profiles/synthetic.profile')
    lab = Lab(engine, profile)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(lab))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(json.dumps({'ready': True, 'origin': f'http://127.0.0.1:{server.server_port}'}), flush=True)
    try:
        # A blocking TextIOWrapper read holds stdin's lock. Torch imports can
        # inspect that stream from an HTTP worker and deadlock on Windows.
        # Read the owned pipe directly; EOF still closes every engine process.
        pending = b''
        while True:
            chunk = os.read(sys.stdin.fileno(), 1024)
            if not chunk:
                break
            pending += chunk
            if b'stop\n' in pending or len(pending) > 4096:
                break
    finally:
        server.shutdown()
        server.server_close()
        lab.close()
        worker.join(timeout=2)


if __name__ == '__main__':
    main()
