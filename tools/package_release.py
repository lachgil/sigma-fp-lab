#!/usr/bin/env python3
"""Build both cards and package each as a zip that extracts onto the card root.

    ./tools/package_release.py            builds both, writes cards/*.zip

Each zip holds the four files a card should have:

    AutoRun.txt   the loader
    VSHL.BIN      the menu code -- BUILD SPECIFIC, never mix it between builds
    manifest.json what went into this build, so a card can be identified later
    README.txt    the same text `publish_card.sh` writes onto a real card

The two builds are the public card and the development card (USB shell plus the
SEL row's cell counts). They deliberately share filenames inside their own zip,
which is why they are packaged separately rather than as loose files: a public
AutoRun.txt beside a dev VSHL.BIN is a card that loads the wrong code.
"""
import hashlib
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / 'cards/card-readme.txt'
BUILDS = (
    ('fp-menu-card', 'builds/combined-menu', [], 'Public card', 'fpLAB MENU'),
    ('fp-menu-card-dev', 'builds/combined-debug', ['--debug'],
     'Development card (USB shell)', 'fpLAB DEBUG'),
)


def render(out_dir, label, banner):
    text = TEMPLATE.read_text()
    for token, value in (
            ('@BUILD@', label),
            ('@DATE@', time.strftime('%Y-%m-%d %H:%M')),
            ('@BANNER@', banner),
            ('@AUTORUN_SHA@', sha(out_dir / 'AutoRun.txt')),
            ('@VSHL_SHA@', sha(out_dir / 'VSHL.BIN'))):
        text = text.replace(token, value)
    if '@' in text.replace('@ ', ''):
        leftover = [w for w in text.split() if w.startswith('@') and w.endswith('@')]
        if leftover:
            raise SystemExit(f'unreplaced token(s) in the template: {leftover}')
    return text


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for name, out, extra, label, banner in BUILDS:
        out_dir = ROOT / out
        subprocess.run([sys.executable, str(ROOT / 'tools/build_combined_card.py'),
                        '--out', str(out_dir)] + extra,
                       check=True, stdout=subprocess.DEVNULL)
        (out_dir / 'README.txt').write_text(render(out_dir, label, banner))
        target = ROOT / 'cards' / f'{name}.zip'
        # Deterministic: a fixed timestamp, so rebuilding an unchanged card does
        # not produce a different zip and a pointless "new release".
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
            for member in ('AutoRun.txt', 'VSHL.BIN', 'manifest.json', 'README.txt'):
                info = zipfile.ZipInfo(member, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, (out_dir / member).read_bytes())
        print(f'{target.relative_to(ROOT)}  '
              f'AutoRun {sha(out_dir / "AutoRun.txt")[:16]}  '
              f'VSHL {sha(out_dir / "VSHL.BIN")[:16]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
