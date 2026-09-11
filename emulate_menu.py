#!/usr/bin/env python3
"""Exercise the shipped ARM binary, including stage2 and real gyro boot code.

Firmware allocator, cache, mode getter and LCD calls are boundary stand-ins.
This is not a simulation of the sensor, filesystem, LCD or RTOS concurrency.
"""
import json
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *
import subprocess
import sys
from build_combined_card import parse_vbin

ROOT = Path(__file__).resolve().parent
CARD = ROOT / 'builds/combined-menu'
RAW = (CARD / 'VSHL.BIN').read_bytes()
MANIFEST = json.loads((CARD / 'manifest.json').read_text())
SYMS = MANIFEST['menu_symbols']
FW = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
POOL = 0x45000000
CODE = POOL + 0x50000
ST = 0xC072FB00
RETURN = 0x46000000
STACK = 0x46010000
HOOKS = [0xC050D4C8, 0xC03790B8, 0xC038C484, 0xC0058310]
OFF = [0xE3A02000, 0xE5DB25CE, 0xE3500000, 0xE1A05001]
_, SECTIONS = parse_vbin(RAW)
MENU = dict(SECTIONS)[0x50000]
ON = [x[2] for x in struct.iter_unpack('<III', MENU[SYMS['gyro_hooks_table']:SYMS['busy_words']])]
GUARDS = list(struct.iter_unpack('<II', MENU[SYMS['guard_table']:SYMS['labels']]))


BUILT_SEL60 = 173      # build_combined_card.py's measured default


def build(sel60):
    """Rebuild the card with a measured 59.94 selector baked in."""
    out = ROOT / 'builds/combined-menu-sel'
    subprocess.run([sys.executable, str(ROOT / 'build_combined_card.py'),
                    '--out', str(out), '--og60-sel', str(sel60)],
                   check=True, capture_output=True)
    raw = (out / 'VSHL.BIN').read_bytes()
    syms = json.loads((out / 'manifest.json').read_text())['menu_symbols']
    sections = parse_vbin(raw)[1]
    return raw, sections, dict(sections)[0x50000], syms


class Camera:
    def __init__(self, fail_allocation=0, bad_firmware=False, sel60=None):
        global RAW, SECTIONS, MENU, SYMS
        sel60 = BUILT_SEL60 if sel60 is None else sel60
        if sel60 != BUILT_SEL60:
            RAW, SECTIONS, MENU, SYMS = build(sel60)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in [(0xC0000000, 0x4000000), (POOL, 0x100000),
                              (RETURN, 0x20000), (0x47000000, 0x100000)]:
            self.uc.mem_map(address, size)
        self.uc.mem_write(0xC0000000, FW)
        self.uc.mem_write(POOL + 0x8000, RAW)
        self.put(0xC3757A7C, POOL)
        self.put(0xC31AC530, 0xC31AC59C)
        self.uc.mem_write(0xC3498E2C, b'\x01')
        if bad_firmware:
            self.put(HOOKS[0], 0xE1A00000)
        self.fail_allocation = fail_allocation
        self.allocations = 0
        self.next_allocation = 0x47000000
        self.draws = []
        self.composites = 0
        self.native = []
        self.uc.hook_add(UC_HOOK_CODE, self.boundary)
        self.sel60 = sel60
        self.run(POOL + 0x8000 + 16 + 8 * len(SECTIONS), r0=POOL + 0x8000)

    def get(self, address):
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def put(self, address, value):
        self.uc.mem_write(address, struct.pack('<I', value))

    def boundary(self, uc, address, size, _):
        known = {0xC001CF78, 0xC001D038, 0xC001D2B8, 0xC000E91C,
                 0xC0058340, 0xC03E4620, 0xC03E3D00, 0xC0265800}
        if address not in known:
            return
        assert uc.reg_read(UC_ARM_REG_SP) % 8 == 0, hex(address)
        value = 0
        if address == 0xC001CF78:
            value = 0x470F0000
        elif address == 0xC001D038:
            self.allocations += 1
            if self.allocations != self.fail_allocation:
                value = self.next_allocation
                self.next_allocation += uc.reg_read(UC_ARM_REG_R1)
        elif address == 0xC0058340:
            value = 1  # CINE
        elif address == 0xC03E4620:
            assert uc.reg_read(UC_ARM_REG_R1) == 1
            message = self.get(uc.reg_read(UC_ARM_REG_R2))
            self.draws.append(bytes(uc.mem_read(message, 32)).split(b'\0')[0].decode())
        elif address == 0xC03E3D00:
            assert bytes(uc.mem_read(self.get(uc.reg_read(UC_ARM_REG_R2)), 2)) == b'1\0'
            self.composites += 1
        elif address == 0xC0265800:
            self.native.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)))
            value = 1
        uc.reg_write(UC_ARM_REG_R0, value)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def run(self, address, r0=0, key=0):
        saved = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                 UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
        for i, reg in enumerate(saved):
            self.uc.reg_write(reg, 0x12340000 + i)
        self.uc.reg_write(UC_ARM_REG_R0, r0)
        self.uc.reg_write(UC_ARM_REG_R1, key)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN)
        self.uc.emu_start(address, RETURN, count=1000000)
        assert self.uc.reg_read(UC_ARM_REG_PC) == RETURN, 'did not return'
        assert self.uc.reg_read(UC_ARM_REG_SP) == STACK
        assert [self.uc.reg_read(r) for r in saved] == [0x12340000 + i for i in range(8)]

    def press(self, key):
        self.draws.clear()
        before = self.composites
        self.run(self.get(0xC091EA38), r0=0x12345678, key=key)
        if key in (0x0C, 0x14):
            assert len(self.draws) == 4 and len(set(self.draws)) == 1
            assert self.composites - before == 4
            return self.draws[0].strip()
        assert not self.draws

    def select(self, index):
        for _ in range(10):
            if self.get(ST) == index:
                return
            self.press(0x0C)
        raise AssertionError('cursor did not reach selection')

    def features(self):
        """og, m130, gyro, m98a, m98b, m6, m130f."""
        return tuple(self.get(ST + i) for i in (4, 8, 12, 24, 28, 36, 40))

    def geom(self, slot=0):
        base = GEOM + slot * 0x10
        return tuple(self.get(base + i * 4) for i in range(4))

    def assert_stock(self):
        assert self.features() == (0, 0, 0, 0, 0, 0, 0)
        for at, expected in GUARDS:
            if at not in (0xC091EA38, 0xC043A19C):
                assert self.get(at) == expected, hex(at)
        assert self.get(0xC072FA10) == 0
        assert (self.get(0xC072FA20), self.get(0xC072FA24)) == (0, 0)
        assert self.geom(0) == (0, 0, 0, 0) and self.geom(1) == (0, 0, 0, 0)
        assert bytes(self.uc.mem_read(0xC3498E2C, 1)) == b'\x01'


