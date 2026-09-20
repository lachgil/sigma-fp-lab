#!/usr/bin/env python3
"""Build a single AutoRun: False Color / EL Zone toggle, plus the stop scale.

One file, and nothing in it but this. No menu, no key hook, no panel, no gyro,
no USB shell, no recording changes. The only button involved is the one the
camera's own Custom Button Functions menu already points at False Color.

    press 1   mode on, and it stays on
    press 2   the scale appears across the bottom
    press 3   both off

    ./tools/build_fcscale_autorun.py [--out DIR]

RAM only. Remove AutoRun.txt and cold boot to revert.
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

# Free worker-region caves, checked zero in the stock image at build time. The
# state block runs to +0x180: the thread and body objects live at +0x80.
STATE = 0xC072E200
CODE = 0xC072E400

PRESS_SITE = 0xC03722E8         # CameraIF vtable +0xCC
RELEASE_SITE = 0xC0372330       # vtable +0xD0
SITE_STOCK = 0xE92D4010         # push {r4, lr}, in both


# How an AutoRun runs code: the echo command's handler pointer is repointed,
# `echo` is issued, and the stock handler is put straight back. Same slot the
# fpSup loader itself uses.
ECHO_SLOT = 0xC0BAC2F8
ECHO_ORIG = 0xC03D99A0
CACHE_FN = 0xC000E91C

def word(image: bytes, address: int) -> int:
    return struct.unpack_from('<I', image, address - BASE)[0]


def branch(site: int, target: int) -> int:
    offset = target - site - 8
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise SystemExit('branch out of range')
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


def build(out: Path, swatch: bool = False, capture: bool = False) -> str:
    image = MAIN.read_bytes()
    if hashlib.sha256(image).hexdigest() != MAIN_SHA:
        raise SystemExit('requires the verified SIGMA fp Ver.5.02 MAIN image')
    for site, why in ((PRESS_SITE, 'press'), (RELEASE_SITE, 'release')):
        got = word(image, site)
        if got != SITE_STOCK:
            raise SystemExit(f'the {why} method at {site:#x} starts {got:#x}, '
                             f'expected {SITE_STOCK:#x}')
    defines = (f'STATE={STATE:#x}', f'SWATCH={1 if swatch else 0}')
    code = assemble(ROOT / 'src/fcscale.S', defines)
    marks = symbols(ROOT / 'src/fcscale.S', defines)
    if STATE + 0x180 > CODE:
        raise SystemExit('the state block runs into the code')
    for address, length, why in ((STATE, 0x180, 'state'), (CODE, len(code), 'code')):
        for at in range(address, address + length, 4):
            if word(image, at):
                raise SystemExit(f'the {why} cave at {at:#x} is not zero in stock')

    words = struct.unpack(f'<{len(code) // 4}I', code)
    press = CODE + marks['fc_press']
    release = CODE + marks['fc_release']
    spawn = CODE + marks['fc_spawn']
    probe = CODE + marks['fc_probe']
    body = CODE + marks['fc_body']
    lines = [
        '# ==========================================================',
        '# False Color / EL Zone as a TOGGLE, with the stop scale',
        '# SIGMA fp Ver.5.02 ONLY. RAM only: remove this file and cold',
        '# boot to revert. Nothing is written to flash.',
        '#',
        '# FIRST, in the camera:',
        '#   Menu -> Custom -> button settings -> False Color',
        '# (this changes what that button does; it does not create it)',
        '#',
        '# Then, on that button:',
        '#   press 1  False Color / EL Zone on, and it STAYS on',
        '#   press 2  the stop scale appears across the bottom',
        '#   press 3  both off',
        '#',
        '# EL Zone or False Color is your own False Color Style setting.',
        '# Nothing else is touched: no menu, no other buttons, no',
        '# recording changes, no USB shell.',
        '# ==========================================================',
        'display monitor 0 1',
        'display osd 1 0x00000000',
        'display text fpLAB FC',
        'display osd 1',
        '',
        f'# --- state @ 0x{STATE:08X} ---',
    ]
    lines += [f'mem set 0x{STATE + i * 4:08X} 0x00000000' for i in range(0x180 // 4)]
    lines.append(f'# --- code @ 0x{CODE:08X} ({len(words)} words) ---')
    lines += [f'mem set 0x{CODE + i * 4:08X} 0x{value:08X}'
              for i, value in enumerate(words)]
    lines += [
        '# --- the drawing thread object: +0x140 holds the object, whose vtable',
        '#     is the next word, and the body hangs off that vtable +0x0C ---',
        f'mem set 0x{STATE + 0x140:08X} 0x{STATE + 0x144:08X}',
        f'mem set 0x{STATE + 0x150:08X} 0x{body:08X}',
        '# --- arm both methods ---',
        f'mem set 0x{PRESS_SITE:08X} 0x{branch(PRESS_SITE, press):08X}',
        f'mem set 0x{RELEASE_SITE:08X} 0x{branch(RELEASE_SITE, release):08X}',
        '# --- freshly written code is only data to the caches, so flush it and',
        '#     then start the thread. Both are done by borrowing the echo',
        "#     command's handler slot and putting it straight back ---",
        f'mem set 0x{ECHO_SLOT:08X} 0x{CACHE_FN:08X}',
        'echo',
        f'mem set 0x{ECHO_SLOT:08X} 0x{spawn:08X}',
        'echo',
        f'mem set 0x{ECHO_SLOT:08X} 0x{ECHO_ORIG:08X}',
    ]
    if capture:
        # Put the chart up without touching a button, then ask the camera to
        # write its own screen to the card: the capture carries the actual
        # colour of every index, which a photograph cannot.
        lines += [
            f'mem set 0x{STATE:08X} 0x00000002',
            f'mem set 0x{STATE + 0xC:08X} 0x00000001',
            f'mem set 0x{ECHO_SLOT:08X} 0x{probe:08X}',
            'echo',
            'echo',
            f'mem set 0x{ECHO_SLOT:08X} 0x{ECHO_ORIG:08X}',
            'display capture \\OSDMAIN.XCI 1 0',
            'display capture \\OSDSUB.XCI 2 0',
            'display capture \\OSDBOTH.XCI 3 0',
        ]
    lines += [
        '',
        'display osd 1 0x00000000',
        'display text fpLAB FC ready',
        'display osd 1',
        '',
    ]
    text = '\n'.join(lines)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'AutoRun.txt').write_text(text)
    print(f'{out / "AutoRun.txt"}: {len(words)} words, {len(text)} bytes')
    print(f'  press   {PRESS_SITE:#010x} -> {press:#010x}')
    print(f'  release {RELEASE_SITE:#010x} -> {release:#010x}')
    print(f'  spawn   {spawn:#010x}   body {body:#010x}   state {STATE:#010x}')
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/fcscale')
    parser.add_argument('--swatch', action='store_true',
                        help='draw all 256 palette indices as a chart instead '
                             'of the scale, so the palette can be photographed')
    parser.add_argument('--capture', action='store_true',
                        help='draw the chart at boot and have the camera write '
                             'its own screen to the card, so the palette can be '
                             'read exactly')
    args = parser.parse_args()
    build(args.out, args.swatch, args.capture)


if __name__ == '__main__':
    main()
