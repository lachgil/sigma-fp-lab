#!/usr/bin/env python3
"""AutofocusDeep scratch disassembler + movw/movt xref scanner.

arefs (literal-pool) misses movw/movt-built addresses; this scans ARM code for
movw/movt pairs (per-register) that materialise a target address, and reports
the instruction address (approx callsite of the const).
"""
import struct, sys
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from capstone.arm_const import ARM_INS_MOVW, ARM_INS_MOVT
BASE = 0xC0000000
IMG = Path("analysis/MAIN_c0000000.bin").read_bytes()
md = Cs(CS_ARCH_ARM, CS_MODE_ARM); md.detail = True
mdt = Cs(CS_ARCH_ARM, CS_MODE_THUMB); mdt.detail = True

def off(a): return a - BASE
def w32(a): return struct.unpack_from("<I", IMG, off(a))[0]
def f32(a): return struct.unpack_from("<f", IMG, off(a))[0]

def dis(a, n=40, thumb=False):
    o = off(a); code = IMG[o:o+n*4]
    m = mdt if thumb else md
    for ins in m.disasm(code, a):
        print(f"{ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")

def movwt_xrefs(target):
    """Scan every aligned ARM word for movw/movt (A1 enc) building `target`."""
    tlo = target & 0xFFFF
    thi = (target >> 16) & 0xFFFF
    reg_low = {}   # Rd -> (imm16, waddr)
    results = []
    n = len(IMG) & ~3
    for o in range(0, n, 4):
        w = struct.unpack_from("<I", IMG, o)[0]
        op = w & 0x0FF00000
        if op == 0x03000000:  # movw
            rd = (w >> 12) & 0xF
            imm = ((w >> 4) & 0xF000) | (w & 0xFFF)
            reg_low[rd] = (imm, BASE + o)
        elif op == 0x03400000:  # movt
            rd = (w >> 12) & 0xF
            imm = ((w >> 4) & 0xF000) | (w & 0xFFF)
            if imm == thi and rd in reg_low and reg_low[rd][0] == tlo:
                results.append((reg_low[rd][1], BASE + o))
    return results
def movwt_xrefs_thumb(target):
    """The same scan for Thumb-2 MOVW/MOVT (T3), on 2-byte alignment.

    Whole subsystems here are Thumb -- the GUI and its Lua engine among them --
    and scanning only ARM makes their strings look unreferenced. That cost a
    wrong conclusion once: the Lua expression templates were reported as having
    no reference anywhere in the image, when in fact the code that builds them
    is Thumb.
    """
    tlo = target & 0xFFFF
    thi = (target >> 16) & 0xFFFF
    reg_low = {}
    results = []

    def decode(hw1, hw2):
        # T3: 1111 0 i 10 x100 imm4 | 0 imm3 rd imm8
        rd = (hw2 >> 8) & 0xF
        imm = (((hw1 >> 10) & 1) << 11) | (((hw2 >> 12) & 7) << 8) | (hw2 & 0xFF)
        return rd, imm | (((hw1 & 0xF) << 12))

    n = len(IMG) & ~1
    for o in range(0, n - 2, 2):
        hw1 = struct.unpack_from("<H", IMG, o)[0]
        if (hw1 & 0xFBF0) not in (0xF240, 0xF2C0):
            continue
        hw2 = struct.unpack_from("<H", IMG, o + 2)[0]
        if hw2 & 0x8000:
            continue
        rd, imm = decode(hw1, hw2)
        if (hw1 & 0xFBF0) == 0xF240:        # movw
            reg_low[rd] = (imm, BASE + o)
        elif imm == thi and rd in reg_low and reg_low[rd][0] == tlo:
            results.append((reg_low[rd][1], BASE + o))
    return results


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dis":
        a = int(sys.argv[2], 16); n = int(sys.argv[3]) if len(sys.argv) > 3 else 40
        thumb = len(sys.argv) > 4 and sys.argv[4] == "t"
        dis(a, n, thumb)
    elif cmd == "w":
        for x in sys.argv[2:]:
            a = int(x, 16); print(f"{a:#x} w={w32(a):#x} f={f32(a)}")
    elif cmd == "xref":  # movw/movt xref
        t = int(sys.argv[2], 16)
        for waddr, taddr in movwt_xrefs(t):
            print(f"movw@{waddr:#010x} movt@{taddr:#010x} -> {t:#x}")
