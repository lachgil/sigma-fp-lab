#!/usr/bin/env python3
"""Emulate the r5-gated canvas hook and prove its isolation logic offline.

Gate (measured on hardware): rewrite only when armed AND row is 1936x1090 AND
the FieldAngle selector r5 == 175 (open gate FHD/29.97). FHD/25 uses r5 == 180.
Asserts the rewrite happens only for the open-gate case and is a no-op for
FHD/25, for a UHD row, and when disarmed.
"""
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP,
                               UC_ARM_REG_LR)

CODE = 0xC072F800
LOG = 0xC072FA00
ARMED = 0xC072FA10
FIELDANGLE = 0x45000000
ROW = FIELDANGLE + 0x5C
STACK = 0x46000800
RET = 0xC043A1A0
blob = Path("builds/rowpatch_gated.bin").read_bytes()


def run(armed, width, height, r5):
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for base, size in [(0xC0720000, 0x20000), (0x45000000, 0x1000),
                       (0x46000000, 0x1000), (0xC0400000, 0x1000)]:
        uc.mem_map(base, size)
    uc.mem_write(CODE, blob)
    uc.mem_write(ARMED, struct.pack("<I", armed))
    uc.mem_write(LOG, b"\0" * 16)
    uc.mem_write(ROW, struct.pack("<I", width) + struct.pack("<I", height))
    for off in (0x24, 0x2C, 0xD8, 0xDC, 0xE0, 0xE4, 0xF4, 0xF8):
        uc.mem_write(ROW + off, struct.pack("<I", 0xDEAD0000 | off))
    uc.reg_write(UC_ARM_REG_R4, FIELDANGLE)
    uc.reg_write(UC_ARM_REG_R5, r5)
    uc.reg_write(UC_ARM_REG_SP, STACK)
    uc.reg_write(UC_ARM_REG_LR, RET)
    uc.emu_start(CODE, RET)
    row = {off: struct.unpack_from("<I", uc.mem_read(ROW + off, 4))[0]
           for off in (0x00, 0x04, 0x24, 0x2C, 0xD8, 0xE0, 0xDC, 0xE4, 0xF4, 0xF8)}
    hits = struct.unpack_from("<I", uc.mem_read(LOG, 4))[0]
    return row, hits, uc.reg_read(UC_ARM_REG_R4)


REWRITTEN = {0x00: 3032, 0x04: 2012, 0x24: 3008, 0x2C: 2000,
             0xD8: 3032, 0xE0: 2012, 0xDC: 3032, 0xE4: 2012,
             0xF4: 3032, 0xF8: 2012}


def untouched(width, height):
    base = {0x00: width, 0x04: height}
    base.update({off: 0xDEAD0000 | off
                 for off in (0x24, 0x2C, 0xD8, 0xDC, 0xE0, 0xE4, 0xF4, 0xF8)})
    return base


cases = [
    ("armed + FHD row + r5=175 (open gate) -> REWRITE", (1, 1936, 1090, 175), REWRITTEN, 1),
    ("armed + FHD row + r5=180 (FHD/25)    -> no-op",   (1, 1936, 1090, 180), untouched(1936, 1090), 0),
    ("armed + UHD row + r5=175             -> no-op",   (1, 3856, 2170, 175), untouched(3856, 2170), 0),
    ("DISARMED + FHD row + r5=175          -> no-op",   (0, 1936, 1090, 175), untouched(1936, 1090), 0),
]

ok = True
for label, args, expect_row, expect_hits in cases:
    row, hits, r0 = run(*args)
    passed = row == expect_row and hits == expect_hits and r0 == FIELDANGLE
    ok &= passed
    print(f"[{'PASS' if passed else 'FAIL'}] {label}  (hits={hits})")
    if not passed:
        print("   expected", expect_row)
        print("   got     ", row)

print("\nALL PASS" if ok else "\nFAILURES PRESENT")
raise SystemExit(0 if ok else 1)
