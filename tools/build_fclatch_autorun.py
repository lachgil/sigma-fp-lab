#!/usr/bin/env python3
"""Build a standalone AutoRun that makes the mapped False Color button a toggle.

One thing only: no menu, no USB shell, no gyro, no recording-mode patch. It
writes src/fclatch.S into a cave and repoints the two CameraIF methods that the
camera's own function key calls.

    press   0xC03722E8 -> our handler: flip a flag, post 0x21 or 0x22
    release 0xC0372330 -> our handler: return without posting

Map a button to False Color in the camera's own Custom Button Functions menu
first; this does not create a mapping, it changes what an existing one does.
The camera's False Color Style decides whether that is False Color or EL Zone.

RAM only. Remove AutoRun.txt and cold boot to revert.

    ./tools/build_fclatch_autorun.py [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'reference/fpSup/fp_usb_shell')]
from armasm import assemble, symbols

BASE = 0xC0000000
MAIN = ROOT / 'analysis/MAIN_c0000000.bin'
MAIN_SHA = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'

# Free worker-region caves, checked zero in the stock image at build time.
STATE = 0xC072E400          # latch, presses, releases swallowed
CODE = 0xC072E440           # the handlers

PRESS_SITE = 0xC03722E8     # CameraIF vtable +0xCC
PRESS_STOCK = 0xE92D4010    # push {r4, lr}
RELEASE_SITE = 0xC0372330   # CameraIF vtable +0xD0
RELEASE_STOCK = 0xE92D4010  # push {r4, lr}


def word(image: bytes, address: int) -> int:
    return struct.unpack_from('<I', image, address - BASE)[0]


def branch(site: int, target: int) -> int:
    offset = target - site - 8
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise SystemExit('branch out of range')
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


def build(out: Path) -> str:
    image = MAIN.read_bytes()
    if hashlib.sha256(image).hexdigest() != MAIN_SHA:
        raise SystemExit('requires the verified SIGMA fp Ver.5.02 MAIN image')
    for site, stock, why in ((PRESS_SITE, PRESS_STOCK, 'press'),
                             (RELEASE_SITE, RELEASE_STOCK, 'release')):
        got = word(image, site)
        if got != stock:
            raise SystemExit(f'{why} method at {site:#x} starts {got:#x}, '
                             f'expected {stock:#x}')
    code = assemble(ROOT / 'src/fclatch.S', (f'STATE={STATE:#x}',))
    marks = symbols(ROOT / 'src/fclatch.S', (f'STATE={STATE:#x}',))
    for address, length, why in ((STATE, 0x10, 'state'), (CODE, len(code), 'code')):
        for at in range(address, address + length, 4):
            if word(image, at):
                raise SystemExit(f'{why} cave at {at:#x} is not zero in stock')
    if STATE + 0x10 > CODE:
        raise SystemExit('the state block runs into the code cave')

    words = struct.unpack(f'<{len(code) // 4}I', code)
    press = CODE + marks['fc_press']
    release = CODE + marks['fc_release']
    lines = [
        '# ==========================================================',
        '# fpLAB: False Color / EL Zone as a TOGGLE, on your own button',
        '# SIGMA fp Ver.5.02 ONLY. RAM only; remove this file and cold',
        '# boot to revert. Nothing is written to flash.',
        '#',
        '# Map a button to False Color first:',
        '#   Menu -> Custom -> button settings -> False Color',
        '# Then one press turns it on and it STAYS on; the next press',
        '# turns it off. Whether you see False Color or EL Zone is the',
        '# camera own False Color Style setting, unchanged by this.',
        '#',
        '# It replaces the two methods the button calls:',
        f'#   press   {PRESS_SITE:#010x} -> {press:#010x}',
        f'#   release {RELEASE_SITE:#010x} -> {release:#010x}',
        '# ==========================================================',
        'display monitor 0 1',
        'display osd 1 0x00000000',
        'display text fpLAB FC latch',
        'display osd 1',
        '',
        f'# --- state @ 0x{STATE:08X}: latch, presses, releases swallowed ---',
    ]
    lines += [f'mem set 0x{STATE + i * 4:08X} 0x00000000' for i in range(4)]
    lines.append(f'# --- handlers @ 0x{CODE:08X} ({len(words)} words) ---')
    lines += [f'mem set 0x{CODE + i * 4:08X} 0x{value:08X}'
              for i, value in enumerate(words)]
    lines += [
        '# --- arm both methods LAST ---',
        f'mem set 0x{PRESS_SITE:08X} 0x{branch(PRESS_SITE, press):08X}',
        f'mem set 0x{RELEASE_SITE:08X} 0x{branch(RELEASE_SITE, release):08X}',
        '',
        'display osd 1 0x00000000',
        'display text fpLAB FC latch ON',
        'display osd 1',
        '',
    ]
    text = '\n'.join(lines)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'AutoRun.txt').write_text(text)
    print(f'{out / "AutoRun.txt"}: {len(words)} words of handler, '
          f'{len(text)} bytes')
    print(f'  press   {PRESS_SITE:#010x} -> {press:#010x}')
    print(f'  release {RELEASE_SITE:#010x} -> {release:#010x}')
    print(f'  state   {STATE:#010x}  latch / presses / releases swallowed')
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/fclatch')
    build(parser.parse_args().out)


if __name__ == '__main__':
    main()