OG_CELLS = (0xC0BE5888, 0xC0BE5A28, 0xC0BE5BC8)      # FHD 29.97 picker cell
HF_CELLS = (0xC0BE5858, 0xC0BE59F8, 0xC0BE5B98)      # FHD 59.94 picker cell
K4_CELLS = (0xC0BE58E8, 0xC0BE5A88, 0xC0BE5C28)      # 4K picker cell
M130_VMAX = 0xC0B59A88
M130_HMAX = 0xC0B59A84
GEOM = 0xC072FA40
BINNED = (3032, 2012, 3008, 2000)
FULL130 = (3968, 2640, 3968, 2640)
UNITY = (0xC0BD9A34, 0xC0BE1684, 0xC0BD9EFC, 0xC0BE1B4C)
M98_VMAX = 0xC0B59548
SEL60 = 163                                          # stand-in measured value

c = Camera()
assert c.get(ST + 20) == 1 and c.get(ST + 16) == 1
assert c.get(0xC091EA38) == CODE + SYMS['entry']
c.assert_stock()
assert c.allocations == 9
print('PASS: real stage2 -> trampoline -> gyro allocation/init -> Stock menu')


def cells(addresses):
    return tuple(c.get(a) for a in addresses)


# Each option, one key event, and the exact cells/canvas it is supposed to own.
c.select(1)
assert c.press(0x14) == '>OPEN GATE  ON'
assert c.features() == (1, 0, 0, 0, 0, 0, 0)
assert cells(OG_CELLS) == (0x75,) * 3 and c.get(0xC0B59A28) == 0x00041C70
assert c.get(0xC072FA20) == 175 and c.get(0xC072FA10) == 1
assert c.geom(0) == BINNED and cells(UNITY) == (0x400,) * 4
assert c.press(0x14) == '>OPEN GATE  OFF'
c.assert_stock()

c.select(2)
assert c.press(0x14) == '>M98 30P    ON'
assert c.features() == (0, 0, 0, 1, 0, 0, 0)
assert cells(OG_CELLS) == (0x62,) * 3 and c.get(M98_VMAX) == 0x00041516
assert c.get(0xC072FA20) == 175 and c.geom(0) == BINNED
assert c.press(0x14) == '>M98 30P    OFF'
c.assert_stock()

