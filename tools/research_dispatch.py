#!/usr/bin/env python3
"""Execute fp 5.02 native request routing offline, without camera access.

OS logging, allocation, queue/wait and consumer policy/business handlers are
explicit boundaries. Original producer, copy, routing and holder cleanup execute.
This is not a scheduler simulation or authorization to send arbitrary events.
"""
import hashlib
import json
from pathlib import Path
import gc
import struct

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as ar

ROOT = Path(__file__).resolve().parent.parent
BASE = 0xC0000000
TABLE = 0xC0B9E034
RAM = 0x45000000
MANAGER, REQUEST, ALLOCATION = RAM + 0x1000, RAM + 0x2000, RAM + 0x3000
STOP = RAM + 0x8000
SP = RAM + 0xF000
SHA = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'


class Model:
    def __init__(self, image, rows):
        self.u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.u.mem_map(BASE, (len(image) + 4095) & ~4095)
        self.u.mem_write(BASE, image)
        self.u.mem_map(RAM, 0x10000)
        self.u.mem_write(MANAGER + 0x128, struct.pack('<II', 77, 88))
        self.receive_result = 0
        self.receive_timeout = 0xFFFFFFFF
        self.cleanup_holders = []
        self.rows = rows
        self.policy = 1
        self.queue_result = 0
        self.wait_result = 0
        self.allocate = True
        self.trace = []
        self.queued = None
        self.assertion = False
        self.handlers = {row[3] for row in rows if row[2]}
        self.u.hook_add(UC_HOOK_CODE, self.hook)
        gc.collect()

    def reg(self, n):
        return self.u.reg_read(getattr(ar, f'UC_ARM_REG_R{n}'))

    def ret(self, value=0):
        self.u.reg_write(ar.UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.u.reg_write(ar.UC_ARM_REG_PC, self.u.reg_read(ar.UC_ARM_REG_LR))

    def hook(self, u, address, size, _):
        if address == STOP:
            u.emu_stop()
        elif address in (0xC0019DC0, 0xC001A048):
            self.ret()
        elif address == 0xC0011100:
            self.assertion = True
            u.emu_stop()
        elif address == 0xC0015058:
            u.mem_write(self.reg(0), bytes([self.reg(1) & 255]) * self.reg(2))
            self.ret(self.reg(0))
        elif address == 0xC03A3BF8:
            assert self.reg(0) == 0xC0
            self.trace.append('allocate')
            self.ret(ALLOCATION if self.allocate else 0)
        elif address == 0xC036D948:
            assert self.reg(0) == MANAGER + 0x128
            assert self.reg(2) == ALLOCATION
            self.queued = bytes(u.mem_read(ALLOCATION, 0xC0))
            self.trace.append('queue')
        elif address == 0xC036D818:
            assert self.reg(0) == MANAGER + 0x12C
            assert self.reg(1) == 1 and self.reg(2) == 0x21
            assert struct.unpack('<I', u.mem_read(u.reg_read(ar.UC_ARM_REG_SP), 4))[0] == 0xFFFFFFFF
            self.trace.append('wait')
        elif address == 0xC0016C08:
            assert self.reg(0) == 88
            self.ret(self.wait_result)
        elif address == 0xC00011D0:
            selector = u.reg_read(ar.UC_ARM_REG_R12)
            assert self.reg(0) == 77
            if selector == 0x80280200:
                assert self.reg(1) == ALLOCATION
                self.ret(self.queue_result)
            elif selector == 0x80290300:
                assert self.reg(2) == self.receive_timeout
                if self.receive_result == 0:
                    u.mem_write(self.reg(1), struct.pack('<I', ALLOCATION))
                self.ret(self.receive_result)
            else:
                raise AssertionError(f'unmodeled syscall {selector:#x}')
        elif address == 0xC03A3B40:
            self.cleanup_holders.append(struct.unpack('<I', u.mem_read(self.reg(0), 4))[0])
        elif address == 0xC03A2438:
            self.trace.append('consumer_policy')
            self.ret(self.policy)
        elif address in self.handlers:
            assert self.reg(0) == MANAGER and self.reg(1) == REQUEST
            self.trace.append(hex(address))
            self.ret(0x1234)

    def call(self, entry, event, asynchronous=1):
        request = bytearray((i * 7) & 255 for i in range(0xBC))
        struct.pack_into('<I', request, 0, event)
        request[4] = asynchronous
        self.u.mem_write(REQUEST, bytes(request))
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        self.u.reg_write(ar.UC_ARM_REG_R0, MANAGER)
        self.u.reg_write(ar.UC_ARM_REG_R1, REQUEST)
        self.u.emu_start(entry, STOP, count=10000)
        assert self.assertion or self.u.reg_read(ar.UC_ARM_REG_PC) == STOP, 'execution did not finish'
        if not self.assertion:
            assert self.u.reg_read(ar.UC_ARM_REG_SP) == SP
        return self.reg(0), bytes(request)


def main():
    image = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest() == SHA
    rows = [struct.unpack_from('<4I', image, TABLE - BASE + i * 16) for i in range(51)]
    assert [r[0] for r in rows] == list(range(51))
    assert all(r[1] == 0 and r[2] in (0, 0xFFFFFFFF) for r in rows)
    observations = []
    for event, delta, kind, target in rows:
        for allowed in (0, 1):
            m = Model(image, rows)
            m.policy = allowed
            m.call(0xC03A3398, event)
            expected = (['consumer_policy'] + ([hex(target)] if allowed else [])) if kind else []
            assert m.trace == expected
    observations.append('All 51 stock rows: disabled entries skip policy/handler; enabled entries obey consumer policy and select exact table target')
    for event in (0, 16, 37, 40, 44, 47, 48, 49, 50):
        m = Model(image, rows)
        value, _ = m.call(0xC03A0798, event)
        assert value == 0 and not m.trace
    observations.append('Disabled producer requests return zero without allocation or queue submission')
    for asynchronous, queue_result, wait_result in ((1, 0, 0), (1, -5, 0), (0, 0, 0), (0, 0, -5), (0, -5, -50)):
        m = Model(image, rows)
        m.queue_result, m.wait_result = queue_result, wait_result
        value, request = m.call(0xC03A0798, 0x21, asynchronous)
        assert m.queued == bytes(4) + request
        assert m.trace == ['allocate', 'queue'] + ([] if asynchronous else ['wait'])
        expected = int(queue_result == 0) if asynchronous else int(wait_result == 0)
        assert value == expected
        assert m.cleanup_holders == [0], 'sender no longer owns the envelope after send attempt'
    observations.append('Native queue wrapper maps OS success to 1 and failure to 0; producer copies 0xBC bytes; synchronous requests still wait after failed send; sender holder is null on cleanup')
    for os_result, expected in ((0, 0), (-50, 1), (-25, 3), (-5, 2)):
        m = Model(image, rows)
        m.receive_result = os_result
        sentinel = 0x12345678
        m.u.mem_write(REQUEST, struct.pack('<I', sentinel))
        m.u.mem_write(SP, struct.pack('<I', m.receive_timeout))
        m.u.reg_write(ar.UC_ARM_REG_SP, SP)
        m.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        for n, v in enumerate((MANAGER + 0x128, 77, REQUEST, 0)):
            m.u.reg_write(getattr(ar, f'UC_ARM_REG_R{n}'), v)
        m.u.emu_start(0xC036D9C0, STOP, count=1000)
        assert m.reg(0) == expected and m.u.reg_read(ar.UC_ARM_REG_PC) == STOP
        assert m.u.reg_read(ar.UC_ARM_REG_SP) == SP
        assert struct.unpack('<I', m.u.mem_read(REQUEST, 4))[0] == (ALLOCATION if os_result == 0 else sentinel)
    observations.append('Native receive translates OS 0/-50/-25/other into 0/1/3/2 and forwards caller timeout to OS')
    for event, allocation in ((51, True), (0x21, False)):
        m = Model(image, rows)
        m.allocate = allocation
        m.call(0xC03A0798, event)
        assert m.assertion and 'queue' not in m.trace
    observations.append('Out-of-table event 51 and allocation failure reach firmware assertion boundary; no graceful error contract')
    # Execute the actual property-domain validator, stopping before the firmware
    # assertion handler. This catches a dangerous earlier no-validation inference.
    for value, count, expected in ((99, 1, 0), (2, 3, 2), (0xFFFF, 3, 0xFFFF), (3, 3, None), (0, 0, None)):
        m = Model(image, rows)
        m.u.reg_write(ar.UC_ARM_REG_SP, SP)
        m.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        for n, v in enumerate((MANAGER, value, count, 0x80000002)):
            m.u.reg_write(getattr(ar, f'UC_ARM_REG_R{n}'), v)
        m.u.emu_start(0xC00913F8, STOP, count=1000)
        assert m.assertion == (expected is None)
        if expected is not None:
            assert m.reg(0) == expected and m.u.reg_read(ar.UC_ARM_REG_PC) == STOP
    observations.append('Property validator normalizes count-one domains to zero; otherwise accepts in-range or 0xFFFF, asserts on invalid domain/index')
    out = ROOT / 'builds/dispatch-research'
    out.mkdir(parents=True, exist_ok=True)
    report = {'firmware_sha256': SHA, 'observations': observations,
              'event_table': [{'event': e, 'this_adjustment': d, 'dispatch_kind': hex(k), 'handler': hex(t)} for e, d, k, t in rows],
              'limits': ['No live camera access', 'Consumer policy and business handlers stubbed',
                         'OS syscall results, logger and allocator modeled; native queue/wait wrappers execute',
                         'No deep-copy or payload pointer lifetime guarantee',
                         'No recording safety, concurrency or task scheduling proof',
                         'Event IDs are local to this manager, not global firmware IDs']}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    cs = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    ranges = [(0xC03A0798, 0xC03A096C), (0xC03A3398, 0xC03A3440),
              (0xC03A34A8, 0xC03A35C8), (0xC00913F8, 0xC009148C),
              (0xC036D948, 0xC036DA48), (0xC036D818, 0xC036D880)]
    lines = [f'{i.address:08x}: {i.mnemonic:8} {i.op_str}' for a, b in ranges for i in cs.disasm(image[a-BASE:b-BASE], a)]
    (out / 'disassembly.txt').write_text('\n'.join(lines) + '\n')
    for observation in observations:
        print('PASS:', observation)
    print('Report:', out / 'report.json')
    print('OFFLINE ONLY: modeled boundaries are listed in report.json')


if __name__ == '__main__':
    main()
