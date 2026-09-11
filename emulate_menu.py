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


BUILT_SEL60 = 0


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
    def __init__(self, fail_allocation=0, bad_firmware=False, sel60=0):
        global RAW, SECTIONS, MENU, SYMS
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
        for _ in range(7):
            if self.get(ST) == index:
                return
            self.press(0x0C)
        raise AssertionError('cursor did not reach selection')

    def features(self):
        """og, hfps, gyro, m98a, m98b."""
        return tuple(self.get(ST + i) for i in (4, 8, 12, 24, 28))

    def assert_stock(self):
        assert self.features() == (0, 0, 0, 0, 0)
        for at, expected in GUARDS:
            if at not in (0xC091EA38, 0xC043A19C):
                assert self.get(at) == expected, hex(at)
        assert self.get(0xC072FA10) == 0
        assert (self.get(0xC072FA20), self.get(0xC072FA24)) == (0, 0)
        assert bytes(self.uc.mem_read(0xC3498E2C, 1)) == b'\x01'


OG_CELLS = (0xC0BE5888, 0xC0BE5A28, 0xC0BE5BC8)      # FHD 29.97 picker cell
HF_CELLS = (0xC0BE5858, 0xC0BE59F8, 0xC0BE5B98)      # FHD 59.94 picker cell
UNITY = (0xC0BD9A34, 0xC0BE1684, 0xC0BD9EFC, 0xC0BE1B4C)
M98_VMAX = 0xC0B59548
SEL60 = 163                                          # stand-in measured value

c = Camera()
assert c.get(ST + 20) == 1 and c.get(ST + 16) == 1
assert c.get(0xC091EA38) == CODE + SYMS['entry']
c.assert_stock()
assert c.allocations == 9
print('PASS: real stage2 -> trampoline -> gyro allocation/init -> Stock menu')

# Each option, one key event, and the exact cells it is supposed to own.
def cells(addresses):
    return tuple(c.get(a) for a in addresses)

c.select(1)
assert c.press(0x14) == '>OPEN GATE  ON'
assert c.features() == (1, 0, 0, 0, 0)
assert cells(OG_CELLS) == (0x75,) * 3 and c.get(0xC0B59A28) == 0x00041C70
assert c.get(0xC072FA20) == 175 and c.get(0xC072FA10) == 1
assert cells(UNITY) == (0x400,) * 4
assert c.press(0x14) == '>OPEN GATE  OFF'
c.assert_stock()

c.select(2)
assert c.press(0x14) == '>M98 30P    ON'
assert c.features() == (0, 0, 0, 1, 0)
assert cells(OG_CELLS) == (0x62,) * 3 and c.get(M98_VMAX) == 0x00041516
assert c.get(0xC072FA20) == 175 and c.get(0xC072FA10) == 1
assert cells(UNITY) == (0x400,) * 4
assert c.press(0x14) == '>M98 30P    OFF'
c.assert_stock()

c.select(3)
assert c.press(0x14) == '>HIGH FPS   ON'
assert c.features() == (0, 1, 0, 0, 0)
assert cells(HF_CELLS) == (0x3A,) * 3
# M58 shares the cell's raster, so High FPS must not arm the geometry hook.
assert c.get(0xC072FA10) == 0 and cells(UNITY) == (0x640,) * 4
assert c.press(0x14) == '>HIGH FPS   OFF'
c.assert_stock()

c.select(5)
assert c.press(0x14) == '>GYRO       ON'
assert [c.get(a) for a in HOOKS] == ON
assert c.press(0x14) == '>GYRO       OFF'
c.assert_stock()

c.select(6)
assert c.press(0x14) == '>GYRO-GATE  ON'
assert c.features() == (1, 0, 1, 0, 0)
assert [c.get(a) for a in HOOKS] == ON and c.get(0xC072FA20) == 175
assert c.press(0x14) == '>GYRO-GATE  OFF'
c.assert_stock()
print('PASS: each option owns exactly its cells; High FPS never arms the hook')

