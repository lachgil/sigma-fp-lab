#!/usr/bin/env python3
"""Run fp 5.02's own NBU record interpreter over scene data, offline.

Real firmware code: the record dispatcher at 0xC05E6400, the scene allocation
arithmetic, the object factory, parent attachment, name assignment and the
sorted id table. Modelled: bounded allocation and free, the big-endian byte and
word readers, arena selection, and the component-size registry, which returns
zero here -- so component records are not instantiated and arena sizes exclude
component storage. Declarations, ids and parenting are real.
"""
from __future__ import annotations

from pathlib import Path
import collections
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import menu_resources as res
import unicorn as uc
from unicorn import arm_const as ar

DECLARATION = 0x10003
POOL = res.NBU_BASE + 20
LOOKUP = 0xC05E82A1          # id -> object, binary search over the sorted table
CHILDREN = 0xC05D1EA9        # child count of an object
INTERPRET = 0xC05E6401       # one record
TABLE_STORE = 0xC05E8290     # the sorted-insert store, watched by watch_table


class SceneVM:
    P = 0x10000000
    CTX = 0x10000200
    ALLOC = 0x10001000
    SCENE = 0x10002000
    DATA = 0x10010000
    SP = 0x11FFFFF0
    STOP = 0x1000F000

    def __init__(self, image: bytes):
        self.image = image
        pool_end = res.NBU_BASE + 12 + struct.unpack_from('>I', image, res.NBU_BASE + 16)[0]
        self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        span = (len(image) + 4095) & ~4095
        self.u.mem_map(res.LOAD, span)
        self.u.mem_write(res.LOAD, image)
        self.u.mem_protect(res.LOAD, span, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
        self.u.mem_map(0x10000000, 0x2000000)
        self.heap = 0x11000000
        self.arena_requests: list[int] = []
        self.trace: collections.deque = collections.deque(maxlen=16)
        self.put(self.P, 0x1090000)
        self.put(self.P + 8, self.CTX, self.SCENE, pool_end - POOL, POOL + res.LOAD)
        self.put(self.P + 36, res.NBU_BASE + res.LOAD)
        self.put(self.P + 0x34, 0x10003004, 0x10003008, 0, 0x1000300C)
        self.put(self.CTX + 8, self.ALLOC)
        self.put(self.SCENE + 12, self.ALLOC)
        self.put(self.ALLOC, 0x10003000, 0x10003010, 0x10003014)
        for address in (0x10003000, 0x10003004, 0x10003008, 0x1000300C,
                        0x10003010, 0x10003014, 0xC05E8EF8, 0xC05D9E88):
            self.u.hook_add(uc.UC_HOOK_CODE, self.boundary, begin=address, end=address)
        self.u.hook_add(uc.UC_HOOK_CODE, lambda u, a, n, d: self.trace.append(hex(a)))

    # -- modelled boundaries -------------------------------------------------
    def put(self, address: int, *values: int) -> None:
        self.u.mem_write(address, struct.pack('<' + 'I' * len(values), *values))

    def get(self, address: int) -> int:
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def allocate(self, size: int) -> int:
        assert 0 <= size < 0x100000, size
        address = self.heap
        self.heap += (max(size, 4) + 15) & ~15
        assert self.heap < 0x11E00000, 'emulated heap exhausted'
        return address

    def boundary(self, u, address, size, data):
        r0, r1 = (u.reg_read(r) for r in (ar.UC_ARM_REG_R0, ar.UC_ARM_REG_R1))
        result = 0
        if address == 0x10003000:
            result = self.allocate(r0)
        elif address in (0x10003004, 0x10003008):
            count = 1 if address == 0x10003004 else 4
            position = self.get(r0 + 4)
            result = int.from_bytes(u.mem_read(self.get(r0 + 36) + position, count), 'big')
            self.put(r0 + 4, position + count)
        elif address == 0x1000300C:
            self.put(r0 + 4, self.get(r0 + 4) + r1)
        elif address == 0xC05E8EF8:
            self.arena_requests.append(r1)
            result = self.ALLOC
        elif address == 0xC05D9E88:
            result = 0          # component registry: sizes excluded, see docstring
        elif address == 0x10003014:
            raise RuntimeError('unexpected realloc: a declaration ran out of child slots')
        u.reg_write(ar.UC_ARM_REG_R0, result)
        u.reg_write(ar.UC_ARM_REG_PC, u.reg_read(ar.UC_ARM_REG_LR))

    # -- native calls --------------------------------------------------------
    def call(self, address: int, *args: int) -> int:
        self.u.reg_write(ar.UC_ARM_REG_FPEXC, 0x40000000)
        self.u.reg_write(ar.UC_ARM_REG_C1_C0_2, 0xF00000)
        for register, value in zip((ar.UC_ARM_REG_R0, ar.UC_ARM_REG_R1,
                                    ar.UC_ARM_REG_R2, ar.UC_ARM_REG_R3), args):
            self.u.reg_write(register, value)
        self.u.reg_write(ar.UC_ARM_REG_SP, self.SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, self.STOP)
        self.u.emu_start(address, self.STOP, count=2000000)
        assert self.u.reg_read(ar.UC_ARM_REG_PC) == self.STOP, list(self.trace)
        assert self.u.reg_read(ar.UC_ARM_REG_SP) == self.SP
        return self.u.reg_read(ar.UC_ARM_REG_R0)

    def record(self, raw: bytes) -> int:
        """Interpret one record, the way the resident's hook substitutes one."""
        self.u.mem_write(self.DATA, raw)
        base, position = self.get(self.P + 36), self.get(self.P + 4)
        self.put(self.P + 36, self.DATA - position)
        result = self.call(INTERPRET, self.P)
        assert self.get(self.P + 4) == position + len(raw), 'record not fully consumed'
        self.put(self.P + 36, base)
        assert self.get(self.P + 20) == POOL + res.LOAD, 'string pool identity changed'
        return result

    # -- observations --------------------------------------------------------
    def created(self) -> int:
        return self.get(self.P + 0x50)

    def lookup(self, identifier: int) -> int:
        return self.call(LOOKUP, self.P + 0x48, identifier)

    def children(self, identifier: int) -> int:
        return self.call(CHILDREN, self.lookup(identifier))

    def parent(self, identifier: int) -> int:
        """Owning object's id, read from the child's parent pointer at +0x1C."""
        owner = self.get(self.lookup(identifier) + 0x1C)
        return self.get(owner + 0x18) if owner else 0

    def watch_table(self, records: bytes) -> list[dict]:
        """Interpret `records`, reporting native stores past the id table."""
        limit = self.get(self.P + 0x54) + self.get(self.P + 0x48) * 4
        seen: list[dict] = []

        def observe(u, access, address, size, value, data):
            seen.append(dict(pc=hex(u.reg_read(ar.UC_ARM_REG_PC)),
                             address=hex(address), size=size))

        handle = self.u.hook_add(uc.UC_HOOK_MEM_WRITE, observe,
                                 begin=limit, end=limit + 3)
        at = 0
        while at < len(records):
            tag, size = struct.unpack_from('>II', records, at)
            if tag == DECLARATION:
                self.record(records[at:at + size])
                if seen:
                    break
            at += size
        self.u.hook_del(handle)
        return [event for event in seen if event['pc'] == hex(TABLE_STORE)]
