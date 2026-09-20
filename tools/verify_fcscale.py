#!/usr/bin/env python3
"""Run src/fcscale.S in an emulator: three presses, a repaint after each, and
the sub layer it painted rendered through the firmware's own palette.

The display manager, the drawable and the event post are the only things
faked; the payload, the firmware's memset and the native tables are the real
bytes. What this proves: the press cycle, the palette install/restore calls,
that every pixel written lands inside the scale's rows, and what the scale
looks like. What it cannot prove: that the LCD composites the sub layer in
every DISP state -- that needs the camera.

    ./tools/verify_fcscale.py            -> builds/fcscale/scale.png
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'reference/fpSup/fp_usb_shell')]
import unicorn as uc
from unicorn import arm_const as ar
from armasm import assemble, symbols
import build_fcscale_autorun as card

W, H = 1024, 682
FAKE = 0x10000000               # manager, drawable, descriptor, pixels
PIX = FAKE + 0x1000
SP = 0x20100000 - 0x10
STOP = 0x20200000
CAMERA_IF = FAKE + 0x800

# Fake object layout
MGR_IF = FAKE                   # [+4] -> MGR_VT
MGR_VT = FAKE + 0x20            # [+0xC] -> resolve stub
DRAWABLE = FAKE + 0x40          # [+0] -> DRW_VT
DRW_VT = FAKE + 0x60            # +0x10 back descriptor, +0x18 set palette, +0x1C restore
DESC = FAKE + 0xA0              # {format, pixels, &geometry, palette}
GEOM = FAKE + 0xC0              # {width, height}
STUB_RESOLVE, STUB_BACK, STUB_SETPAL, STUB_RESTORE = (FAKE + 0x100 + 8 * i for i in range(4))


class Camera:
    def __init__(self):
        image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
        card.NATIVE_INC.write_text(card.native_include(image))
        defines = (f'STATE={card.STATE:#x}',)
        self.code = assemble(ROOT / 'src/fcscale.S', defines)
        self.marks = symbols(ROOT / 'src/fcscale.S', defines)
        u = self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        span = (len(image) + 4095) & ~4095
        u.mem_map(card.BASE, span)
        u.mem_write(card.BASE, image)
        u.mem_write(card.CODE, self.code)
        u.mem_map(0xC3033000, 0x1000)                       # UI_STATE
        u.mem_write(0xC3033A44, struct.pack('<I', 2))       # live view
        u.mem_map(FAKE, 0x1000 + ((W * H + 0xFFFF) & ~0xFFF))
        u.mem_map(0x20000000, 0x200000)                     # stack, stop
        u.mem_write(MGR_IF + 4, struct.pack('<I', MGR_VT))
        u.mem_write(MGR_VT + 0xC, struct.pack('<I', STUB_RESOLVE))
        u.mem_write(DRAWABLE, struct.pack('<I', DRW_VT))
        u.mem_write(DRW_VT + 0x10, struct.pack('<I', STUB_BACK))
        u.mem_write(DRW_VT + 0x18, struct.pack('<II', STUB_SETPAL, STUB_RESTORE))
        u.mem_write(DESC, struct.pack('<IIII', 3, PIX, GEOM, 0))
        u.mem_write(GEOM, struct.pack('<II', W, H))
        for stub in (STUB_RESOLVE, STUB_BACK, STUB_SETPAL, STUB_RESTORE):
            u.mem_write(stub, struct.pack('<I', 0xE12FFF1E))        # bx lr
        self.events, self.palette_calls, self.selectors = [], [], []
        self.pixel_writes = set()
        u.hook_add(uc.UC_HOOK_CODE, self._code)
        u.hook_add(uc.UC_HOOK_MEM_WRITE, self._write, begin=PIX, end=PIX + W * H)

    def _ret(self, value=None):
        if value is not None:
            self.u.reg_write(ar.UC_ARM_REG_R0, value)
        self.u.reg_write(ar.UC_ARM_REG_PC, self.u.reg_read(ar.UC_ARM_REG_LR))

    def _code(self, u, address, size, _):
        r0 = u.reg_read(ar.UC_ARM_REG_R0)
        if address == 0xC0698D80:
            self._ret(MGR_IF)
        elif address == STUB_RESOLVE:
            self.selectors.append(u.reg_read(ar.UC_ARM_REG_R1))
            self._ret(DRAWABLE)
        elif address == STUB_BACK:
            assert r0 == DRAWABLE
            self._ret(DESC)
        elif address == STUB_SETPAL:
            desc = struct.unpack('<III', u.mem_read(u.reg_read(ar.UC_ARM_REG_R1), 12))
            self.palette_calls.append(('set', desc))
            self._ret(0)
        elif address == STUB_RESTORE:
            self.palette_calls.append(('restore', r0))
            self._ret(0)
        elif address == card.CACHE_FN:
            self._ret()
        elif address == 0xC03A0798:                             # POST
            self.events.append(struct.unpack('<I', u.mem_read(u.reg_read(ar.UC_ARM_REG_R1), 4))[0])
            self._ret(1)

    def _write(self, u, access, address, size, value, _):
        self.pixel_writes.add(address - PIX)

    def call(self, name, r0=0, r1=0, r2=0):
        u = self.u
        for reg, val in ((ar.UC_ARM_REG_R0, r0), (ar.UC_ARM_REG_R1, r1),
                         (ar.UC_ARM_REG_R2, r2), (ar.UC_ARM_REG_SP, SP),
                         (ar.UC_ARM_REG_LR, STOP)):
            u.reg_write(reg, val)
        u.emu_start(card.CODE + self.marks[name], STOP, count=200_000_000)
        if u.reg_read(ar.UC_ARM_REG_PC) != STOP:
            raise AssertionError(f'{name} did not return')
        if u.reg_read(ar.UC_ARM_REG_SP) != SP:
            raise AssertionError(f'{name} moved the stack')
        return u.reg_read(ar.UC_ARM_REG_R0)

    def state(self, offset):
        return struct.unpack('<I', self.u.mem_read(card.STATE + offset, 4))[0]

    def pixels(self):
        return bytes(self.u.mem_read(PIX, W * H))


def ayuv_to_rgb(entry):
    a, y, u, v = entry
    r = y + 1.402 * v
    g = y - 0.344136 * u - 0.714136 * v
    b = y + 1.772 * u
    return tuple(max(0, min(255, int(c))) for c in (r, g, b)) + (a,)


def render(pixels: bytes, pal, out: Path) -> None:
    """A PNG of the layer through the firmware's palette; index 0 shows as grey."""
    import zlib
    lut = [bytes(ayuv_to_rgb(entry)[:3]) for entry in pal]
    lut[0] = bytes((40, 40, 40))
    raw = b''.join(b'\0' + b''.join(lut[p] for p in pixels[y * W:(y + 1) * W])
                   for y in range(H))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
    out.write_bytes(b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main() -> None:
    cam = Camera()
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    pal = card.palette(image)
    results = []

    def check(name, passed):
        results.append(dict(case=name, passed=bool(passed)))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    # Press 1: on. Draws nothing.
    cam.call('fc_press', r0=CAMERA_IF)
    cam.call('fc_pass')
    check('press 1 latches the mode on and posts 0x21',
          cam.state(0) == 1 and cam.events == [0x21] and not cam.pixel_writes)
    check('the buffer was learnt from the indexed sub layer (selector 1, format 3)',
          cam.selectors and all(s == card.SUB_LAYER if hasattr(card, 'SUB_LAYER') else s == 1
                                for s in cam.selectors)
          and cam.state(0x28) == PIX and cam.state(0x20) == W and cam.state(0x24) == H)

    # Press 2: the scale. No event, palette installed, painted.
    cam.call('fc_press', r0=CAMERA_IF)
    cam.call('fc_pass')
    check('press 2 shows the scale without posting an event',
          cam.state(0) == 2 and cam.state(0xC) == 1 and cam.events == [0x21])
    check("the firmware's 256-entry palette is installed through the drawable's own setter",
          cam.palette_calls == [('set', (card.NATIVE_PAL, card.NATIVE_PAL_N, 0))])
    pixels = cam.pixels()
    rows = {addr // W for addr in cam.pixel_writes}
    check('every pixel written lies in rows 430..582',
          rows and min(rows) == 430 and max(rows) == 582)
    bands = card.bands(image)
    bar_ok = all(pixels[y * W + x] == index
                 for x0, x1, index in bands for y in (464, 523, 582) for x in (x0, x1))
    check("the bar carries the firmware's own band indices at its edges", bar_ok)
    labels = {p for y in range(430, 455) for p in pixels[y * W:(y + 1) * W]} - {0}
    white = [i for i, e in enumerate(pal) if e == (255, 255, 0, 0)][0]
    check('the labels are drawn in opaque white and nothing else',
          labels == {white} and sum(pixels[y * W:(y + 1) * W].count(white)
                                    for y in range(430, 455)) > 1500)
    render(pixels, pal, ROOT / 'builds/fcscale/scale.png')

    # Not live view: the scale comes down and nothing else is touched.
    cam.u.mem_write(0xC3033A44, struct.pack('<I', 5))
    cam.pixel_writes.clear()
    cam.call('fc_pass')
    check('leaving live view clears the scale rows and nothing else',
          not any(cam.pixels()) and {a // W for a in cam.pixel_writes} == set(range(430, 583)))
    cam.u.mem_write(0xC3033A44, struct.pack('<I', 2))
    cam.call('fc_pass')
    check('back in live view the scale returns', cam.pixels() == pixels)

    # Press 3: off. Palette restored, event 0x22, scale taken down.
    cam.call('fc_press', r0=CAMERA_IF)
    cam.call('fc_pass')
    check('press 3 restores the palette, posts 0x22 and clears the scale',
          cam.state(0) == 0 and cam.events == [0x21, 0x22]
          and cam.palette_calls[-1] == ('restore', DRAWABLE) and not any(cam.pixels()))
    cam.call('fc_pass')
    check('an idle pass touches nothing', cam.state(0x14) == 0)
    check('release is swallowed and reports success',
          cam.call('fc_release') == 1 and cam.state(8) == 1)

    report = dict(cases=results, code_bytes=len(cam.code), events=cam.events,
                  palette_calls=[list(map(str, c)) for c in cam.palette_calls],
                  faked=['display manager 0xC0698D80 and its layer resolver',
                         'the drawable: back descriptor, setPalette, restore',
                         'the event post 0xC03A0798', 'the cache flush'],
                  real=['the payload', 'firmware memset', 'band table, palette, glyphs'],
                  untested=['LCD compositing of the sub layer in each DISP state',
                            'the real drawable rotating three buffers'])
    out = ROOT / 'builds/fcscale/verification.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    if not all(c['passed'] for c in results):
        raise SystemExit('fcscale did not verify')
    print(f'{len(results)} cases; {out}; builds/fcscale/scale.png')


if __name__ == '__main__':
    main()
