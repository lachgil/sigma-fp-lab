#!/usr/bin/env python3
"""Read the GUI append probe after a cold boot, over the USB shell.

    ./tools/uiprobe_read.py

Boot a card built with `build_combined_card.py --debug --ui-probe`, let the
camera settle in live view, then run this. It answers one question:

    does an AutoRun run before the startup pass that builds the GUI value
    lists?

A non-zero count means yes, and a native third resolution entry is reachable by
appending during that pass. Zero means AutoRun is too late and the search has to
move earlier. Either answer is worth having; nobody has measured it.

The probe only counts and records names. It changes nothing.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE_STATE = 0xC0732300
PROBE_SITE = 0xC05E2CA8


def word(addr):
    out = subprocess.run(['./host/fpsh', 'mem', 'get', f'{addr:#x},,4'],
                         capture_output=True, text=True, cwd=ROOT).stdout
    hit = re.search(r'D:0x([0-9A-Fa-f]+)', out)
    if not hit:
        raise SystemExit(f'shell read failed: {out.strip() or "no reply"}')
    return int(hit.group(1), 16)


def string_at(addr, limit=32):
    """Pull a short ASCII string out of the camera, one word at a time."""
    out = b''
    while len(out) < limit:
        raw = word(addr + len(out)).to_bytes(4, 'little')
        out += raw
        if b'\0' in raw:
            break
    return out.split(b'\0')[0].decode('ascii', 'replace')


def main():
    hook = word(PROBE_SITE)
    if (hook & 0xF800) != 0xF000:
        print(f'hook word at {PROBE_SITE:#x} is {hook:#010x}, not a B.W')
        print('the probe is not armed: is this the --ui-probe card, cold booted?')
        return 2

    count = word(PROBE_STATE)
    print(f'hook   : armed ({hook:#010x})')
    print(f'appends: {count}')
    if not count:
        print('\nZERO. The AutoRun runs after the resource pass, so appending an')
        print('entry during construction is not reachable from a card. The route')
        print('to a native resolution entry needs an earlier mount point.')
        return 1

    print('\nHIT. AutoRun runs before the value lists are built, so a native')
    print('entry is reachable. Names of the first appends:')
    for i in range(min(count, 8)):
        ptr = word(PROBE_STATE + 4 + i * 4)
        if 0xC0000000 <= ptr < 0xC4000000:
            print(f'  {i}: {ptr:#010x}  {string_at(ptr)}')
        else:
            print(f'  {i}: {ptr:#010x}  (not a string pointer)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
