#!/usr/bin/env python3
"""Recover and execute the fp 5.02 indexed XC_ThreadImpl constructor offline.

No task is created on hardware. OS task creation/start/exit and libc boundaries
are modeled. Stock task reservations are not safe stack sizes for new tasks.
"""
import hashlib
import json
import struct
from research_dispatch import Model, ROOT, BASE, RAM, SP, STOP, SHA
from unicorn import arm_const as ar

TABLE = 0xC0B97F90
OBJECT, BODY = RAM + 0x4000, RAM + 0x5000


class TaskModel(Model):
    def __init__(self, image):
        super().__init__(image, [])
        self.descriptor = None
        self.started = None
        self.entered = False

    def hook(self, u, address, size, _):
        if address == 0xC0013CF8:
            u.mem_write(self.reg(0), bytes(u.mem_read(self.reg(1), self.reg(2))))
            self.ret(self.reg(0))
        elif address == 0xC0013DBC:
            self.ret(len(bytes(u.mem_read(self.reg(0), 32)).split(b'\0', 1)[0]))
        elif address == 0xC0016A58:
            self.descriptor = bytes(u.mem_read(self.reg(0), 28))
            self.ret(77)
        elif address == 0xC0016BC0:
            self.started = [self.reg(0), self.reg(1)]
            self.ret()
        elif address == 0xC03A34A8:
            assert self.reg(0) == BODY
            self.entered = True
            self.ret()
        elif address == 0xC01F8A54:
            # The OS task-exit primitive does not normally return.
            u.emu_stop()
        else:
            super().hook(u, address, size, _)

    def invoke(self, entry, r0, r1):
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        self.u.reg_write(ar.UC_ARM_REG_R0, r0)
        self.u.reg_write(ar.UC_ARM_REG_R1, r1)
        self.u.emu_start(entry, STOP, count=20000)
        assert self.assertion or self.u.reg_read(ar.UC_ARM_REG_PC) in (STOP, 0xC01F8A54)


def main():
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest() == SHA
    rows = []
    for index in range(44):
        row = image[TABLE-BASE+index*32:TABLE-BASE+(index+1)*32]
        enabled = row[0] != 0
        priority, stack = struct.unpack_from('<II', row, 12)
        name = row[20:28].split(b'\0', 1)[0].decode('ascii')
        m = TaskModel(image)
        m.invoke(0xC036ECA8, OBJECT, index)
        assert m.assertion == (not enabled)
        if enabled:
            exinf, attributes, entry, actual_priority, actual_stack = struct.unpack_from('<5I', m.descriptor)
            assert (exinf, attributes, entry) == (OBJECT + 4, 0x41, 0xC036EE50)
            assert (actual_priority, actual_stack) == (priority, stack)
            assert m.descriptor[20:28] == row[20:28]
        rows.append({'index': index, 'enabled': enabled, 'name': name, 'priority': priority, 'stack_bytes': stack})
    m = TaskModel(image)
    m.invoke(0xC036ECA8, OBJECT, 44)
    assert m.assertion and m.descriptor is None
    m = TaskModel(image)
    m.invoke(0xC036ECA8, OBJECT, 0)
    m.u.mem_write(BODY, struct.pack('<I', 0xC0B9DD4C))
    m.invoke(0xC036EE08, OBJECT, BODY)
    assert m.started == [77, OBJECT]
    m.invoke(0xC036EE50, OBJECT, 0)
    assert m.entered
    out = ROOT / 'builds/dispatch-research'
    out.mkdir(parents=True, exist_ok=True)
    report = {'firmware_sha256': SHA, 'table': hex(TABLE), 'tasks': rows,
              'verified': ['All 44 constructor indices against stock definitions', 'Index 44 asserts before task creation', 'RecPMgr index0 body reaches original rec-manager vtable worker slot'],
              'limits': ['OS create/start/exit and libc modeled', 'Worker body is an observation boundary, not a scheduler simulation', 'Stock stack allocations do not authorize custom task stack changes']}
    (out / 'tasks.json').write_text(json.dumps(report, indent=2) + '\n')
    for row in rows:
        print(f"{row['index']:2} {row['name']:8} enabled={row['enabled']} priority={row['priority']:2} stack={row['stack_bytes']:#x}")
    print('PASS: original indexed task constructor and rec-manager worker binding; offline only')
    print('Report:', out / 'tasks.json')


if __name__ == '__main__':
    main()
