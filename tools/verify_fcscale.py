#!/usr/bin/env python3
"""Run src/fcscale.S in an emulator: presses and holds on the button, display
submits between them, and the main OSD layer it painted rendered to a PNG.

Faked: the display manager, the drawable (back descriptor, rotate), the
event post, the camera's millisecond clock, and the body of the display submit
after its hooked first instruction. Real: the payload, the firmware's memset,
the native band table, palette and glyphs, and the hook site's stock word.

What this proves: that a press switches the mode and a half-second hold works
the scale, in every combination; that the submit hook paints only format-1
descriptors of the scale's geometry; that every pixel written lands inside the
scale's own rows and is a 16-bit word with the alpha nibble set; that a buffer
carrying the scale is cleared once when it is no longer wanted; that the paint
lock skips a frame rather than racing; and that the hooked submit returns to
its caller with the stack intact. Not provable here: the order of the three
colour nibbles (PIXEL_ORDER in the builder) -- that is read off the camera.

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

W, H, BPP = 1024, 682, 2
FAKE = 0x10000000
SP = 0x20100000 - 0x10
STOP = 0x20200000
CAMERA_IF = FAKE + 0x800
CTRL = FAKE + 0x900

MGR_IF = FAKE                   # [+4] -> MGR_VT
MGR_VT = FAKE + 0x20            # [+0xC] -> resolve stub
DRAWABLE = FAKE + 0x40          # [+0] -> DRW_VT
DRW_VT = FAKE + 0x60            # +0x0C front, +0x10 back, +0x14 rotate, +0x18/+0x1C palette
DESC_UI = FAKE + 0xA0           # what the UI submits (main, 16-bit)
DESC_FRONT = FAKE + 0xB0        # the layer's front buffer, painted by a press
DESC_SUB = FAKE + 0xC0          # the indexed sub layer: not ours
GEOM = FAKE + 0xD0
(STUB_RESOLVE, STUB_FRONT, STUB_BACK, STUB_ROTATE, STUB_SETPAL,
 STUB_RESTORE) = (FAKE + 0x100 + 8 * i for i in range(6))
BUF = W * H * BPP
PIX_UI = FAKE + 0x1000
PIX_FRONT = PIX_UI + 0x200000
PIX_SUB = PIX_FRONT + 0x200000
PLACE = card.geometry()
SCALE_ROWS = set(range(PLACE['clear_y'], PLACE['clear_y'] + PLACE['clear_h']))
BAR_ROWS = (PLACE['bar_y'], PLACE['bar_y'] + PLACE['bar_h'] // 2,
            PLACE['bar_y'] + PLACE['bar_h'] - 1)
LABEL_ROWS = range(PLACE['label_y'], PLACE['label_y'] + PLACE['glyph_h'])


class Camera:
    def __init__(self):
        image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
        card.NATIVE_INC.write_text(card.native_include(image))
        self.code = assemble(ROOT / 'src/fcscale.S', card.defines())
        self.marks = symbols(ROOT / 'src/fcscale.S', card.defines())
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
        u.mem_map(FAKE, 0x1000 + 3 * 0x200000)
        u.mem_map(0x20000000, 0x200000)                     # stack, stop
        u.mem_write(MGR_IF + 4, struct.pack('<I', MGR_VT))
        u.mem_write(MGR_VT + 0xC, struct.pack('<I', STUB_RESOLVE))
        u.mem_write(DRAWABLE, struct.pack('<I', DRW_VT))
        u.mem_write(DRW_VT + 0x0C, struct.pack('<IIIII', STUB_FRONT, STUB_BACK, STUB_ROTATE,
                                                  STUB_SETPAL, STUB_RESTORE))
        u.mem_write(DESC_UI, struct.pack('<IIII', 1, PIX_UI, GEOM, 0))
        u.mem_write(DESC_FRONT, struct.pack('<IIII', 1, PIX_FRONT, GEOM, 0))
        u.mem_write(DESC_SUB, struct.pack('<IIII', 3, PIX_SUB, GEOM, 0))
        u.mem_write(GEOM, struct.pack('<II', W, H))
        for stub in (STUB_RESOLVE, STUB_FRONT, STUB_BACK, STUB_ROTATE, STUB_SETPAL, STUB_RESTORE):
            u.mem_write(stub, struct.pack('<I', 0xE12FFF1E))        # bx lr
        self.events, self.selectors = [], []
        self.submitted = []
        self.writes = {}
        self.now = 1000             # the camera's millisecond clock
        u.hook_add(uc.UC_HOOK_CODE, self._code)
        u.hook_add(uc.UC_HOOK_MEM_WRITE, self._write, begin=PIX_UI, end=PIX_SUB + 0x200000)

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
        elif address == STUB_FRONT:
            assert r0 == DRAWABLE
            self._ret(DESC_FRONT)
        elif address in (STUB_BACK, STUB_ROTATE):
            raise AssertionError('a press must not rotate or take the back buffer')
        elif address in (STUB_SETPAL, STUB_RESTORE):
            raise AssertionError("the layer's palette setters must not be called")
        elif address == 0xC03A0798:                             # POST
            self.events.append(struct.unpack('<I', u.mem_read(u.reg_read(ar.UC_ARM_REG_R1), 4))[0])
            self._ret(1)
        elif address == card.CLOCK:
            self._ret(self.now)

    def _write(self, u, access, address, size, value, _):
        self.writes[address] = size

    def _run(self, entry, r0=0, r1=0, r2=0):
        u = self.u
        for reg, val in ((ar.UC_ARM_REG_R0, r0), (ar.UC_ARM_REG_R1, r1),
                         (ar.UC_ARM_REG_R2, r2), (ar.UC_ARM_REG_SP, SP),
                         (ar.UC_ARM_REG_LR, STOP)):
            u.reg_write(reg, val)
        u.emu_start(entry, STOP, count=400_000_000)
        if u.reg_read(ar.UC_ARM_REG_PC) != STOP:
            raise AssertionError(f'{entry:#x} did not return')
        if u.reg_read(ar.UC_ARM_REG_SP) != SP:
            raise AssertionError(f'{entry:#x} moved the stack')
        return u.reg_read(ar.UC_ARM_REG_R0)

    def call(self, name, **regs):
        return self._run(card.CODE + self.marks[name], **regs)

    def ui_submit(self, desc=DESC_UI, sub=0):
        """The UI sending a frame: a call to the hooked 0xC02E8A08."""
        self.writes.clear()
        return self._run(card.SUBMIT_SITE, r0=CTRL, r1=desc, r2=sub)

    def state(self, offset):
        return struct.unpack('<I', self.u.mem_read(card.STATE + offset, 4))[0]

    def pixels(self, base=PIX_UI):
        return struct.unpack(f'<{W * H}H', self.u.mem_read(base, BUF))

    def rows_written(self, base=PIX_UI):
        return {(a - base) // (W * BPP) for a in self.writes if base <= a < base + BUF}

    def hold(self, ms):
        """The button down for `ms` milliseconds, and let go."""
        self.call('fc_press', r0=CAMERA_IF)
        self.now += ms
        return self.call('fc_release', r0=CAMERA_IF)


def render(pixels, out: Path) -> None:
    """A PNG of the layer decoded per the builder's PIXEL_ORDER; alpha 0 shows grey."""
    import zlib

    def rgb(p):
        if not p >> 12:
            return b'\x28\x28\x28'
        a, b_, c = (p >> 8) & 0xF, (p >> 4) & 0xF, p & 0xF
        r, g, b = (a, b_, c) if card.PIXEL_ORDER == 'ARGB' else (c, b_, a)
        return bytes((r * 17, g * 17, b * 17))
    raw = b''.join(b'\0' + b''.join(rgb(p) for p in pixels[y * W:(y + 1) * W]) for y in range(H))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
    out.write_bytes(b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main() -> None:
    cam = Camera()
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    results = []

    def check(name, passed):
        results.append(dict(case=name, passed=bool(passed)))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    # A press: the mode comes on at the press itself, without the scale.
    cam.call('fc_press', r0=CAMERA_IF)
    check('the press latches the mode on, posts 0x21, paints nothing',
          cam.state(0) == 1 and cam.events == [0x21] and not any(cam.pixels(PIX_FRONT))
          and not cam.submitted)
    cam.now += 120
    check('letting go of a short press changes nothing and reports success',
          cam.call('fc_release', r0=CAMERA_IF) == 1
          and cam.state(0) == 1 and cam.state(0xC) == 0 and cam.events == [0x21])

    # A UI frame: passes through, teaches the hook the controller, paints nothing.
    r = cam.ui_submit()
    check('a hooked submit returns to its caller with the stack intact',
          r == 1 and cam.submitted == [(CTRL, DESC_UI, 0)])
    check('the hook paints nothing while only on', not cam.writes and cam.state(0x74) == 1)

    # A hold while the mode is on: the scale comes up, and no event is posted.
    check('a hold puts the scale up without posting an event',
          cam.hold(card.HOLD_MS) == 1 and cam.state(0) == 2
          and cam.state(0xC) == 1 and cam.events == [0x21])
    check('the hold paints the main layer front buffer: resolve 0, no submit',
          len(cam.submitted) == 1 and cam.selectors and all(s == 0 for s in cam.selectors))
    back = cam.pixels(PIX_FRONT)
    check('the front buffer carries the scale and the UI buffer is untouched',
          any(back) and not any(cam.pixels(PIX_UI)))

    # The next UI frame gets the scale too, identically.
    cam.ui_submit()
    pixels = cam.pixels()
    check(f"the next UI frame is painted, in rows {min(SCALE_ROWS)}..{max(SCALE_ROWS)} "
          'only, identically', cam.rows_written() == SCALE_ROWS and pixels == back)
    check('every write is a 16-bit pixel', set(cam.writes.values()) == {2})
    bands = card.scaled_bands(image)
    pal = card.palette(image)
    check("the bar carries the firmware's colours, converted, opaque, at its edges",
          all(pixels[y * W + x] == card.pack16(card.ayuv_to_rgb(pal[index]))
              and pixels[y * W + x] >> 12 == 0xF
              for x0, x1, index in bands for y in BAR_ROWS for x in (x0, x1)))
    check('the bar spans exactly the rescaled width, and nothing outside it',
          all(pixels[BAR_ROWS[1] * W + x] == 0
              for x in (bands[0][0] - 1, bands[-1][1] + 1))
          and bands[0][0] == PLACE['left']
          and bands[-1][1] == PLACE['left'] + PLACE['width'] - 1)
    labels = {p for y in LABEL_ROWS for p in pixels[y * W:(y + 1) * W]} - {0}
    check('the labels are opaque white and nothing else',
          labels == {0xFFFF} and sum(pixels[y * W:(y + 1) * W].count(0xFFFF)
                                     for y in LABEL_ROWS) > 1000)
    render(pixels, ROOT / 'builds/fcscale/scale.png')

    # The indexed sub layer is not ours.
    cam.ui_submit(DESC_SUB, sub=1)
    check('an indexed sub-layer descriptor is passed through untouched',
          not cam.writes and cam.submitted[-1] == (CTRL, DESC_SUB, 1))

    # Another UI screen (False Color's own) still gets the scale.
    cam.ui_state(9)
    cam.ui_submit()
    check('a UI state other than playback or menu keeps the scale', cam.pixels() == pixels)

    # Playback or the camera menu: every buffer carrying the scale is cleared on
    # the FIRST frame after the gate closes, not one buffer per frame. Clearing
    # them one at a time leaves the scale sitting in the menu until each buffer
    # happens to come round again (camera, 2026-09-21).
    cam.ui_submit(DESC_FRONT)
    check('both buffers carry the scale while the gate is open',
          any(cam.pixels(PIX_UI)) and any(cam.pixels(PIX_FRONT)))
    cam.ui_state(4)
    cam.ui_submit()
    check('the first frame after the gate closes clears every buffer at once',
          not any(cam.pixels()) and not any(cam.pixels(PIX_FRONT))
          and cam.state(0x60) == cam.state(0x64) == cam.state(0x68) == 0)
    check('and the rows cleared are only ours',
          cam.rows_written() == SCALE_ROWS)
    cam.ui_submit()
    check('and not touched again once clear', not cam.writes)
    cam.ui_state(5)
    cam.ui_submit(DESC_FRONT)
    check('the other buffer is cleared when it comes round, in the menu too',
          not any(cam.pixels(PIX_FRONT)))
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

    # A second hold while the mode is on: the scale goes, the mode stays -- and
    # it comes off EVERY buffer it was on, not just the one being shown. The
    # camera menu can claim a buffer before that buffer next comes round in
    # live view, and then the menu draws with our scale still on it.
    cam.ui_submit(DESC_FRONT)
    carried = [base for base in (PIX_UI, PIX_FRONT) if any(cam.pixels(base))]
    check('both buffers carry the scale before it is taken down',
          carried == [PIX_UI, PIX_FRONT])
    check('a second hold takes the scale down and leaves the mode on',
          cam.hold(card.HOLD_MS + 400) == 1 and cam.state(0) == 1
          and cam.state(0xC) == 0 and cam.events == [0x21])
    check('taking it down clears every buffer it was on, with no further frames',
          not any(cam.pixels(PIX_FRONT)) and not any(cam.pixels(PIX_UI))
          and cam.state(0x60) == cam.state(0x64) == cam.state(0x68) == 0)
    check('a hold is counted, and the press it followed measured',
          cam.state(0x18) == 2 and cam.state(0x14) == card.HOLD_MS + 400)

    # And a hold from off brings the mode up with the scale in one gesture.
    cam.hold(10)
    check('a press from on turns the mode off and posts 0x22',
          cam.state(0) == 0 and cam.state(0xC) == 0 and cam.events == [0x21, 0x22])
    check('a hold from off comes up with the scale, posting 0x21 once',
          cam.hold(card.HOLD_MS) == 1 and cam.state(0) == 2 and cam.state(0xC) == 1
          and cam.events == [0x21, 0x22, 0x21])
    cam.ui_submit()
    check('that hold draws the same scale', cam.pixels() == pixels)

    # Off again from the scale state: one press, no hold needed.
    cam.hold(80)
    check('one press from the scale state turns everything off',
          cam.state(0) == 0 and cam.state(0xC) == 0
          and cam.events == [0x21, 0x22, 0x21, 0x22])
    cam.ui_submit()
    check('the UI buffer is cleared on its next frame', not any(cam.pixels()))
    cam.ui_submit()
    check('an idle frame touches nothing', not cam.writes)
    check('every press and release was accounted for',
          cam.state(4) == cam.state(8) == 6)

    report = dict(cases=results, code_bytes=len(cam.code), events=cam.events,
                  submits=len(cam.submitted),
                  pixel_order=card.PIXEL_ORDER,
                  faked=['display manager 0xC0698D80 and its layer resolver',
                         'the drawable: front descriptor',
                         'the display submit body after its first instruction',
                         'the event post 0xC03A0798'],
                  real=['the payload', 'firmware memset', 'band table, palette, glyphs',
                        'the stock word at the submit site'],
                  untested=['the order of the three colour nibbles (PIXEL_ORDER)'])
    out = ROOT / 'builds/fcscale/verification.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    if not all(c['passed'] for c in results):
        raise SystemExit('fcscale did not verify')
    print(f'{len(results)} cases; {out}; builds/fcscale/scale.png')


if __name__ == '__main__':
    main()
