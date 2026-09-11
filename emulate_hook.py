#!/usr/bin/env python3
"""Emulate the selector-gated canvas hook and prove its isolation logic offline.

Gate (measured on hardware): rewrite only when armed AND row is 1936x1090 AND
the FieldAngle selector r5 matches an ENABLED selector slot. 175 is FHD/29.97
CinemaDNG; FHD/25 uses 180. The 59.94 slot is data, so a framerate whose
selector has not been measured stays zero and never rewrites. The probe records
every 1936x1090 selector regardless of arming, which is how an unknown one is
measured.
"""
from pathlib import Path
import struct
import sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_SP, UC_ARM_REG_LR)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "reference" / "fpSup" / "fp_usb_shell"))
from armasm import assemble

CODE = 0xC072F800
LOG = 0xC072FA00
ARMED = 0xC072FA10
SELS = 0xC072FA20
PROBE = 0xC072FA30
GEOM = 0xC072FA40
FIELDANGLE = 0x45000000
ROW = FIELDANGLE + 0x5C
STACK = 0x46000800
RET = 0xC043A1A0
blob = assemble(HERE / "src" / "rowpatch_gated.S")


def run(armed, width, height, r5, sels=(175, 0), geom=(3032, 2012, 3008, 2000),
        geom1=(3032, 2012, 3008, 2000)):
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for base, size in [(0xC0720000, 0x20000), (0x45000000, 0x1000),
                       (0x46000000, 0x1000), (0xC0400000, 0x1000)]:
        uc.mem_map(base, size)
    uc.mem_write(CODE, blob)
    uc.mem_write(ARMED, struct.pack("<I", armed))
    uc.mem_write(SELS, struct.pack("<II", *sels))
    uc.mem_write(PROBE, b"\0" * 8)
    uc.mem_write(GEOM, struct.pack("<4I", *geom) + struct.pack("<4I", *geom1))
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
    probe = struct.unpack("<II", uc.mem_read(PROBE, 8))
    abi_ok = (uc.reg_read(UC_ARM_REG_R0) == FIELDANGLE
              and uc.reg_read(UC_ARM_REG_R4) == FIELDANGLE
              and uc.reg_read(UC_ARM_REG_R5) == r5
              and uc.reg_read(UC_ARM_REG_SP) == STACK)
    return row, hits, abi_ok, probe


REWRITTEN = {0x00: 3032, 0x04: 2012, 0x24: 3008, 0x2C: 2000,
             0xD8: 3032, 0xE0: 2012, 0xDC: 3032, 0xE4: 2012,
             0xF4: 3032, 0xF8: 2012}


def untouched(width, height):
    base = {0x00: width, 0x04: height}
    base.update({off: 0xDEAD0000 | off
                 for off in (0x24, 0x2C, 0xD8, 0xDC, 0xE0, 0xE4, 0xF4, 0xF8)})
    return base


# (label, args, kwargs, expected row, expected hits, expected probe)
cases = [
    ("armed + FHD row + r5=175 (open gate) -> REWRITE",
     (1, 1936, 1090, 175), {}, REWRITTEN, 1, (175, 1)),
    ("armed + FHD row + r5=180 (FHD/25)    -> no-op",
     (1, 1936, 1090, 180), {}, untouched(1936, 1090), 0, (180, 1)),
    ("armed + UHD row + r5=175             -> no-op",
     (1, 3856, 2170, 175), {}, untouched(3856, 2170), 0, (0, 0)),
    ("DISARMED + FHD row + r5=175          -> no-op, still probes",
     (0, 1936, 1090, 175), {}, untouched(1936, 1090), 0, (175, 1)),
    ("armed + wrong height + r5=175        -> no-op",
     (1, 1936, 1080, 175), {}, untouched(1936, 1080), 0, (0, 0)),
    # The 59.94 slot: zero means unmeasured, so that framerate records as stock.
    ("armed + FHD row + r5=163, 60p slot 0 -> no-op (unmeasured)",
     (1, 1936, 1090, 163), {}, untouched(1936, 1090), 0, (163, 1)),
    ("armed + FHD row + r5=163, 60p slot set -> REWRITE",
     (1, 1936, 1090, 163), {"sels": (0, 163)}, REWRITTEN, 1, (163, 1)),
    ("armed + FHD row + r5=175, only 60p set -> no-op",
     (1, 1936, 1090, 175), {"sels": (0, 163)}, untouched(1936, 1090), 0, (175, 1)),
    ("armed + FHD row + r5=175, both set     -> REWRITE",
     (1, 1936, 1090, 175), {"sels": (175, 163)}, REWRITTEN, 1, (175, 1)),
    ("armed + FHD row + r5=180, both set     -> no-op",
     (1, 1936, 1090, 180), {"sels": (175, 163)}, untouched(1936, 1090), 0, (180, 1)),
    # The canvas is data: a matched selector with no published canvas must not
    # rewrite, and a second slot can carry a different raster than the first.
    ("armed + match + canvas unpublished     -> no-op",
     (1, 1936, 1090, 175), {"geom": (0, 0, 0, 0)}, untouched(1936, 1090), 0, (175, 1)),
    ("armed + slot1 match + 3968x2640 canvas -> REWRITE at that raster",
     (1, 1936, 1090, 163), {"sels": (0, 163), "geom1": (3968, 2640, 3968, 2640)},
     {0x00: 3968, 0x04: 2640, 0x24: 3968, 0x2C: 2640, 0xD8: 3968, 0xE0: 2640,
      0xDC: 3968, 0xE4: 2640, 0xF4: 3968, 0xF8: 2640}, 1, (163, 1)),
]

ok = True
for label, args, kwargs, expect_row, expect_hits, expect_probe in cases:
    row, hits, abi_ok, probe = run(*args, **kwargs)
    passed = (row == expect_row and hits == expect_hits and abi_ok
              and probe == expect_probe)
    ok &= passed
    print(f"[{'PASS' if passed else 'FAIL'}] {label}  (hits={hits}, probe={probe})")
    if not passed:
        print("   expected", expect_row, expect_probe)
        print("   got     ", row, probe)

print("\nALL PASS" if ok else "\nFAILURES PRESENT")
raise SystemExit(0 if ok else 1)
