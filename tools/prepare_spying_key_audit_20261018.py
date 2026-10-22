"""Prepare a frozen Spying Gem cohort without classifying key outcomes.

Reuses the native-frame extractor from the baseline audit, in its own namespace.
The eligibility filter is replaced before extraction; original files stay intact.
"""
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    args = parser.parse_args()
    source = (ROOT / 'tools/prepare_key_extension_20261017.py').read_text(encoding='utf-8')
    source = source.replace("OUT = ROOT/'out/key-drop-extension-2026-10-17'",
                            "OUT = ROOT/'out/spying-key-audit-2026-10-18'")
    old = "elif set(row['gems']) & {'spying','lodestone'}: reason='key_modifying_stone_locator'"
    new = ("elif 'spying' not in (row['gems'] or []): reason='spying_not_active_locator'\n"
           "        elif 'lodestone' in row['gems']: reason='lodestone_confounded_locator'")
    assert old in source
    source = source.replace(old, new)
    namespace = {'__file__': str(ROOT / 'tools/prepare_key_extension_20261017.py'),
                 '__name__': 'spying_key_extractor'}
    exec(compile(source, 'spying_key_extractor', 'exec'), namespace)
    namespace['prepare'](args.source)


if __name__ == '__main__':
    main()
