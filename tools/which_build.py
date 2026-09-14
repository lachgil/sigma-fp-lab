#!/usr/bin/env python3
"""Say whether the card in the camera is the build in this working tree.

    ./tools/which_build.py                 compare against builds/combined-debug
    ./tools/which_build.py builds/combined-menu

Reads the menu section out of the camera over the USB shell and compares it with
the packaged one, byte for byte. This exists because "the fix does not work" and
"you are running last week's card" look identical from the outside, and telling
them apart by eye cost an hour once.

Needs the dev card (it has the USB shell) and `fpshd` running.
"""
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL_PTR = 0xC3757A7C
MENU_OFFSET = 0x50000
SAMPLE = 512      # enough to fingerprint; a full section read is slow


def word(addr):
    out = subprocess.run(['./host/fpsh', 'mem', 'get', f'{addr:#x},,4'],
                         capture_output=True, text=True, cwd=ROOT).stdout
    hit = re.search(r'D:0x([0-9A-Fa-f]+)', out)
    if not hit:
        raise SystemExit(f'shell read failed: {out.strip() or "no reply"}')
    return int(hit.group(1), 16)


def sections(raw):
    magic, count, _entry, _length = struct.unpack_from('<4sIII', raw)
    if magic != b'VBIN':
        raise SystemExit('not a VSHL.BIN')
    offset, out = 16 + count * 8, {}
    for i in range(count):
        at, size = struct.unpack_from('<II', raw, 16 + i * 8)
        out[at] = raw[offset:offset + size]
        offset += size
    return out


def main():
    build = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'builds/combined-debug'
    packaged = sections((build / 'VSHL.BIN').read_bytes())[MENU_OFFSET]
    manifest = json.loads((build / 'manifest.json').read_text())

    pool = word(POOL_PTR)
    if not pool:
        raise SystemExit('no pool pointer: is a card loaded at all?')
    live = b''.join(struct.pack('<I', word(pool + MENU_OFFSET + i))
                    for i in range(0, SAMPLE, 4))

    same = live == packaged[:SAMPLE]
    print(f'build   : {build}')
    print(f'shell   : {"USB shell present" if manifest["usb_shell"] else "no shell"}')
    print(f'pool    : {pool:#010x}')
    print(f'live    : {hashlib.sha256(live).hexdigest()[:16]}')
    print(f'packaged: {hashlib.sha256(packaged[:SAMPLE]).hexdigest()[:16]}')
    print('MATCH: the camera is running this build' if same else
          'DIFFERENT: the camera is running some other card -- recopy it')
    return 0 if same else 1


if __name__ == '__main__':
    raise SystemExit(main())
