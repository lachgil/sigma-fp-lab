#!/usr/bin/env python3
"""Execute src/nbuinject.S against the real hook site and check what it feeds.

The NBU interpreter itself is mocked at 0xC05E6404, immediately after the
displaced prologue: it records the bytes it was handed and advances the reader
the way the firmware does. Everything before that boundary -- the hook's stream
matching, table search, fingerprint guard, buffer substitution and the injected
run -- is the assembled payload running as ARM code.

Checks, in order:
  * a record on another stream is passed through untouched
  * an unlisted record on our stream is passed through untouched
  * a listed record whose bytes changed is refused and counted
  * REPLACE hands over the replacement and leaves the position advanced by the
    stock length, with the stream base and string pool restored
  * INJECT hands over every new record and then the stock one, and leaves the
    position advanced by the stock length only
"""
from __future__ import annotations

from pathlib import Path
import json
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'reference/fpSup/fp_usb_shell')]
import menu_resources as res
import nbu_scene as ns
import fplab_page
import unicorn as uc
from unicorn import arm_const as ar
from armasm import assemble, symbols

CODE = 0x10200000            # where the payload is placed for the test
STATE = 0x10210000
TABLE = 0x10210100
BUFFERS = 0x10220000
READER = 0x10230000
SP = 0x102FFFF0
STOP = 0x10240000
SITE = 0xC05E6400
NBU = 0xC18C0460
POOL = 0x45000000            # the modelled DMA pool the card's offsets land in
POOL_PTR = 0xC3757A7C        # where the firmware keeps that base


def fnv(raw: bytes) -> int:
    value = 0x811C9DC5
    for byte in raw:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


