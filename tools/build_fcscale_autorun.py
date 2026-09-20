#!/usr/bin/env python3
"""Build a single AutoRun: False Color / EL Zone toggle, plus the camera's own
EL Zone scale.

One file, and nothing in it but this. No menu, no key hook, no panel, no gyro,
no USB shell, no recording changes. The only button involved is the one the
camera's own Custom Button Functions menu already points at False Color.

    press 1   mode on, and it stays on
    press 2   the scale appears across the bottom
    press 3   both off

    ./tools/build_fcscale_autorun.py [--out DIR]

The scale's colours, band positions and label glyphs are read out of the
verified MAIN image here and written to src/fcscale_native.inc.S before the
assembly is built, so the card draws exactly what the firmware draws.

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
NATIVE_INC = ROOT / 'src/fcscale_native.inc.S'

# Free worker-region caves, checked zero in the stock image at build time.
STATE = 0xC072E200
STATE_SIZE = 0x90
CODE = 0xC072E400

PRESS_SITE = 0xC03722E8         # CameraIF vtable +0xCC
RELEASE_SITE = 0xC0372330       # vtable +0xD0
SITE_STOCK = 0xE92D4010         # push {r4, lr}, in both
SUBMIT_SITE = 0xC02E8A08        # display submit(controller, descriptor, sub)
SUBMIT_STOCK = 0xE92D48F0       # push {r4, r5, r6, r7, fp, lr}, replayed by fc_submit

# How an AutoRun runs code: the echo command's handler pointer is repointed,
# `echo` is issued, and the stock handler is put straight back. Same slot the
# fpSup loader itself uses.
ECHO_SLOT = 0xC0BAC2F8
ECHO_ORIG = 0xC03D99A0
CACHE_FN = 0xC000E91C

# The firmware's own EL Zone scale (FalseColorBarDrawer, 0xC057DB98).
BAND_TABLE = 0xC0D1B74C         # 15 x {u16 x0, u16 x1 inclusive, u32 palette index}
BAND_N = 15
NATIVE_PAL = 0xC0D1B34C         # 256 x {u8 alpha, u8 Y, s8 U, s8 V}
NATIVE_PAL_N = 256
GLYPH_H = 25
PAL_GENERATION = 0x0000FC01     # distinct from anything the layer numbers its own with
GLYPH_SIGN_W = 22
# The label glyphs of scene B5_9's ElZoneScale (object 33212): XCI images in
# MAIN, in the order the label table below indexes them.
GLYPHS = [('minus', 0xC14405C4), ('plus', 0xC1440624), ('half', 0xC143FA68),
          ('zero', 0xC143DE10), ('one', 0xC143DF6C), ('two', 0xC143E15C),
          ('three', 0xC143E420), ('four', 0xC143E714), ('five', 0xC143E978),
          ('six', 0xC143EAC0)]
NONE = 0xFF
# Label x positions from the same scene, left to right; each is a sign glyph
# followed by a numeral or the half glyph, except the bare zero.
LABELS = [(14, 'minus', 'six'), (82, 'minus', 'five'), (150, 'minus', 'four'),
          (218, 'minus', 'three'), (286, 'minus', 'two'), (354, 'minus', 'one'),
          (410, 'minus', 'half'), (503, 'zero', None), (548, 'plus', 'half'),
          (630, 'plus', 'one'), (698, 'plus', 'two'), (766, 'plus', 'three'),
          (834, 'plus', 'four'), (902, 'plus', 'five'), (970, 'plus', 'six')]


def word(image: bytes, address: int) -> int:
    return struct.unpack_from('<I', image, address - BASE)[0]


def branch(site: int, target: int) -> int:
    offset = target - site - 8
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise SystemExit('branch out of range')
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


# ------------------------------------------------------------ the native data

def lz4_block(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        literal = token >> 4
        if literal == 15:
            while True:
                b = src[i]
                i += 1
                literal += b
                if b != 255:
                    break
        out += src[i:i + literal]
        i += literal
        if i + 2 > n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            break
        length = token & 0xF
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - offset
        for k in range(length):
            out.append(out[start + k])
    return bytes(out)


def decode_xci(image: bytes, address: int) -> tuple[int, int, list[int]]:
    """An XCI icon: 16-bit pixels, low byte luminance, high byte alpha.
    Returns (width, height, alpha per pixel, row-major)."""
    off = address - BASE
    if image[off:off + 4] != b'XC\0\0':
        raise SystemExit(f'no XCI at {address:#x}')
    w, h = struct.unpack_from('<HH', image, off + 8)
    block = struct.unpack_from('<I', image, off + 0x2F)[0] & 0x7FFFFFFF
    raw = lz4_block(image[off + 0x33:off + 0x33 + block])
    if len(raw) < w * h * 2:
        raise SystemExit(f'XCI at {address:#x} decoded short')
    return w, h, [raw[2 * i + 1] for i in range(w * h)]


def bands(image: bytes) -> list[tuple[int, int, int]]:
    out = []
    for n in range(BAND_N):
        x0, x1, index = struct.unpack_from('<HHI', image, BAND_TABLE - BASE + n * 8)
        out.append((x0, x1, index & 0xFF))
    out.sort()
    if out[0][0] != 0 or out[-1][1] != 1023:
        raise SystemExit('the band table does not span the 1024-pixel row')
    for (_, a, _), (b, _, _) in zip(out, out[1:]):
        if b != a + 1:
            raise SystemExit('the band table has a gap or an overlap')
    return out


def palette(image: bytes) -> list[tuple[int, int, int, int]]:
    return [struct.unpack_from('<BBbb', image, NATIVE_PAL - BASE + i * 4)
            for i in range(NATIVE_PAL_N)]


def native_include(image: bytes) -> str:
    """The include: bands, labels and glyph bitmaps, all read from MAIN."""
    pal = palette(image)
    white = [i for i, entry in enumerate(pal) if entry == (255, 255, 0, 0)]
    if not white:
        raise SystemExit('no opaque white in the native palette for the labels')
    if pal[0][0] != 0:
        raise SystemExit('native palette index 0 is not transparent')

    lines = ['/* Generated by tools/build_fcscale_autorun.py from the verified',
             ' * SIGMA fp Ver.5.02 MAIN image. Do not edit; rebuild the card. */',
             f'.equ NATIVE_PAL,   {NATIVE_PAL:#x}',
             f'.equ NATIVE_PAL_N, {NATIVE_PAL_N}',
             f'.equ BAND_N,       {BAND_N}',
             f'.equ LABEL_N,      {len(LABELS)}',
             f'.equ GLYPH_N,      {len(GLYPHS)}',
             f'.equ GLYPH_H,      {GLYPH_H}',
             f'.equ GLYPH_SIGN_W, {GLYPH_SIGN_W}',
             f'.equ C_LABEL,      {white[0]}',
             '.balign 4',
             f'bands:                      /* {BAND_TABLE:#x}: x0, x1 inclusive, palette index */']
    for x0, x1, index in bands(image):
        a, y, u, v = pal[index]
        lines.append(f'    .short {x0}, {x1}; .byte {index}, 0, 0, 0'
                     f'    /* a{a} y{y} u{u} v{v} */')

    names = [name for name, _ in GLYPHS]
    lines.append('labels:                     /* x, first glyph, second glyph or 0xFF */')
    for x, first, second in LABELS:
        lines.append(f'    .short {x}; .byte {names.index(first)}, '
                     f'{names.index(second) if second else NONE}')

    headers, bits = [], []
    for name, address in GLYPHS:
        w, h, alpha = decode_xci(image, address)
        if h != GLYPH_H:
            raise SystemExit(f'glyph {name} is {h} rows, expected {GLYPH_H}')
        if name in ('minus', 'plus') and w != GLYPH_SIGN_W:
            raise SystemExit(f'sign glyph {name} is {w} wide, expected {GLYPH_SIGN_W}')
        per_row = (w + 31) // 32
        headers.append((len(bits), w, per_row, name))
        for row in range(h):
            value = 0
            for x in range(w):
                if alpha[row * w + x] >= 128:       # the icons are white; alpha is coverage
                    value |= 1 << x
            for n in range(per_row):
                bits.append((value >> (32 * n)) & 0xFFFFFFFF)
    lines.append('.balign 4')
    lines.append('glyph_hdr:                  /* offset in words, width, words per row */')
    for offset, w, per_row, name in headers:
        lines.append(f'    .short {offset}; .byte {w}, {per_row}    /* {name} */')
    lines.append('.balign 4')
    lines.append('glyph_bits:')
    for i in range(0, len(bits), 4):
        lines.append('    .word ' + ', '.join(f'{b:#010x}' for b in bits[i:i + 4]))
    return '\n'.join(lines) + '\n'


# ------------------------------------------------------------------ the card

def build(out: Path) -> str:
    image = MAIN.read_bytes()
    if hashlib.sha256(image).hexdigest() != MAIN_SHA:
        raise SystemExit('requires the verified SIGMA fp Ver.5.02 MAIN image')
    for site, why in ((PRESS_SITE, 'press'), (RELEASE_SITE, 'release')):
        got = word(image, site)
        if got != SITE_STOCK:
            raise SystemExit(f'the {why} method at {site:#x} starts {got:#x}, '
                             f'expected {SITE_STOCK:#x}')
    if word(image, SUBMIT_SITE) != SUBMIT_STOCK:
        raise SystemExit(f'the display submit at {SUBMIT_SITE:#x} does not start '
                         f'{SUBMIT_STOCK:#x}')
    NATIVE_INC.write_text(native_include(image))
    defines = (f'STATE={STATE:#x}', f'SUBMIT_RESUME={SUBMIT_SITE + 4:#x}')
    code = assemble(ROOT / 'src/fcscale.S', defines)
    marks = symbols(ROOT / 'src/fcscale.S', defines)
    if STATE + STATE_SIZE > CODE:
        raise SystemExit('the state block runs into the code')
    for address, length, why in ((STATE, STATE_SIZE, 'state'), (CODE, len(code), 'code')):
        for at in range(address, address + length, 4):
            if word(image, at):
                raise SystemExit(f'the {why} cave at {at:#x} is not zero in stock')

    words = struct.unpack(f'<{len(code) // 4}I', code)
    press = CODE + marks['fc_press']
    release = CODE + marks['fc_release']
    submit = CODE + marks['fc_submit']
    lines = [
        '# ==========================================================',
        '# False Color / EL Zone as a TOGGLE, with the EL Zone scale',
        '# SIGMA fp Ver.5.02 ONLY. RAM only: remove this file and cold',
        '# boot to revert. Nothing is written to flash.',
        '#',
        '# FIRST, in the camera:',
        '#   Menu -> Custom -> button settings -> False Color',
        '# (this changes what that button does; it does not create it)',
        '#',
        '# Then, on that button:',
        '#   press 1  False Color / EL Zone on, and it STAYS on',
        '#   press 2  the EL Zone scale appears across the bottom',
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
    lines += [f'mem set 0x{STATE + i * 4:08X} 0x00000000' for i in range(STATE_SIZE // 4)]
    lines += [
        "# --- our palette descriptor {entries, count, generation}: the firmware's",
        '#     own 256-entry scale palette, pointed at by the frame descriptor',
        '#     while the scale is up ---',
        f'mem set 0x{STATE + 0x50:08X} 0x{NATIVE_PAL:08X}',
        f'mem set 0x{STATE + 0x54:08X} 0x{NATIVE_PAL_N:08X}',
        f'mem set 0x{STATE + 0x58:08X} 0x{PAL_GENERATION:08X}',
    ]
    lines += [f'mem set 0x{CODE + i * 4:08X} 0x{value:08X}'
              for i, value in enumerate(words)]
    lines += [
        '# --- arm the press and the release, then make the new code real to',
        "#     the caches by borrowing the echo command's handler slot ---",
        f'mem set 0x{PRESS_SITE:08X} 0x{branch(PRESS_SITE, press):08X}',
        f'mem set 0x{RELEASE_SITE:08X} 0x{branch(RELEASE_SITE, release):08X}',
        f'mem set 0x{ECHO_SLOT:08X} 0x{CACHE_FN:08X}',
        'echo',
        '# --- the display submit fires every frame, so it is armed only once',
        '#     the code is clean, and the site itself is flushed after ---',
        f'mem set 0x{SUBMIT_SITE:08X} 0x{branch(SUBMIT_SITE, submit):08X}',
        'echo',
        f'mem set 0x{ECHO_SLOT:08X} 0x{ECHO_ORIG:08X}',
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
    print(f'  submit  {SUBMIT_SITE:#010x} -> {submit:#010x}   state {STATE:#010x}')
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/fcscale')
    args = parser.parse_args()
    build(args.out)


if __name__ == '__main__':
    main()
