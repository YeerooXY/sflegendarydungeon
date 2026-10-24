"""Where tests and tools find the compiled engine.

SFLD_TEST_BUILD (a build folder, relative to the repository or absolute) wins.
Otherwise the caller's preferred local builds are tried in order (several
checks were written against a specific engine build), then the standard CMake
folder: build/Release on Windows, build elsewhere. Callers skip when the file
they need is absent.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = 'sfld.exe' if os.name == 'nt' else 'sfld'
LIBRARY = 'sfld_rl.dll' if os.name == 'nt' else 'libsfld_rl.so'
MISSING = 'Engine not built (cmake --build build --config Release, or set SFLD_TEST_BUILD)'


def _candidates(folder):
    folder = ROOT/folder
    return (folder/'Release', folder)


def build_dir(*preferred):
    if os.environ.get('SFLD_TEST_BUILD'):
        folders = _candidates(os.environ['SFLD_TEST_BUILD'])
        return next((f for f in folders if (f/LIBRARY).is_file() or (f/EXE).is_file()), folders[0])
    for name in (*preferred, 'build'):
        for folder in _candidates(name):
            if (folder/LIBRARY).is_file() or (folder/EXE).is_file():
                return folder
    return _candidates('build')[0 if os.name == 'nt' else 1]


def library(*preferred):
    return build_dir(*preferred)/LIBRARY


def engine(*preferred):
    return build_dir(*preferred)/EXE
