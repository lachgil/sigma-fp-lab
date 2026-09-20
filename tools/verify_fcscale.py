#!/usr/bin/env python3
"""Run src/fcscale.S in an emulator: three presses, display submits between
them, and the sub layer it painted rendered through the firmware's palette.

Faked: the display manager, the drawable (back descriptor, rotate, palette
setters), the event post, the cache flush, and the body of the display submit
after its hooked first instruction. Real: the payload, the firmware's memset,
the native band table, palette and glyphs, and the hook site's stock word.

What this proves: the press cycle, the palette install/restore calls, that
the submit hook paints only format-3 descriptors of the scale's geometry,
that every pixel written lands inside the scale's rows, that a buffer carrying
the scale is cleared once when it is no longer wanted, that the paint lock
skips a frame rather than racing, and that the hooked submit still returns to
its caller with the stack intact. Not provable here: LCD compositing of the
sub layer in each DISP state.

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
FAKE = 0x10000000
SP = 0x20100000 - 0x10
STOP = 0x20200000
CAMERA_IF = FAKE + 0x800
CTRL = FAKE + 0x900

MGR_IF = FAKE                   # [+4] -> MGR_VT
MGR_VT = FAKE + 0x20            # [+0xC] -> resolve stub
DRAWABLE = FAKE + 0x40          # [+0] -> DRW_VT
DRW_VT = FAKE + 0x60            # +0x10 back, +0x14 rotate, +0x18 set palette, +0x1C restore
DESC_UI = FAKE + 0xA0           # what the UI submits
DESC_BACK = FAKE + 0xB0         # what the drawable hands a present
DESC_MAIN = FAKE + 0xC0         # a 16-bit main-layer descriptor
GEOM = FAKE + 0xD0
STUB_RESOLVE, STUB_BACK, STUB_ROTATE, STUB_SETPAL, STUB_RESTORE = (FAKE + 0x100 + 8 * i
                                                                   for i in range(5))
PIX_UI = FAKE + 0x1000
PIX_BACK = PIX_UI + 0x100000
PIX_MAIN = PIX_BACK + 0x100000


class Camera:
    def __init__(self):
        image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
        card.NATIVE_INC.write_text(card.native_include(image))
        defines = (f'STATE={card.STATE:#x}', f'SUBMIT_RESUME={card.SUBMIT_SITE + 4:#x}')
        self.code = assemble(ROOT / 'src/fcscale.S', defines)
        self.marks = symbols(ROOT / 'src/fcscale.S', defines)
        u = self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        span = (len(image) + 4095) & ~4095
        u.mem_map(card.BASE, span)
        u.mem_write(card.BASE, image)
        u.mem_write(card.CODE, self.code)
        assert struct.unpack_from('<I', image, card.SUBMIT_SITE - card.BASE)[0] == card.SUBMIT_STOCK
        u.mem_write(card.SUBMIT_SITE, struct.pack(
            '<I', card.branch(card.SUBMIT_SITE, card.CODE + self.marks['fc_submit'])))
        u.mem_map(0xC3033000, 0x1000)                       # UI_STATE
        self.ui_state(2)
        u.mem_map(FAKE, 0x1000 + 3 * 0x100000)
        u.mem_map(0x20000000, 0x200000)                     # stack, stop
        u.mem_write(MGR_IF + 4, struct.pack('<I', MGR_VT))
        u.mem_write(MGR_VT + 0xC, struct.pack('<I', STUB_RESOLVE))
        u.mem_write(DRAWABLE, struct.pack('<I', DRW_VT))
        u.mem_write(DRW_VT + 0x10, struct.pack('<IIII', STUB_BACK, STUB_ROTATE,
                                                 STUB_SETPAL, STUB_RESTORE))
        u.mem_write(DESC_UI, struct.pack('<IIII', 3, PIX_UI, GEOM, 0))
        u.mem_write(DESC_BACK, struct.pack('<IIII', 3, PIX_BACK, GEOM, 0))
        u.mem_write(DESC_MAIN, struct.pack('<IIII', 1, PIX_MAIN, GEOM, 0))
        u.mem_write(GEOM, struct.pack('<II', W, H))
        for stub in (STUB_RESOLVE, STUB_BACK, STUB_ROTATE, STUB_SETPAL, STUB_RESTORE):
            u.mem_write(stub, struct.pack('<I', 0xE12FFF1E))        # bx lr
        self.events, self.palette_calls, self.selectors = [], [], []
        self.rotates, self.submitted = 0, []
        self.writes = set()
        u.hook_add(uc.UC_HOOK_CODE, self._code)
        u.hook_add(uc.UC_HOOK_MEM_WRITE, self._write, begin=PIX_UI, end=PIX_MAIN + 0x100000)

    def ui_state(self, value):
        self.u.mem_write(0xC3033A44, struct.pack('<I', value))

    def _ret(self, value=None):
        if value is not None:
            self.u.reg_write(ar.UC_ARM_REG_R0, value)
        self.u.reg_write(ar.UC_ARM_REG_PC, self.u.reg_read(ar.UC_ARM_REG_LR))

    def _code(self, u, address, size, _):
        r0 = u.reg_read(ar.UC_ARM_REG_R0)
        if address == card.SUBMIT_SITE + 4:
            # The stock body after the replayed push {r4, r5, r6, r7, fp, lr}:
            # stand in for it by popping that frame and returning 1.
            sp = u.reg_read(ar.UC_ARM_REG_SP)
            lr = struct.unpack('<I', u.mem_read(sp + 20, 4))[0]
            self.submitted.append((r0, u.reg_read(ar.UC_ARM_REG_R1), u.reg_read(ar.UC_ARM_REG_R2)))
            u.reg_write(ar.UC_ARM_REG_SP, sp + 24)
            u.reg_write(ar.UC_ARM_REG_R0, 1)
            u.reg_write(ar.UC_ARM_REG_PC, lr)
        elif address == 0xC0698D80:
            self._ret(MGR_IF)
        elif address == STUB_RESOLVE:
            self.selectors.append(u.reg_read(ar.UC_ARM_REG_R1))
            self._ret(DRAWABLE)
        elif address == STUB_BACK:
            assert r0 == DRAWABLE
            self._ret(DESC_BACK)
        elif address == STUB_ROTATE:
            assert r0 == DRAWABLE
            self.rotates += 1
            self._ret()
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
        self.writes.add(address)

    def _run(self, entry, r0=0, r1=0, r2=0):
        u = self.u
        for reg, val in ((ar.UC_ARM_REG_R0, r0), (ar.UC_ARM_REG_R1, r1),
                         (ar.UC_ARM_REG_R2, r2), (ar.UC_ARM_REG_SP, SP),
                         (ar.UC_ARM_REG_LR, STOP)):
            u.reg_write(reg, val)
        u.emu_start(entry, STOP, count=200_000_000)
        if u.reg_read(ar.UC_ARM_REG_PC) != STOP:
            raise AssertionError(f'{entry:#x} did not return')
        if u.reg_read(ar.UC_ARM_REG_SP) != SP:
            raise AssertionError(f'{entry:#x} moved the stack')
        return u.reg_read(ar.UC_ARM_REG_R0)

    def call(self, name, **regs):
        return self._run(card.CODE + self.marks[name], **regs)

    def ui_submit(self, desc=DESC_UI, sub=1):
        """The UI sending a frame: a call to the hooked 0xC02E8A08."""
        self.writes.clear()
        return self._run(card.SUBMIT_SITE, r0=CTRL, r1=desc, r2=sub)

    def state(self, offset):
        return struct.unpack('<I', self.u.mem_read(card.STATE + offset, 4))[0]

    def pixels(self, base=PIX_UI):
        return bytes(self.u.mem_read(base, W * H))

    def rows_written(self, base=PIX_UI):
        return {(a - base) // W for a in self.writes if base <= a < base + W * H}


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
    white = [i for i, e in enumerate(pal) if e == (255, 255, 0, 0)][0]
    scale_rows = set(range(430, 583))
    results = []

    def check(name, passed):
        results.append(dict(case=name, passed=bool(passed)))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    # Press 1: on. No controller seen yet, so nothing to present.
    cam.call('fc_press', r0=CAMERA_IF)
    check('press 1 latches the mode on, posts 0x21, presents nothing',
          cam.state(0) == 1 and cam.events == [0x21] and cam.rotates == 0 and not cam.submitted)

    # A UI frame: passes through, teaches the hook the controller, paints nothing.
    r = cam.ui_submit()
    check('a hooked submit returns to its caller with the stack intact',
          r == 1 and cam.submitted == [(CTRL, DESC_UI, 1)])
    check('the hook remembers the controller and paints nothing while only on',
          cam.state(0x70) == CTRL and not cam.writes and cam.state(0x74) == 1)

    # Press 2: palette installed, one frame presented through the hook.
    cam.call('fc_press', r0=CAMERA_IF)
    check('press 2 sets the scale flag without posting an event',
          cam.state(0) == 2 and cam.state(0xC) == 1 and cam.events == [0x21])
    check("the firmware's 256-entry palette is installed through the drawable's own setter",
          cam.palette_calls == [('set', (card.NATIVE_PAL, card.NATIVE_PAL_N, 0))]
          and cam.selectors and all(s == card.SUB_LAYER if hasattr(card, 'SUB_LAYER') else s == 1
                                    for s in cam.selectors))
    check('the press presents the back buffer: rotate once, submit it as the sub layer',
          cam.rotates == 1 and cam.submitted[-1] == (CTRL, DESC_BACK, 1))
    back = cam.pixels(PIX_BACK)
    check('the presented buffer carries the scale and the UI buffer is untouched',
          any(back) and not any(cam.pixels(PIX_UI)))

    # The next UI frame gets the scale too, identically.
    cam.ui_submit()
    pixels = cam.pixels()
    check('the next UI frame is painted, in rows 430..582 only, identically',
          cam.rows_written() == scale_rows and pixels == back)
    bands = card.bands(image)
    check("the bar carries the firmware's own band indices at its edges",
          all(pixels[y * W + x] == index
              for x0, x1, index in bands for y in (464, 523, 582) for x in (x0, x1)))
    labels = {p for y in range(430, 455) for p in pixels[y * W:(y + 1) * W]} - {0}
    check('the labels are drawn in opaque white and nothing else',
          labels == {white} and sum(pixels[y * W:(y + 1) * W].count(white)
                                    for y in range(430, 455)) > 1500)
    render(pixels, pal, ROOT / 'builds/fcscale/scale.png')

    # A main-layer frame is not ours.
    cam.ui_submit(DESC_MAIN, sub=0)
    check('a 16-bit main-layer descriptor is passed through untouched',
          not cam.writes and cam.submitted[-1] == (CTRL, DESC_MAIN, 0))

    # Leaving live view: each buffer carrying the scale is cleared once.
    cam.ui_state(5)
    cam.ui_submit()
    check('outside live view a buffer carrying the scale is cleared, rows 430..582 only',
          cam.rows_written() == scale_rows and not any(cam.pixels()))
    cam.ui_submit()
    check('and not touched again once clear', not cam.writes)
    cam.ui_submit(DESC_BACK)
    check('the other buffer is cleared when it comes round', not any(cam.pixels(PIX_BACK)))
    cam.ui_state(2)
    cam.ui_submit()
    check('back in live view the scale returns', cam.pixels() == pixels)

    # The paint lock: a frame arriving while another paint holds it is skipped.
    cam.u.mem_write(card.STATE + 0x6C, struct.pack('<I', 1))
    cam.ui_state(5)
    cam.ui_submit()
    check('a frame arriving while the paint lock is held is left alone',
          not cam.writes and cam.pixels() == pixels)
    cam.u.mem_write(card.STATE + 0x6C, struct.pack('<I', 0))
    cam.ui_state(2)

    # Press 3: presents a cleared frame, restores the palette, posts 0x22.
    cam.call('fc_press', r0=CAMERA_IF)
    check('press 3 posts 0x22, presents once more and restores the palette after',
          cam.state(0) == 0 and cam.events == [0x21, 0x22] and cam.rotates == 2
          and cam.submitted[-1] == (CTRL, DESC_BACK, 1)
          and cam.palette_calls[-1] == ('restore', DRAWABLE))
    cam.ui_submit()
    check('the UI buffer is cleared on its next frame', not any(cam.pixels()))
    cam.ui_submit()
    check('an idle frame touches nothing', not cam.writes)
    check('release is swallowed and reports success',
          cam.call('fc_release') == 1 and cam.state(8) == 1)

    report = dict(cases=results, code_bytes=len(cam.code), events=cam.events,
                  submits=len(cam.submitted), rotates=cam.rotates,
                  palette_calls=[list(map(str, c)) for c in cam.palette_calls],
                  faked=['display manager 0xC0698D80 and its layer resolver',
                         'the drawable: back descriptor, rotate, setPalette, restore',
                         'the display submit body after its first instruction',
                         'the event post 0xC03A0798', 'the cache flush'],
                  real=['the payload', 'firmware memset', 'band table, palette, glyphs',
                        'the stock word at the submit site'],
                  untested=['LCD compositing of the sub layer in each DISP state'])
    out = ROOT / 'builds/fcscale/verification.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    if not all(c['passed'] for c in results):
        raise SystemExit('fcscale did not verify')
    print(f'{len(results)} cases; {out}; builds/fcscale/scale.png')


if __name__ == '__main__':
    main()
