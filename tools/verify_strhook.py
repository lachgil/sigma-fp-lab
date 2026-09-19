#!/usr/bin/env python3
"""Execute src/strhook.S at the real resolver site and compare it to stock.

The hook replaces the whole of 0xC05E5B58, so the test is an equivalence one:
for every input a stock record can produce, our code must return exactly what
the firmware's own instructions return. The firmware's version is run in the
same emulator, from the untouched image, to get those answers -- they are not
asserted from reading the disassembly.

Then the part that is new: offsets at and above PRIVATE_BASE come out of our
blob, and an offset past the blob returns zero rather than a wild pointer.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'reference/fpSup/fp_usb_shell')]
import menu_resources as res
import unicorn as uc
from unicorn import arm_const as ar
from armasm import assemble, symbols

SITE = 0xC05E5B58
CODE = 0xC0793100            # a cave, checked empty by the card builder
PRIVATE_BASE = 0x00100000    # far above the pool's 0x2B018 bytes
READER = 0x10000000
SP = 0x100FFFF0
STOP = 0x10100000
STOCK_WORD = 0x3FFFF1B1      # cmp.w r1, #-1, the four bytes the B.W replaces
LABEL = 'FP LAB'


def thumb_branch(site: int, target: int) -> int:
    """A Thumb-2 B.W (T4) at `site` reaching `target`, as one little word."""
    offset = target - (site + 4)
    if not -0x800000 <= offset < 0x800000 or offset % 2:
        raise SystemExit('branch target out of B.W range')
    sign = (offset >> 24) & 1
    j1 = (~((offset >> 23) & 1) ^ sign) & 1
    j2 = (~((offset >> 22) & 1) ^ sign) & 1
    first = 0xF000 | (sign << 10) | ((offset >> 12) & 0x3FF)
    second = 0x9000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    return first | (second << 16)


def build() -> tuple[bytes, dict, int]:
    """Assemble twice: the blob's address is only known after the first pass."""
    defines = (f'PRIVATE_BASE={PRIVATE_BASE:#x}', 'STR_BLOB=0')
    blob_at = CODE + symbols(ROOT / 'src/strhook.S', defines)['str_blob']
    defines = (f'PRIVATE_BASE={PRIVATE_BASE:#x}', f'STR_BLOB={blob_at:#x}')
    code = assemble(ROOT / 'src/strhook.S', defines)
    marks = symbols(ROOT / 'src/strhook.S', defines)
    if CODE + marks['str_blob'] != blob_at:
        raise SystemExit('the blob moved between passes; the two-pass build is wrong')
    return code, marks, blob_at


class Fixture:
    def __init__(self, image: bytes, hooked: bool):
        code, self.marks, self.blob_at = build()
        self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        span = (len(image) + 4095) & ~4095
        self.u.mem_map(res.LOAD, span)
        self.u.mem_write(res.LOAD, image)
        if hooked:
            self.u.mem_write(CODE, code)
            self.u.mem_write(SITE, struct.pack('<I', thumb_branch(SITE, CODE)))
        self.u.mem_protect(res.LOAD, span, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
        self.u.mem_map(READER, 0x200000)
        pool_end = res.NBU_BASE + 12 + struct.unpack_from('>I', image,
                                                          res.NBU_BASE + 16)[0]
        self.pool = res.NBU_BASE + 20
        self.length = pool_end - self.pool
        self.u.mem_write(READER + 0x10, struct.pack('<II', self.length,
                                                    self.pool + res.LOAD))

    def resolve(self, offset: int) -> int:
        self.u.reg_write(ar.UC_ARM_REG_R0, READER)
        self.u.reg_write(ar.UC_ARM_REG_R1, offset & 0xFFFFFFFF)
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        self.u.emu_start(SITE | 1, STOP, count=2000)
        if self.u.reg_read(ar.UC_ARM_REG_PC) != STOP:
            raise AssertionError('resolver did not return')
        if self.u.reg_read(ar.UC_ARM_REG_SP) != SP:
            raise AssertionError('resolver moved the stack')
        return self.u.reg_read(ar.UC_ARM_REG_R0)

    def string(self, pointer: int) -> str:
        raw = bytes(self.u.mem_read(pointer, 64))
        return raw[:raw.index(0)].decode()


def main() -> None:
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    stock = Fixture(image, hooked=False)
    ours = Fixture(image, hooked=True)
    results = []

    def check(name: str, passed: bool) -> None:
        results.append(dict(case=name, passed=bool(passed)))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    # The pool offsets records really use: the first string, a four-digit
    # localization key, and the last byte of the pool.
    samples = [0, 0xE38, 0x260, stock.length - 1, stock.length,
               stock.length + 1, 0xFFFFFFFF, PRIVATE_BASE - 1]
    same = all(ours.resolve(offset) == stock.resolve(offset) for offset in samples)
    check('every stock input resolves exactly as the firmware does', same)
    check('a real key still points into the pool',
          ours.string(ours.resolve(0xE38)) == '0315'
          and ours.resolve(0xE38) == stock.pool + res.LOAD + 0xE38)
    check('the out-of-pool and -1 cases still answer zero',
          ours.resolve(stock.length) == 0 and ours.resolve(0xFFFFFFFF) == 0)
    check('a private offset answers out of our blob',
          ours.resolve(PRIVATE_BASE) == ours.blob_at
          and ours.string(ours.resolve(PRIVATE_BASE)) == LABEL)
    check('a private offset past the blob answers zero, not a wild pointer',
          ours.resolve(PRIVATE_BASE + len(LABEL) + 1) == 0)
    check('the site still holds the stock cmp.w r1, #-1 the hook replaces',
          struct.unpack_from('<I', image, SITE - res.LOAD)[0] == STOCK_WORD)

    report = dict(cases=results, site=hex(SITE), private_base=hex(PRIVATE_BASE),
                  blob=hex(ours.blob_at), payload_bytes=len(build()[0]),
                  pool_length=hex(stock.length), label=LABEL,
                  stock_word=hex(STOCK_WORD),
                  mocked=['nothing: both versions are the real instructions'],
                  untested=['whether the text system draws an unknown key '
                            'literally on the LCD -- that needs a camera'])
    out = ROOT / 'builds/strhook-verification.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    if not all(case['passed'] for case in results):
        raise SystemExit('the string hook did not verify')
    print(f"{len(results)} cases, {report['payload_bytes']} bytes of payload; {out}")


if __name__ == '__main__':
    main()
