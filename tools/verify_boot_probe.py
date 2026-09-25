#!/usr/bin/env python3
"""Execute the actual boot-probe installer, remover, callbacks and native gate.

Offline only. Unchanged upstream loader/stage2 ARM executes; allocator, cache,
clock, subsystem initialization and native file-reader boundaries are modeled.
The native observer initializer and readiness gate execute original firmware.
No USB/device access. This is not hardware retention or SD timing evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct

import unicorn as uc
from unicorn import arm_const as ar

from build_boot_probe import (
    BASE, CAVE_BEGIN, CAVE_END, INIT_ORIG, INIT_SITE, LOADER, MAIN, MAIN_SHA,
    READ_ORIG, READ_SITE, ROOT, STATE, branch, build_card, container_sections,
    firmware_bytes,
)

STOP, SP, SUBJECT = 0x10000000, 0x1000FFF0, 0x51000000
READER_RETURN = STOP + 0x100
OLD_HEAP, OLD_HEAP_SIZE = 0x48000000, 0x10000
H_GET, H_ADDR, H_FREE = 0xC001D740, 0xC001D7F0, 0xC001D7A0
DCACHE, ICACHE = 0xC000E91C, 0xC000EABC
F_MGR, F_VOL = 0xC0444658, 0xC0444698
F_CTOR, F_OPEN, F_READ = 0xC0365E90, 0xC0365FB0, 0xC0366060
F_CLOSE, F_DTOR, TICK = 0xC0366020, 0xC0365ED0, 0xC002B6E0
SHELL_INIT, GUARD, GUARD_DONE = 0xC03DA178, 0xC0013090, 0xC00131E4
GET_SUBJECT, REGISTER, NATIVE_BOOT, NATIVE_GATE = (
    0xC0366A98, 0xC02DE968, 0xC0020B70, 0xC03DA3F8)
SERVICES = {H_GET, H_ADDR, H_FREE, DCACHE, ICACHE, F_MGR, F_VOL,
            F_CTOR, F_OPEN, F_READ, F_CLOSE, F_DTOR, TICK}
NATIVE_SERVICES = {SHELL_INIT, GUARD, GUARD_DONE, GET_SUBJECT, REGISTER}
REGS = [getattr(ar, f'UC_ARM_REG_R{i}') for i in range(13)]
CALL_CPSR, IRQ_FIQ_MASK = 0xA80F0013, 0xC0
SNAPSHOT_REGS = REGS + [ar.UC_ARM_REG_SP, ar.UC_ARM_REG_LR, ar.UC_ARM_REG_CPSR]
SAVED = REGS[4:12]


def signed(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


class ProbeMachine:
    """Run emitted instructions with strict memory and call-boundary checks."""

    def __init__(self, firmware: bytes, install: Path):
        self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        self.u.mem_map(BASE, (len(firmware) + 4095) & ~4095)
        self.u.mem_write(BASE, firmware)
        self.u.mem_map(STOP, 0x10000)
        self.u.mem_map(SUBJECT, 0x1000)
        self.u.mem_map(0xC3756000, 0x2000)
        # Deliberately make an old heap allocation inaccessible before ANY
        # retained callback is executed; no pointer into it can pass by luck.
        self.u.mem_map(OLD_HEAP, OLD_HEAP_SIZE)
        self.u.mem_write(OLD_HEAP, b'\xA5' * OLD_HEAP_SIZE)
        self.u.mem_unmap(OLD_HEAP, OLD_HEAP_SIZE)
        self.manifest = json.loads((install / 'manifest.json').read_text())
        self.resident = (install / 'resident.bin').read_bytes()
        self.callbacks = self.manifest['callbacks']
        self.code_end = CAVE_BEGIN + self.manifest['code_bytes']
        self.next_heap = 0x45000000
        self.blocks = []
        self.descriptors = {}
        self.entry = None
        self.entry_return = None
        self.entry_cache = []
        self.entry_active = False
        self.pending_callback = None
        self.callback_count = 0
        self.reader_count = 0
        self.reader_load = False
        self.reader_frame = None
        self.leaf_original = False
        self.results = []
        self.writes = []
        self.cache_calls = []
        self.blob = b''
        self.opened = self.closed = False
        self.u.hook_add(uc.UC_HOOK_CODE, self.execute)
        self.u.hook_add(uc.UC_HOOK_MEM_WRITE, self.check_write)
        self.u.hook_add(uc.UC_HOOK_MEM_READ, self.check_read)

    def get(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def put(self, address, *values):
        self.u.mem_write(address, struct.pack('<' + 'I' * len(values),
                                            *(v & 0xFFFFFFFF for v in values)))
        self.u.ctl_remove_cache(address, address + 4 * len(values))

    def snapshot(self):
        return tuple(self.u.reg_read(reg) for reg in SNAPSHOT_REGS)

    def markers(self):
        return list(struct.unpack('<4I', self.u.mem_read(STATE, 16)))

    def protected(self):
        return (bytes(self.u.mem_read(CAVE_BEGIN, CAVE_END - CAVE_BEGIN)),
                self.get(INIT_SITE), self.get(READ_SITE))

    def owned(self):
        return (branch(INIT_SITE, self.callbacks['probe_init']),
                branch(READ_SITE, self.callbacks['probe_reader']))

    def check_read(self, u, access, address, size, value, _):
        marker = STATE <= address and address + size <= STATE + 16
        protected = (CAVE_BEGIN <= address < CAVE_END
                     or address in (INIT_SITE, READ_SITE))
        if self.pending_callback is not None:
            assert (STOP <= address and address + size <= SP
                    or CAVE_BEGIN <= address and address + size <= self.code_end
                    or marker), ('callback reads outside resident image/stack', hex(address))
        if self.entry_active and protected or self.pending_callback is not None and marker:
            assert u.reg_read(ar.UC_ARM_REG_CPSR) & IRQ_FIQ_MASK == IRQ_FIQ_MASK, (
                'ownership/marker read exposed to local interrupt dispatch', hex(address))

    def check_write(self, u, access, address, size, value, _):
        if CAVE_BEGIN <= address < CAVE_END or address in (INIT_SITE, READ_SITE):
            self.writes.append((address, size, value))
            if self.entry_active or self.pending_callback is not None:
                assert u.reg_read(ar.UC_ARM_REG_CPSR) & IRQ_FIQ_MASK == IRQ_FIQ_MASK, (
                    'partial probe transaction exposed to local interrupt dispatch', hex(address))
        if self.pending_callback is not None:
            assert ((STOP <= address and address + size <= SP)
                    or STATE <= address and address + size <= STATE + 16), (
                        'callback writes outside markers/stack', hex(address))
        if self.entry_active:
            assert ((STOP <= address and address + size <= SP)
                    or CAVE_BEGIN <= address and address + size <= CAVE_END
                    or address in (INIT_SITE, READ_SITE) and size == 4), (
                        'installer writes outside stack/owned locations', hex(address))
            if address in (INIT_SITE, READ_SITE):
                original = branch(address, INIT_ORIG if address == INIT_SITE else READ_ORIG)
                if value != original:
                    assert self.entry_cache == [DCACHE, ICACHE], 'hook armed before code publication'
                    assert bytes(u.mem_read(CAVE_BEGIN, len(self.resident))) == self.resident
        # Firmware writes from upstream loader/stage2 are bounded too. This
        # excludes CommonSave, flash programming, and the DRAM retention word.
        if BASE <= address < BASE + 0x02000000:
            assert (CAVE_BEGIN <= address and address + size <= CAVE_END
                    or address in (INIT_SITE, READ_SITE, 0xC072E060, 0xC072F6F8)
                    or 0xC072F6D8 <= address and address + size <= 0xC072F6E8), (
                        'unexpected firmware-image mutation', hex(address))
        for block in self.blocks:
            if not block['freed'] and block['base'] <= address < block['base'] + block['span']:
                assert address + size <= block['base'] + block['size'], 'allocation overflow'

    def return_boundary(self, result=0, *, clobber=True):
        if clobber:
            for reg in (ar.UC_ARM_REG_R1, ar.UC_ARM_REG_R2, ar.UC_ARM_REG_R3, ar.UC_ARM_REG_IP):
                self.u.reg_write(reg, 0xD00DFEED)
        self.u.reg_write(ar.UC_ARM_REG_R0, result)
        self.u.reg_write(ar.UC_ARM_REG_PC, self.u.reg_read(ar.UC_ARM_REG_LR))

    def execute(self, u, address, size, _):
        if address == READER_RETURN:
            assert self.reader_frame is not None
            sp, lr = self.reader_frame
            self.reader_frame = None
            u.reg_write(ar.UC_ARM_REG_SP, sp)
            u.reg_write(ar.UC_ARM_REG_LR, lr)
            self.return_boundary(1)
            return
        if address == self.entry:
            assert not self.entry_active
            self.entry_active = True
            self.entry_return = u.reg_read(ar.UC_ARM_REG_LR)
            self.entry_saved = [u.reg_read(r) for r in SAVED]
            self.entry_sp = u.reg_read(ar.UC_ARM_REG_SP)
            self.entry_cpsr = u.reg_read(ar.UC_ARM_REG_CPSR)
            self.entry_cache = []
        elif self.entry_active and address == self.entry_return:
            assert u.reg_read(ar.UC_ARM_REG_SP) == self.entry_sp
            assert [u.reg_read(r) for r in SAVED] == self.entry_saved
            assert u.reg_read(ar.UC_ARM_REG_CPSR) == self.entry_cpsr, (
                'installer/remover changed caller flags or interrupt control')
            self.entry_active = False
            self.results.append(signed(u.reg_read(ar.UC_ARM_REG_R0)))

        if address in self.callbacks.values():
            assert self.pending_callback is None
            assert not self.entry_active, 'installer unexpectedly executes a callback'
            target = INIT_ORIG if address == self.callbacks['probe_init'] else READ_ORIG
            self.pending_callback = (target, self.snapshot())
            self.callback_count += 1
        if self.pending_callback is not None:
            target, before = self.pending_callback
            if address == target:
                assert self.snapshot() == before, 'callback changed registers/arguments/LR/SP/CPSR'
                self.pending_callback = None
            else:
                assert CAVE_BEGIN <= address < self.code_end, 'callback left resident code'
                return

        if address in (INIT_ORIG, READ_ORIG) and self.leaf_original:
            self.return_boundary(0x76543210, clobber=False)
            return
        if address == READ_ORIG:
            assert bytes(u.mem_read(u.reg_read(ar.UC_ARM_REG_R0), 13)).split(b'\0')[0] == b'\\AutoRun.txt'
            assert u.reg_read(ar.UC_ARM_REG_R1) == 1
            self.reader_count += 1
            if self.reader_load:
                self.reader_load = False
                # Model the omitted reader/interpreter stack frame, including
                # its aligned call into the real loader. Restore the original
                # reader LR/SP when that loader returns; no emulator recursion.
                assert self.reader_frame is None
                sp, lr = u.reg_read(ar.UC_ARM_REG_SP), u.reg_read(ar.UC_ARM_REG_LR)
                self.reader_frame = (sp, lr)
                u.reg_write(ar.UC_ARM_REG_SP, sp & ~7)
                u.reg_write(ar.UC_ARM_REG_LR, READER_RETURN)
                u.reg_write(ar.UC_ARM_REG_PC, LOADER)
            else:
                self.return_boundary(1)
            return
        if address in NATIVE_SERVICES:
            r0, r1 = u.reg_read(ar.UC_ARM_REG_R0), u.reg_read(ar.UC_ARM_REG_R1)
            result = 1
            if address == GET_SUBJECT:
                assert r0 == 1
                result = SUBJECT
            elif address == REGISTER:
                assert (r0, r1) == (SUBJECT, 0xC3756940)
            self.return_boundary(result)
            return
        if address not in SERVICES:
            allowed = (LOADER <= address < LOADER + 0x200
                       or NATIVE_BOOT <= address < NATIVE_BOOT + 16
                       or NATIVE_GATE <= address < INIT_ORIG + 0x68
                       or CAVE_BEGIN <= address < self.code_end
                       or address in (INIT_SITE, READ_SITE))
            assert allowed or any(not b['freed'] and b['base'] <= address < b['base'] + b['size']
                                  for b in self.blocks), f'unexpected execution {address:#x}'
            return

        assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0, 'unaligned modeled service call'
        r0, r1, r2, r3 = [u.reg_read(r) for r in REGS[:4]]
        if self.entry_active:
            assert address in (DCACHE, ICACHE), 'probe installer invokes a forbidden service'
            assert u.reg_read(ar.UC_ARM_REG_CPSR) & IRQ_FIQ_MASK == IRQ_FIQ_MASK, (
                'local interrupt dispatch enabled before final cache publication')
        result = 0
        if address == H_GET:
            assert (r1, r2, r3) == (0, 0x10000, 0), 'unexpected resident allocation'
            assert not any(not b['freed'] for b in self.blocks), 'overlapping loader runs'
            span = (r2 + 4095) & ~4095
            block = dict(base=self.next_heap, size=r2, span=span, freed=False)
            self.next_heap += span + 0x1000
            u.mem_map(block['base'], span)
            self.blocks.append(block)
            self.descriptors[r0] = block
            self.opened = self.closed = False
        elif address == H_ADDR:
            result = self.descriptors[r0]['base']
        elif address == H_FREE:
            block = self.descriptors[r0]
            assert not block['freed'] and self.closed and r1 == 2
            assert not self.entry_active and self.pending_callback is None
            u.mem_unmap(block['base'], block['span'])
            block['freed'] = True
            self.entry = None
        elif address in (DCACHE, ICACHE):
            self.cache_calls.append(address)
            if self.entry_active:
                self.entry_cache.append(address)
            if address == ICACHE:
                u.ctl_remove_cache(INIT_SITE, INIT_SITE + 4)
                u.ctl_remove_cache(READ_SITE, READ_SITE + 4)
                u.ctl_remove_cache(CAVE_BEGIN, CAVE_END)
        elif address == F_MGR:
            result = 0x20000000
        elif address == F_VOL:
            result = 1
        elif address == F_OPEN:
            assert bytes(u.mem_read(r1, 32)).split(b'\0')[0] == b'\\fpSup.BIN' and r2 == 1
            self.opened = True
            result = 1
        elif address == F_READ:
            assert self.opened and not self.closed and len(self.blob) <= r2
            block = self.blocks[-1]
            assert block['base'] <= r1 and r1 + r2 <= block['base'] + block['size']
            u.mem_write(r1, self.blob)
            self.entry = r1 + self.payload_offset
            result = len(self.blob)
        elif address == F_CLOSE:
            assert self.opened and not self.closed
            self.closed = True
        elif address == TICK:
            result = 123456
        self.return_boundary(result)

    def prepare_card(self, card):
        self.blob = (card / 'fpSup.BIN').read_bytes()
        payload = (card / 'boot_probe.bin').read_bytes()
        _, sections = container_sections(self.blob)
        cursor = 16 + 8 * len(sections)
        offsets = []
        for destination, data in sections:
            if destination == 0 and data == payload:
                offsets.append(cursor)
            cursor += len(data)
        assert len(offsets) == 1
        self.payload_offset = offsets[0]
        for address, value in re.findall(r'^mem set (0x[0-9A-Fa-f]+) (0x[0-9A-Fa-f]+)$',
                                         (card / 'AutoRun.txt').read_text(), re.M):
            at = int(address, 16)
            if LOADER <= at < LOADER + 0x200:
                self.put(at, int(value, 16))

    def call(self, address, *args, stop=STOP, sp=SP, cpsr=CALL_CPSR):
        self.u.reg_write(ar.UC_ARM_REG_CPSR, cpsr)
        self.u.reg_write(ar.UC_ARM_REG_SP, sp)
        self.u.reg_write(ar.UC_ARM_REG_LR, stop)
        for index, reg in enumerate(REGS):
            self.u.reg_write(reg, 0x12340000 + index)
        for index, value in enumerate(args):
            self.u.reg_write(REGS[index], value)
        saved = [self.u.reg_read(r) for r in SAVED]
        self.u.emu_start(address, stop, count=200000)
        assert self.u.reg_read(ar.UC_ARM_REG_PC) == stop, 'execution did not return'
        assert self.u.reg_read(ar.UC_ARM_REG_SP) == sp, 'unbalanced stack'
        assert [self.u.reg_read(r) for r in SAVED] == saved, 'callee-saved register corruption'
        assert self.pending_callback is None and not self.entry_active
        return self.u.reg_read(ar.UC_ARM_REG_R0)

    def load(self, card, result, *, sp=SP, cpsr=CALL_CPSR):
        self.prepare_card(card)
        count = len(self.results)
        self.call(LOADER, sp=sp, cpsr=cpsr)
        assert self.results[count:] == [result], self.results[count:]
        assert all(block['freed'] for block in self.blocks), 'temporary staging survives loader'

    def native_boot(self, flags, *, card=None, result=None):
        # Modeled warm boundary clears boot BSS but retains the firmware image.
        # Old staging/heap mappings have already been removed, not just zeroed.
        assert all(block['freed'] for block in self.blocks)
        self.u.mem_write(0xC3756000, bytes(0x2000))
        self.u.mem_write(SUBJECT + 0x1C, bytes((flags[0], 0, 0, flags[1])))
        if card is not None:
            self.prepare_card(card)
        self.reader_load = card is not None
        readers, entries = self.reader_count, len(self.results)
        self.call(NATIVE_BOOT)
        expected = int(bool(flags[0] & flags[1]))
        assert self.reader_count - readers == expected, 'native gate behavior changed'
        assert self.results[entries:] == ([result] if card is not None and expected else [])
        assert all(block['freed'] for block in self.blocks)
        self.reader_load = False


def lifecycle(firmware, install, remove):
    m = ProbeMachine(firmware, install)
    # Cold installation really happens INSIDE the modeled AutoRun reader.
    m.native_boot((1, 1), card=install, result=0)
    assert m.markers() == [0, 0, 0, 0], 'installation invents pre-install visits'
    assert m.protected()[1:] == m.owned()
    assert m.entry_cache == [DCACHE, ICACHE, DCACHE, ICACHE]
    writes = len(m.writes)
    m.native_boot((1, 1), card=install, result=1)
    assert m.markers() == [1, 1, 1, 1]
    assert all(STATE <= at < STATE + 16 for at, _, _ in m.writes[writes:]), 'reinstall rewrote evidence'
    assert m.entry_cache == [], 'preserving installer unexpectedly publishes/mutates'
    for sequence, flags in enumerate(((0, 0), (1, 0), (0, 1)), 2):
        m.native_boot(flags)
        assert m.markers() == [sequence, sequence, 1, 1]
    # A later observer notification can read without a new initialization.
    m.u.mem_write(SUBJECT + 0x1C, b'\x01\0\0\x01')
    m.call(NATIVE_GATE, 0xC3756940, SUBJECT + 0x1C)
    assert m.markers() == [4, 4, 2, 4]
    writes = len(m.writes)
    m.native_boot((1, 1), card=remove, result=0)
    assert m.markers() == [5, 5, 3, 5], 'remover cleared the active reader markers'
    assert m.protected()[1:] == (branch(INIT_SITE, INIT_ORIG), branch(READ_SITE, READ_ORIG))
    assert m.entry_cache == [DCACHE, ICACHE]
    assert all(at in (INIT_SITE, READ_SITE) or STATE <= at < STATE + 16
               for at, _, _ in m.writes[writes:])
    retained = m.protected()
    m.native_boot((1, 1))
    assert m.protected() == retained, 'removed native path still touches markers'
    writes = len(m.writes)
    m.load(remove, 1)
    assert m.protected() == retained and len(m.writes) == writes
    m.load(install, -3)  # Removed evidence is not silently reinitialized.
    assert m.protected() == retained and len(m.writes) == writes
    return {'cold_markers': [0, 0, 0, 0], 'warm_reinstall_markers': [1, 1, 1, 1],
            'removed_markers': m.markers(), 'native_gate_inputs': [[1, 1], [0, 0], [1, 0], [0, 1]],
            'staging_allocations_unmapped': len(m.blocks), 'old_heap_unmapped': True,
            'callback_register_snapshots_checked': m.callback_count}


def callback_invariants(firmware, install):
    m = ProbeMachine(firmware, install)
    m.load(install, 0)
    m.leaf_original = True
    m.put(STATE, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 123)
    # Exercise both actual BL sites with eight-aligned and four-aligned native
    # incoming stacks. Neither leaf may introduce an ABI call alignment change.
    cases = []
    for site in (INIT_SITE, READ_SITE):
        for offset in (0, 4):
            sp = SP - 0x100 - offset
            args = bytes(range(32))
            m.u.mem_write(sp, args)
            result = m.call(site, 0x01020304, 0x11223344, 0x55667788, 0x99AABBCC,
                            stop=site + 4, sp=sp)
            assert result == 0x76543210, 'original return value lost'
            assert m.u.reg_read(ar.UC_ARM_REG_LR) == site + 4, 'BL return link changed'
            assert bytes(m.u.mem_read(sp, len(args))) == args, 'stack arguments changed'
            assert m.u.reg_read(ar.UC_ARM_REG_CPSR) == CALL_CPSR, 'callback changed flags'
            cases.append({'site': hex(site), 'incoming_sp_mod8': sp % 8})
    assert m.markers() == [1, 1, 1, 1], 'counter wrap/last sequence propagation differs'
    return {'cases': cases, 'wrapped_markers': m.markers()}


def local_interrupt_atomicity(firmware, install, remove):
    """Every probe transaction excludes local dispatch and restores its caller."""
    cases = []
    for mask in (0, 0x40, 0x80, IRQ_FIQ_MASK):
        m = ProbeMachine(firmware, install)
        cpsr = CALL_CPSR | mask
        m.load(install, 0, cpsr=cpsr)
        m.leaf_original = True
        for site in (INIT_SITE, READ_SITE):
            for offset in (0, 4):
                m.call(site, stop=site + 4, sp=SP - 0x100 - offset, cpsr=cpsr)
                assert m.u.reg_read(ar.UC_ARM_REG_CPSR) == cpsr
        assert m.markers() == [2, 2, 2, 2]
        retained = m.protected()[0]
        m.load(install, 1, cpsr=cpsr)
        m.load(remove, 0, cpsr=cpsr)
        m.load(remove, 1, cpsr=cpsr)
        m.load(install, -3, cpsr=cpsr)
        assert m.protected()[0] == retained
        m.put(INIT_SITE, 0xEA000000)
        m.load(install, -8, cpsr=cpsr)
        m.load(remove, -8, cpsr=cpsr)
        cases.append({'incoming_irq_fiq_mask': mask, 'callback_sp_mod8': [0, 4]})
    return cases


def refusal_cases(firmware, install, remove):
    results = []

    def attempt(name, card, change, expected, *, installed=True):
        m = ProbeMachine(firmware, install)
        if installed:
            m.load(install, 0)
            m.put(STATE, 19, 20, 21, 18)
        change(m)
        before, writes = m.protected(), len(m.writes)
        m.load(card, expected)
        assert m.protected() == before, f'{name}: refusal changed hooks/arena'
        assert len(m.writes) == writes, f'{name}: same-value mutation on refusal'
        assert m.entry_cache == [], f'{name}: refusal unexpectedly cache-published'
        results.append(name)

    stock = (branch(INIT_SITE, INIT_ORIG), branch(READ_SITE, READ_ORIG))
    for first, second in (('stock', 'owned'), ('owned', 'stock'),
                          ('unknown', 'owned'), ('owned', 'unknown'),
                          ('unknown', 'stock'), ('stock', 'unknown'),
                          ('unknown', 'unknown')):
        def changed(m, first=first, second=second):
            owned = m.owned()
            values = [dict(stock=stock[n], owned=owned[n], unknown=0xEA000000)[kind]
                      for n, kind in enumerate((first, second))]
            m.put(INIT_SITE, values[0])
            m.put(READ_SITE, values[1])
        attempt(f'install {first}/{second}', install, changed, -8)
    symbols = json.loads((install / 'manifest.json').read_text())['symbols']
    init_target = CAVE_BEGIN + symbols['init_target'] - symbols['probe_image']
    for at, why in ((CAVE_BEGIN, 'different callback code'),
                    (init_target, 'different native tail target'),
                    (STATE - 4, 'unknown padding'), (CAVE_END - 4, 'unknown arena tail')):
        attempt(f'install {why}', install, lambda m, at=at: m.put(at, m.get(at) ^ 1), -3)
    for at in (CAVE_BEGIN, STATE, CAVE_END - 4):
        attempt(f'first install occupied {at:#x}', install,
                lambda m, at=at: m.put(at, 0xBAD0CAFE), -3, installed=False)
    for site in (INIT_SITE, READ_SITE):
        attempt(f'remove unknown site {site:#x}', remove,
                lambda m, site=site: m.put(site, 0xEA000000), -8)
    attempt('remove differing code', remove,
            lambda m: m.put(CAVE_BEGIN, m.get(CAVE_BEGIN) ^ 1), -3)
    attempt('remove already stock with unknown cave', remove,
            lambda m: m.put(CAVE_BEGIN, 0xBAD0CAFE), 1, installed=False)

    # A known partial removal may finish, but it must not even rewrite a site
    # already stock, and no marker/image byte can be cleared while doing so.
    for already_stock in (INIT_SITE, READ_SITE):
        m = ProbeMachine(firmware, install)
        m.load(install, 0)
        m.put(STATE, 8, 9, 10, 7)
        m.put(already_stock, branch(already_stock,
                                   INIT_ORIG if already_stock == INIT_SITE else READ_ORIG))
        before, writes = m.protected()[0], len(m.writes)
        m.load(remove, 0)
        assert m.protected() == (before, *stock)
        changed_sites = [at for at, _, _ in m.writes[writes:]]
        assert changed_sites == [READ_SITE if already_stock == INIT_SITE else INIT_SITE]
        results.append(f'remove known partial {already_stock:#x}')
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, default=MAIN)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/boot-probe')
    args = parser.parse_args()
    firmware = firmware_bytes(args.firmware)
    install = build_card(args.upstream, args.out / 'install', firmware=args.firmware)
    remove = build_card(args.upstream, args.out / 'remove', mode='remove', firmware=args.firmware)
    assert (install / 'resident.bin').read_bytes() == (remove / 'resident.bin').read_bytes(), (
        'installer and remover disagree on resident ownership')
    report = {
        'scope': 'Actual ARM loader/stage2/installer/remover/callbacks/native readiness gate; modeled OS/file/cache boundaries',
        'firmware_sha256': MAIN_SHA,
        'lifecycle': lifecycle(firmware, install, remove),
        'register_stack_and_wrap': callback_invariants(firmware, install),
        'ownership_scenarios': refusal_cases(firmware, install, remove),
        'local_interrupt_atomicity': local_interrupt_atomicity(firmware, install, remove),
        'limits': 'No hardware timing, SD safety, DRAM retention, cache physics or cross-core/arbitrary-owner concurrency proof; no camera access.',
    }
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