# M130 is the full-readout one: different canvas, its own timing entry, and the
# high half of that timing word must survive the rewrite.
c.select(3)
assert c.press(0x14) == '>M130 30P   ON'
assert c.features() == (0, 1, 0, 0, 0, 0, 0)
assert cells(OG_CELLS) == (0x82,) * 3 and c.get(M130_VMAX) == 0x04201517
assert c.get(M130_VMAX) >> 16 == 0x0420, 'high half of the timing word lost'
assert c.get(0xC072FA20) == 175 and c.geom(0) == FULL130
assert c.get(M98_VMAX) == 0x0004082E, 'M130 must not touch M98 timing'
assert c.press(0x14) == '>M130 30P   OFF'
c.assert_stock()

# M130 FAST is the HMAX experiment: same canvas, shorter line period.
c.select(4)
assert c.press(0x14) == '>M130 FAST  ON'
assert c.features() == (0, 0, 0, 0, 0, 0, 1)
assert cells(OG_CELLS) == (0x82,) * 3
assert c.get(M130_HMAX) == 0x014A014A and c.get(M130_VMAX) == 0x04201C6E
assert c.get(M130_HMAX) >> 16 == 0x014A and c.get(M130_VMAX) >> 16 == 0x0420
assert c.geom(0) == FULL130 and c.get(0xC072FA20) == 175
assert c.press(0x14) == '>M130 FAST  OFF'
c.assert_stock()

# M130 30P and M130 FAST share the cell AND the timing entry: one at a time,
# and whoever lets go must restore the stock line period.
c.select(3); c.press(0x14)
c.select(4); c.press(0x14)
assert c.features() == (0, 0, 0, 0, 0, 0, 1)
assert c.get(M130_HMAX) == 0x014A014A
c.select(3); c.press(0x14)
assert c.features() == (0, 1, 0, 0, 0, 0, 0)
assert c.get(M130_HMAX) == 0x01BD01BD, 'stock line period not restored'
assert c.get(M130_VMAX) == 0x04201517
c.select(0); c.press(0x14)
c.assert_stock()

# The SEL line is read-only: it reports the probe and changes nothing.
c.put(0xC072FA30, 0xAF)
c.select(9)
assert c.press(0x0C) is not None
c.select(9)
assert c.press(0x14) == 'SEL PROBE=AF'
c.assert_stock()
c.put(0xC072FA30, 0x2F)
assert c.press(0x14) == 'SEL PROBE=2F'
c.assert_stock()
c.put(0xC072FA30, 0)

c.select(5)
assert c.press(0x14) == '>M98 60P    ON'
assert c.features() == (0, 0, 0, 0, 1, 0, 0)
assert cells(HF_CELLS) == (0x62,) * 3 and c.get(M98_VMAX) == 0x00040A8A
assert c.get(0xC072FA24) == 173 and c.get(0xC072FA20) == 0
assert c.geom(1) == BINNED and c.geom(0) == (0, 0, 0, 0)
assert c.press(0x14) == '>M98 60P    OFF'
c.assert_stock()

# M6 shares the 4K raster, so it must stay a picker-only swap: no hook, no
# canvas, no timing rewrite.
c.select(6)
assert c.press(0x14) == '>M6 4K      ON'
assert c.features() == (0, 0, 0, 0, 0, 1, 0)
assert cells(K4_CELLS) == (0x06,) * 3
assert c.get(0xC072FA10) == 0 and c.geom(0) == (0, 0, 0, 0)
assert cells(UNITY) == (0x640,) * 4
assert c.press(0x14) == '>M6 4K      OFF'
c.assert_stock()

c.select(7)
assert c.press(0x14) == '>GYRO       ON'
assert [c.get(a) for a in HOOKS] == ON
assert c.press(0x14) == '>GYRO       OFF'
c.assert_stock()

c.select(8)
assert c.press(0x14) == '>GYRO-GATE  ON'
assert c.features() == (1, 0, 1, 0, 0, 0, 0)
assert [c.get(a) for a in HOOKS] == ON and c.geom(0) == BINNED
assert c.press(0x14) == '>GYRO-GATE  OFF'
c.assert_stock()
print('PASS: each option owns exactly its cells, canvas and timing entry')