class Fixture:
    def __init__(self, image: bytes, entries: list[dict]):
        defines = (f'INJECT_STATE={STATE:#x}', f'INJECT_TABLE={TABLE:#x}',
                   f'INJECT_COUNT={len(entries)}')
        self.code = assemble(ROOT / 'src/nbuinject.S', defines)
        self.symbols = symbols(ROOT / 'src/nbuinject.S', defines)
        self.image = image
        self.seen: list[bytes] = []
        self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        span = (len(image) + 4095) & ~4095
        self.u.mem_map(res.LOAD, span)
        self.u.mem_write(res.LOAD, image)
        self.u.mem_protect(res.LOAD, span, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
        self.u.mem_map(0x10200000, 0x200000)
        self.u.mem_map(POOL, 0x100000)
        self.u.mem_write(CODE, self.code)
        # The pool base the card's pool-relative entries are resolved against.
        # The firmware word is read-only in this map, so the pointer lives in
        # its own page and POOL_PTR is written there.
        self.u.mem_map(POOL_PTR & ~0xFFF, 0x1000)
        self.put(POOL_PTR, POOL)
        at = BUFFERS
        for entry in entries:
            if entry['mode'] & 2:               # pool-relative: an offset
                entry['buffer_at'] = entry['pool_offset']
                self.u.mem_write(POOL + entry['pool_offset'], entry['buffer'])
            else:
                entry['buffer_at'] = at
                self.u.mem_write(at, entry['buffer'])
                at = (at + len(entry['buffer']) + 15) & ~15
        table = b''.join(struct.pack('<6I', e['record'], e['length'], e['hash'],
                                     e['mode'], e['buffer_at'], len(e['buffer']))
                         for e in entries)
        self.u.mem_write(TABLE, table)
        self.u.hook_add(uc.UC_HOOK_CODE, self.interpreter,
                        begin=SITE + 4, end=SITE + 4)

    def get(self, at: int) -> int:
        return struct.unpack('<I', self.u.mem_read(at, 4))[0]

    def put(self, at: int, *values: int) -> None:
        self.u.mem_write(at, struct.pack('<' + 'I' * len(values), *values))

    def interpreter(self, u, address, size, data):
        """Stand in for the interpreter: consume one record, then return."""
        reader = u.reg_read(ar.UC_ARM_REG_R0)
        position = self.get(reader + 4)
        source = self.get(reader + 36) + position
        length = struct.unpack('>I', u.mem_read(source + 4, 4))[0]
        self.seen.append(bytes(u.mem_read(source, length)))
        self.put(reader + 4, position + length)
        sp = u.reg_read(ar.UC_ARM_REG_SP)
        saved = struct.unpack('<9I', u.mem_read(sp, 36))
        for register, value in zip(range(ar.UC_ARM_REG_R4, ar.UC_ARM_REG_R11 + 1), saved[:8]):
            u.reg_write(register, value)
        u.reg_write(ar.UC_ARM_REG_SP, sp + 36)
        u.reg_write(ar.UC_ARM_REG_R0, 0)
        u.reg_write(ar.UC_ARM_REG_PC, saved[8])

    def run(self, base: int, position: int) -> None:
        self.put(READER + 4, position)
        self.put(READER + 20, 0xC18C0474)          # string pool identity
        self.put(READER + 36, base)
        self.seen.clear()
        self.u.reg_write(ar.UC_ARM_REG_R0, READER)
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        self.u.emu_start(CODE + self.symbols['inject'] - 1, STOP, count=1000000)
        assert self.u.reg_read(ar.UC_ARM_REG_SP) == SP, 'hook left the stack unbalanced'

    def counters(self) -> dict:
        return dict(seen=self.get(STATE), replaced=self.get(STATE + 4),
                    injected=self.get(STATE + 8), refused=self.get(STATE + 12))


def main() -> None:
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    # The payload the card actually installs, built here rather than read from
    # a stale file: a stale plan is exactly how a verifier passes for work the
    # card no longer does.
    plan = fplab_page.build_row(image)
    header, records, menu = plan['header'], plan['body'], plan['menu']

    scene = ns.scene(image, plan['scene'])
    header_at = plan['header_at']
    menu_at = plan['menu_at']
    tail_at = plan['tail_at']
    stock_header = image[header_at - res.LOAD:
                         header_at - res.LOAD + plan['header_stock_size']]
    stock_menu = plan['menu_stock']
    tail_size = plan['tail_size']
    stock_tail = image[tail_at - res.LOAD:tail_at - res.LOAD + tail_size]

    entries = [
        dict(record=header_at, length=len(stock_header), hash=fnv(stock_header),
             mode=2, pool_offset=0x54000, buffer=header),
        dict(record=menu_at, length=len(stock_menu), hash=fnv(stock_menu),
             mode=2, pool_offset=0x56000, buffer=menu),
        dict(record=tail_at, length=tail_size, hash=fnv(stock_tail),
             mode=3, pool_offset=0x58000, buffer=records),
    ]
    fixture = Fixture(image, entries)
    results = []

    def check(name: str, passed: bool) -> None:
        results.append(dict(case=name, passed=bool(passed)))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    # Another stream: passed through, and not even counted as ours.
    other = 0x10260000
    stray = struct.pack('>II', 0x10003, 36) + bytes(28)
    fixture.u.mem_write(other, stray)
    fixture.run(other, 0)
    check('a record on another stream passes through',
          fixture.seen == [stray] and fixture.counters()['seen'] == 0)

    # Our stream, unlisted record.
    first = scene.records[0]
    fixture.run(NBU, first[0] + res.LOAD - NBU)
    check('an unlisted record on our stream passes through',
          fixture.seen == [image[first[0]:first[0] + first[2]]]
          and fixture.counters()['replaced'] == 0)

    # Header: replaced, position advanced by the stock length.
    fixture.run(NBU, header_at - NBU)
    check('the enlarged header is handed over',
          fixture.seen == [header])
    check('the header leaves the stock position and base',
          fixture.get(READER + 4) == header_at - NBU + len(stock_header)
          and fixture.get(READER + 36) == NBU
          and fixture.get(READER + 20) == 0xC18C0474)

    # Menu declaration: replaced, same length.
    fixture.run(NBU, menu_at - NBU)
    check('the Menu declaration gains a child slot',
          fixture.seen == [menu] and menu != stock_menu
          and fixture.get(READER + 4) == menu_at - NBU + len(stock_menu))

    # Terminator: the run is injected first, the stock record last.
    fixture.run(NBU, tail_at - NBU)
    injected = []
    at = 0
    while at < len(records):
        size = struct.unpack_from('>I', records, at + 4)[0]
        injected.append(records[at:at + size])
        at += size
    check('every new record is injected before the terminator',
          fixture.seen == injected + [stock_tail])
    check('the injected run does not move the stream on',
          fixture.get(READER + 4) == tail_at - NBU + tail_size
          and fixture.get(READER + 36) == NBU)
    counters = fixture.counters()
    check('counters agree with the work done',
          counters['replaced'] == 2 and counters['injected'] == len(injected))

    # A record whose bytes changed is refused.
    changed = bytearray(image)
    changed[menu_at - res.LOAD + 32] ^= 1
    refuser = Fixture(bytes(changed), entries)
    refuser.run(NBU, menu_at - NBU)
    check('a changed stock record is refused, not rewritten',
          refuser.seen == [bytes(changed[menu_at - res.LOAD:
                                         menu_at - res.LOAD + len(stock_menu)])]
          and refuser.counters()['refused'] == 1)

    # No pool means no payload: every pool-relative entry has to fall through to
    # the stock record rather than read from offset zero, which is the one way
    # this hook could hand the interpreter garbage.
    poolless = Fixture(image, entries)
    poolless.put(POOL_PTR, 0)
    poolless.run(NBU, tail_at - NBU)
    check('a pool base of zero leaves the scene stock',
          poolless.seen == [stock_tail]
          and poolless.counters()['injected'] == 0)

    report = dict(cases=results, payload_bytes=len(fixture.code),
                  injected_records=len(injected),
                  mocked=['the NBU interpreter itself, at 0xC05E6404'],
                  untested=['native parsing of the injected records',
                            'the row on a camera'])
    out = ROOT / 'builds/fplab-page/inject-verification.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    if not all(case['passed'] for case in results):
        raise SystemExit('injection hook did not verify')
    print(f'{len(results)} cases, {len(fixture.code)} bytes of payload; {out}')


if __name__ == '__main__':
    main()
