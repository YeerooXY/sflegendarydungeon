"""Loopback-only static evidence server with bounded single-range video reads."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re


class EvidenceHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.remaining = None
        raw_range = self.headers.get('Range')
        if not raw_range:
            return super().send_head()
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', raw_range)
        if not match or not any(match.groups()):
            self.send_error(416, 'Expected one byte range')
            return None
        first, last = match.groups()
        if first:
            start = int(first); end = min(size-1, int(last) if last else size-1)
        else:
            start = max(0, size-int(last)); end = size-1
        if start > end or start >= size:
            self.send_response(416); self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0'); self.end_headers(); return None
        file = path.open('rb'); file.seek(start)
        self.remaining = end-start+1
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(self.remaining))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        return file

    def copyfile(self, source, outputfile):
        try:
            if self.remaining is None:
                return super().copyfile(source, outputfile)
            remaining = self.remaining
            while remaining:
                block = source.read(min(remaining, 256*1024))
                if not block:
                    break
                outputfile.write(block); remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Browsers cancel byte ranges when seeking to another scene.


def serve(directory, port=8789):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('Evidence directory does not exist.')
    server = ThreadingHTTPServer(('127.0.0.1', port), partial(EvidenceHandler, directory=str(root)))
    print(f'Evidence review: http://127.0.0.1:{port}/review-all.html', flush=True)
    server.serve_forever()