# Everything that shares the 29.97 picker cell must hand it over, not stack.
for first, second, expected, cell, canvas in [
        (1, 2, (0, 0, 0, 1, 0, 0, 0), 0x62, BINNED),      # Open Gate -> M98 30P
        (2, 3, (0, 1, 0, 0, 0, 0, 0), 0x82, FULL130),     # M98 30P -> M130
        (3, 1, (1, 0, 0, 0, 0, 0, 0), 0x75, BINNED),      # M130 -> Open Gate
        (3, 8, (1, 0, 1, 0, 0, 0, 0), 0x75, BINNED),      # M130 -> Gyro-Gate
        (8, 3, (0, 1, 1, 0, 0, 0, 0), 0x82, FULL130)]:    # Gyro-Gate -> M130
    c.select(first)
    c.press(0x14)
    c.select(second)
    c.press(0x14)
    assert c.features() == expected, (first, second, c.features())
    assert cells(OG_CELLS) == (cell,) * 3 and c.geom(0) == canvas
    # Whoever let go must have put its own timing entry back.
    assert c.get(M98_VMAX) == 0x0004082E or expected[3]
    assert c.get(M130_VMAX) == 0x04201014 or expected[1]
    c.select(0)
    c.press(0x14)
    c.assert_stock()
# M98 has one timing entry, so its two rates cannot both be live.
c.select(2); c.press(0x14)
c.select(5); c.press(0x14)
assert c.features() == (0, 0, 0, 0, 1, 0, 0)
assert cells(OG_CELLS) == (0x6A,) * 3 and c.get(M98_VMAX) == 0x00040A8A
c.select(0); c.press(0x14)
c.assert_stock()
print('PASS: shared cell and shared timing entry both hand over cleanly')

# Independent cells: M130 at 29.97, M98 at 59.94 and M6 at 4K coexist, each
# with its own selector and canvas -- the per-slot geometry this needs.
c.select(3); c.press(0x14)
c.select(5); c.press(0x14)
c.select(6); c.press(0x14)
assert c.features() == (0, 1, 0, 0, 1, 1, 0)
assert (c.get(0xC072FA20), c.get(0xC072FA24)) == (175, 173)
assert c.geom(0) == FULL130 and c.geom(1) == BINNED
assert cells(OG_CELLS) == (0x82,) * 3 and cells(HF_CELLS) == (0x62,) * 3
assert cells(K4_CELLS) == (0x06,) * 3
# Releasing one must not take the shared cells or the other's canvas.
c.select(3); c.press(0x14)
assert c.features() == (0, 0, 0, 0, 1, 1, 0)
assert c.geom(0) == (0, 0, 0, 0) and c.geom(1) == BINNED
assert cells(UNITY) == (0x400,) * 4 and c.get(0xC072FA10) == 1
c.select(0); c.press(0x14)
c.assert_stock()
print('PASS: independent framerates coexist with per-slot canvases')

# Every independent live/busy signal must block a change without touching hooks.
for label, end, width in [('busy_words', 'busy_bytes', 4), ('busy_bytes', 'guard_table', 1)]:
    for (address,) in struct.iter_unpack('<I', MENU[SYMS[label]:SYMS[end]]):
        c.select(7)
        c.uc.mem_write(address, b'\x01' + b'\0' * (width - 1))
        assert c.press(0x14) == 'STOP RECORDING / WAIT'
        assert c.features() == (0, 0, 0, 0, 0, 0, 0)
        assert [c.get(a) for a in HOOKS] == OFF
        c.uc.mem_write(address, b'\0' * width)
for key in (0x0D, 0x15, 0x1C, 0x2F):
    c.press(key)
    assert c.native[-1] == (0x12345678, key)
print('PASS: recording/finalization guards and native key/release passthrough')

for failed in (1, 5, 9):
    c = Camera(fail_allocation=failed)
    assert c.get(ST + 16) == 0
    c.assert_stock()
    c.select(7)
    assert c.press(0x14) == 'GYRO INIT FAILED'
    assert c.features() == (0, 0, 0, 0, 0, 0, 0)
print('PASS: allocation failures do not arm gyro or falsely enable its menu state')

c = Camera(bad_firmware=True)
assert c.get(ST + 20) == 2 and c.allocations == 0
assert c.get(0xC091EA38) == 0xC0265800
assert c.get(0xC043A19C) == 0xE1A00004
print('PASS: mismatched firmware refuses initialization before allocating/arming')

# Built without the measured selector, the 59.94 option must refuse and report
# what the probe saw instead of rewriting geometry for a guess.
c = Camera(sel60=0)
c.put(0xC072FA30, 0xAD)
c.select(5)
assert c.press(0x14) == 'M98 60P NEEDS SEL=AD'
c.assert_stock()
print('PASS: an unmeasured selector refuses and shows the probed value')
print('Binary smoke checks passed. Cold boot, LCD, recording and concurrency need hardware.')