# An unmeasured 59.94 selector must refuse and report the probe value instead.
c.put(0xC072FA30, 0xA3)
c.select(4)
assert c.press(0x14) == 'M98 60P NEEDS SEL=A3'
c.assert_stock()
c.put(0xC072FA30, 0x0C)
assert c.press(0x14) == 'M98 60P NEEDS SEL=0C'
c.assert_stock()
print('PASS: M98 60P refuses without a selector and shows the probed value')

# Conflicting options switch each other off rather than fight over a cell.
for first, second, expected in [
        (1, 2, (0, 0, 0, 1, 0)),      # Open Gate -> M98 30P
        (2, 1, (1, 0, 0, 0, 0)),      # M98 30P -> Open Gate
        (6, 2, (0, 0, 1, 1, 0)),      # Gyro-Gate's gate half -> M98 30P
        (2, 6, (1, 0, 1, 0, 0))]:     # M98 30P -> Gyro-Gate
    c.select(first)
    c.press(0x14)
    c.select(second)
    c.press(0x14)
    assert c.features() == expected, (first, second, c.features())
    assert cells(OG_CELLS)[0] in (0x75, 0x62)
    c.select(0)
    c.press(0x14)
    c.assert_stock()
# Different cells: M98 30P and High FPS are independent and must coexist.
c.select(2); c.press(0x14)
c.select(3); c.press(0x14)
assert c.features() == (0, 1, 0, 1, 0)
assert cells(OG_CELLS) == (0x62,) * 3 and cells(HF_CELLS) == (0x3A,) * 3
c.select(0); c.press(0x14)
c.assert_stock()
print('PASS: cell conflicts resolve by switching off; independent cells coexist')

# Every independent live/busy signal must block a change without touching hooks.
for label, end, width in [('busy_words', 'busy_bytes', 4), ('busy_bytes', 'guard_table', 1)]:
    for (address,) in struct.iter_unpack('<I', MENU[SYMS[label]:SYMS[end]]):
        c.select(5)
        c.uc.mem_write(address, b'\x01' + b'\0' * (width - 1))
        assert c.press(0x14) == 'STOP RECORDING / WAIT'
        assert c.features() == (0, 0, 0, 0, 0)
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
    c.select(5)
    assert c.press(0x14) == 'GYRO INIT FAILED'
    assert c.features() == (0, 0, 0, 0, 0)
print('PASS: allocation failures do not arm gyro or falsely enable its menu state')

c = Camera(bad_firmware=True)
assert c.get(ST + 20) == 2 and c.allocations == 0
assert c.get(0xC091EA38) == 0xC0265800
assert c.get(0xC043A19C) == 0xE1A00004
print('PASS: mismatched firmware refuses initialization before allocating/arming')

# With a selector baked in, M98 60P applies and shares the hook cleanly.
c = Camera(sel60=SEL60)
c.select(4)
assert c.press(0x14) == '>M98 60P    ON'
assert c.features() == (0, 0, 0, 0, 1)
assert cells(HF_CELLS) == (0x62,) * 3 and c.get(M98_VMAX) == 0x00040A8A
assert c.get(0xC072FA24) == SEL60 and c.get(0xC072FA10) == 1
assert cells(UNITY) == (0x400,) * 4
# Open Gate uses a different cell/selector slot, so both can run at once, and
# releasing one must not take the shared cells the other still needs.
c.select(1); c.press(0x14)
assert c.features() == (1, 0, 0, 0, 1)
assert (c.get(0xC072FA20), c.get(0xC072FA24)) == (175, SEL60)
c.press(0x14)
assert c.features() == (0, 0, 0, 0, 1)
assert c.get(0xC072FA20) == 0 and c.get(0xC072FA24) == SEL60
assert cells(UNITY) == (0x400,) * 4 and c.get(0xC072FA10) == 1
c.select(3)
assert c.press(0x14) == '>HIGH FPS   ON'
assert c.features() == (0, 1, 0, 0, 0), 'High FPS must release the 59.94 cell'
assert c.get(0xC072FA24) == 0 and c.get(M98_VMAX) == 0x0004082E
c.select(0); c.press(0x14)
c.assert_stock()
print('PASS: M98 60P applies with a measured selector and shares state correctly')
print('Binary smoke checks passed. Cold boot, LCD, recording and concurrency need hardware.')
