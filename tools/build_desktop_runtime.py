"""Assemble the self-contained runtime folder the desktop installer ships.

Layout mirrors the repository, so every repository-relative path keeps working:
  python/          embeddable CPython with NumPy only (no PyTorch)
  tools/ web/ profiles/ models/
  build/Release/   sfld.exe, sfld_rl.dll and the app-local MSVC runtime
Game art (web/local-assets) is the game developer's property and is not included;
Practice falls back to plain graphics. Downloads (python.org embeddable zip, the
NumPy wheel via pip) are cached in out/desktop-build-cache.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = '3.13.15'
NUMPY = '2.5.3'
CRT = ('msvcp140.dll', 'msvcp140_atomic_wait.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
REDIST = Path(r'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Redist\MSVC')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(url, target):
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response, open(target.with_suffix('.part'), 'wb') as out:
            shutil.copyfileobj(response, out)
        target.with_suffix('.part').replace(target)
    return target


def copy_tree(source, target, suffixes):
    for path in source.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix.lower() in suffixes:
            out = target/path.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output', type=Path, default=ROOT/'out/desktop-runtime')
    p.add_argument('--engine', type=Path, default=ROOT/'build-ui-decisions/Release', help='folder with sfld.exe and sfld_rl.dll')
    a = p.parse_args()
    cache = ROOT/'out/desktop-build-cache'
    if a.output.exists():
        shutil.rmtree(a.output)
    out = a.output;out.mkdir(parents=True)

    # Python: embeddable zip, NumPy unpacked into Lib/site-packages, tools on the path.
    embed = fetch(f'https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip',
                  cache/f'python-{PYTHON}-embed-amd64.zip')
    with zipfile.ZipFile(embed) as z:
        z.extractall(out/'python')
    wheels = cache/'wheels'
    if not list(wheels.glob(f'numpy-{NUMPY}-*.whl')):
        subprocess.run([sys.executable, '-m', 'pip', 'download', f'numpy=={NUMPY}', '--only-binary=:all:',
                        '--platform', 'win_amd64', '--python-version', PYTHON.rsplit('.', 1)[0], '--implementation', 'cp',
                        '--no-deps', '-d', str(wheels)], check=True)
    wheel = next(wheels.glob(f'numpy-{NUMPY}-*.whl'))
    with zipfile.ZipFile(wheel) as z:
        z.extractall(out/'python/Lib/site-packages')
    tag = ''.join(PYTHON.split('.')[:2])
    (out/f'python/python{tag}._pth').write_text(
        f'python{tag}.zip\n.\nLib\\site-packages\n..\\tools\n', encoding='ascii')

    copy_tree(ROOT/'tools', out/'tools', {'.py', '.json'})
    for name in ('index.html', 'app.js', 'style.css', 'game.css', 'play-shell.css', 'presentation.js',
                 'rule-guide.html', 'rule-guide.js', 'rule-guide.css', 'rule-guide-data.json'):
        (out/'web').mkdir(exist_ok=True);shutil.copy2(ROOT/'web'/name, out/'web'/name)
    copy_tree(ROOT/'profiles', out/'profiles', {'.profile'})
    copy_tree(ROOT/'examples', out/'examples', {'.state'})  # the app's smoke check loads one
    copy_tree(ROOT/'models', out/'models', {'.npz', '.pt', '.json'})

    engine = out/'build/Release';engine.mkdir(parents=True)
    for name in ('sfld.exe', 'sfld_rl.dll'):
        shutil.copy2(a.engine/name, engine/name)
    crt = next(REDIST.glob('*/x64/Microsoft.VC*.CRT'))
    for name in CRT:
        shutil.copy2(crt/name, engine/name)

    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    print(f'runtime: {out} ({size/1e6:.0f} MB)')
    print(f'python embed sha256 {sha(embed)}')
    print(f'numpy wheel {wheel.name} sha256 {sha(wheel)}')


if __name__ == '__main__':
    main()
