#!/usr/bin/env python3
"""Execute the experimental fp module registry through the unchanged boot loader.

Offline only: firmware allocation, file I/O, clock and cache calls are modeled.
The emitted loader, registry, services and module ARM instructions execute.
Results are discovered through the public cave directory and service table,
never by scanning allocator internals for module instances. No camera access.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys

import unicorn as uc
from unicorn import arm_const as ar

from build_module_card import build_card

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_SHA256 = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
# Decode the public serialized ABI rather than maintaining a second offset map.
ABI = {name: int(value, 0) for name, value in re.findall(
    r'^#define (\w+) (-?(?:0x[0-9A-Fa-f]+|[0-9]+))$',
    (ROOT / 'src/module_abi.h').read_text(), re.M)}
LOADER = 0xC072DE64
STOP = 0x10000000
SP = 0x1000FFF0
H_GET, H_ADDR, H_FREE = 0xC001D740, 0xC001D7F0, 0xC001D7A0
DCACHE, ICACHE = 0xC000E91C, 0xC000EABC
F_MGR, F_VOL = 0xC0444658, 0xC0444698
F_CTOR, F_OPEN, F_READ = 0xC0365E90, 0xC0365FB0, 0xC0366060
F_CLOSE, F_DTOR, TICK = 0xC0366020, 0xC0365ED0, 0xC002B6E0
TASK_CREATE, TASK_START = 0xC0016A58, 0xC0016BC0
SERVICES = {H_GET, H_ADDR, H_FREE, DCACHE, ICACHE, F_MGR, F_VOL,
            F_CTOR, F_OPEN, F_READ, F_CLOSE, F_DTOR, TICK, TASK_CREATE, TASK_START}
SAVED = [getattr(ar, f'UC_ARM_REG_R{i}') for i in range(4, 12)]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signed(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


class CameraModel:
    """Strict service boundaries; this does not simulate scheduling or caches."""

    def __init__(self, firmware: bytes, card: Path, heap: int, fail_request: int = 0):
        self.u = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        self.u.mem_map(0xC0000000, (len(firmware) + 4095) & ~4095)
        self.u.mem_write(0xC0000000, firmware)
        self.u.mem_map(STOP, 0x10000)
        self.blob = (card / 'fpSup.BIN').read_bytes()
        self.heap = heap
        self.fail_request = fail_request
        self.requests = 0
        self.descriptors = {}
        self.blocks = []
        self.closed = False
        self.opened = False
        self.cache_calls = []
        self.events = []
        self.api = 0
        self.ticks = 0
        self.tasks = []
        # A stale directory must not be accepted after a fresh boot/failure.
        self.put(ABI['FP_CAVE_BEGIN'], ABI['FP_DIRECTORY_MAGIC'], 0xDEADBEEF, 1234, 0)
        for address, value in re.findall(r'^mem set (0x[0-9A-Fa-f]+) (0x[0-9A-Fa-f]+)$',
                                         (card / 'AutoRun.txt').read_text(), re.M):
            address = int(address, 16)
            if LOADER <= address < LOADER + 0x200:
                self.put(address, int(value, 16))
        self.u.hook_add(uc.UC_HOOK_CODE, self.execute)
        self.u.hook_add(uc.UC_HOOK_MEM_WRITE, self.check_write)

    def get(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def put(self, address, *values):
        self.u.mem_write(address, struct.pack('<' + 'I' * len(values), *[v & 0xFFFFFFFF for v in values]))

    def check_write(self, u, access, address, size, value, _):
        for block in self.blocks:
            if not block['freed'] and block['address'] <= address < block['address'] + block['span']:
                assert address + size <= block['address'] + block['size'], 'write beyond allocation extent'

    def execute(self, u, address, size, _):
        if address not in SERVICES:
            if LOADER <= address < LOADER + 0x200:
                return
            for block in self.blocks:
                if not block['freed'] and block['address'] <= address < block['address'] + block['size']:
                    if block['request'] != 1 and not block['executed']:
                        calls = self.cache_calls[block['cache_start']:]
                        assert calls == [DCACHE, ICACHE], 'resident code ran before its own D/I publication'
                        block['executed'] = True
                    return
            raise AssertionError(f'unexpected execution at {address:#x}')
        assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0, 'unaligned firmware call'
        r0, r1, r2, r3 = [u.reg_read(getattr(ar, f'UC_ARM_REG_R{i}')) for i in range(4)]
        result = 0
        if address == H_GET:
            self.requests += 1
            assert r1 == 0 and r3 == 0 and 0 < r2 <= 0x100000
            if self.requests == self.fail_request:
                self.descriptors[r0] = None
            else:
                span = (r2 + 4095) & ~4095
                block = dict(address=self.heap, size=r2, span=span,
                             request=self.requests, freed=False, executed=False,
                             cache_start=len(self.cache_calls))
                u.mem_map(self.heap, span)
                self.heap += span + 0x1000
                self.blocks.append(block)
                self.descriptors[r0] = block
            self.events.append({'allocate': self.requests, 'bytes': r2,
                                'refused': self.requests == self.fail_request})
        elif address == H_ADDR:
            block = self.descriptors[r0]
            result = block['address'] if block else 0
        elif address == H_FREE:
            block = self.descriptors[r0]
            assert block and not block['freed'] and r1 == 2 and self.closed
            u.mem_write(block['address'], b'\xA5' * block['span'])
            u.mem_unmap(block['address'], block['span'])
            block['freed'] = True
            self.events.append({'freed_request': block['request']})
        elif address in (DCACHE, ICACHE):
            self.cache_calls.append(address)
        elif address == F_MGR:
            result = 0x20000000
        elif address == F_VOL:
            result = 1
        elif address == F_OPEN:
            path = bytes(u.mem_read(r1, 32)).split(b'\0', 1)[0]
            assert path == b'\\fpSup.BIN' and r2 == 1
            self.opened = True
            result = 1
        elif address == F_READ:
            assert self.opened and not self.closed and len(self.blob) <= r2
            staging = self.blocks[0]
            assert staging['address'] <= r1 and r1 + r2 <= staging['address'] + staging['size']
            u.mem_write(r1, self.blob)
            result = len(self.blob)
        elif address == F_CLOSE:
            assert self.opened
            self.closed = True
        elif address == TICK:
            self.ticks += 1
            result = 123456
        elif address == TASK_CREATE:
            entry = self.get(r0 + 8)
            owner = next(b for b in self.blocks
                         if not b['freed'] and b['address'] <= entry < b['address'] + b['size'])
            assert owner['request'] != 1, 'task points into temporary staging'
            assert self.cache_calls[owner['cache_start']:] == [DCACHE, ICACHE]
            result = len(self.tasks) + 1
            self.tasks.append({'id': result, 'entry': entry, 'started': False})
        elif address == TASK_START:
            task = next(t for t in self.tasks if t['id'] == r0)
            assert r1 == 0 and not task['started']
            task['started'] = True
            # The scheduler/USB worker loop is deliberately not simulated.
        for register in (ar.UC_ARM_REG_R1, ar.UC_ARM_REG_R2, ar.UC_ARM_REG_R3, ar.UC_ARM_REG_IP):
            u.reg_write(register, 0xDEADBEEF)
        u.reg_write(ar.UC_ARM_REG_R0, result)
        u.reg_write(ar.UC_ARM_REG_PC, u.reg_read(ar.UC_ARM_REG_LR))

    def call(self, address, *arguments):
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        for i, value in enumerate(arguments):
            self.u.reg_write(getattr(ar, f'UC_ARM_REG_R{i}'), value & 0xFFFFFFFF)
        for i, register in enumerate(SAVED):
            self.u.reg_write(register, 0x12340000 + i)
        self.u.emu_start(address, STOP, count=300000)
        assert self.u.reg_read(ar.UC_ARM_REG_PC) == STOP, 'entry did not return'
        assert self.u.reg_read(ar.UC_ARM_REG_SP) == SP, 'unbalanced stack'
        assert [self.u.reg_read(r) for r in SAVED] == [0x12340000 + i for i in range(8)]
        return self.u.reg_read(ar.UC_ARM_REG_R0), self.u.reg_read(ar.UC_ARM_REG_R1)

    def service(self, name, *arguments):
        return self.call(self.get(self.api + ABI[name]), self.api, *arguments)

    def boot(self, root_status=0):
        self.call(LOADER)
        assert self.blocks[0]['freed'], 'loader retained staging'
        root = ABI['FP_CAVE_BEGIN']
        assert self.get(root + ABI['DIR_MAGIC']) == ABI['FP_DIRECTORY_MAGIC']
        assert signed(self.get(root + ABI['DIR_STATUS'])) == root_status
        self.api = self.get(root + ABI['DIR_RUNTIME'])
        if root_status:
            assert self.api == 0 and self.get(root + ABI['DIR_BYTES']) == 0
            return []
        assert self.api and self.get(self.api + ABI['API_MAGIC']) == ABI['FP_RUNTIME_MAGIC']
        assert self.get(self.api + ABI['API_ABI']) == ABI['FP_ABI']
        assert self.get(self.api + ABI['API_BYTES']) >= ABI['API_SIZE']
        count = self.get(self.api + ABI['API_COUNT'])
        assert count <= ABI['FP_CAPACITY']
        records = self.get(self.api + ABI['API_RECORDS'])
        return [records + i * ABI['REC_SIZE'] for i in range(count)]

    def run(self, expected_rows, counters, failure_result=None):
        rows = self.boot()
        actual_rows = [(self.get(r + ABI['REC_ID']), signed(self.get(r + ABI['REC_STATUS']))) for r in rows]
        assert actual_rows == expected_rows, (actual_rows, expected_rows)
        first = {}
        for r in rows:
            first.setdefault(self.get(r + ABI['REC_ID']), r)
        observed = {}
        for identifier, row in first.items():
            if identifier == 0:
                continue
            found, _ = self.service('API_LOOKUP', identifier)
            assert found == row
            status = signed(self.get(row + ABI['REC_STATUS']))
            if status:
                assert self.service('API_CALL', identifier, 2) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
                report_status, _ = self.service('API_REPORT', identifier, 987)
                assert signed(report_status) == ABI['FP_ENOTREADY']
                assert self.get(row + ABI['REC_IMAGE']) == 0
                assert self.get(row + ABI['REC_INVOKE']) == 0
                if failure_result is not None and status == ABI['FP_EINIT']:
                    assert signed(self.get(row + ABI['REC_INIT_RESULT'])) == failure_result
                observed[identifier] = {'status': status}
                continue
            initial, step = counters[identifier]
            assert self.get(row + ABI['REC_VALUE']) == initial, 'initial report not visible'
            assert signed(self.get(row + ABI['REC_INIT_RESULT'])) == 0
            values = []
            for n in (1, 2):
                result, value = self.service('API_CALL', identifier, 2)
                assert result == 0
                assert value == (initial + n * (step + 2)) & 0xFFFFFFFF
                assert self.get(row + ABI['REC_VALUE']) == value, 'module report not visible'
                values.append(value)
            observed[identifier] = {'status': 0, 'values': values}
        for missing in (0, 0xFFFFFFFF):
            assert self.service('API_LOOKUP', missing)[0] == 0
            assert self.service('API_CALL', missing, 0) == (ABI['FP_ENOENT'] & 0xFFFFFFFF, 0)
            assert signed(self.service('API_REPORT', missing, 999)[0]) == ABI['FP_ENOENT']
        assert self.service('API_TICKS')[0] == 123456
        # Allocation model is used for leak/safety checks, not discovery.
        live = [b for b in self.blocks if not b['freed']]
        assert len(live) == 1 + sum(status == 0 for _, status in expected_rows)
        return {'records': actual_rows, 'modules': observed, 'staging_unmapped': True,
                'clock_calls': self.ticks, 'events': self.events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/module-proof')
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    if digest(firmware) != FIRMWARE_SHA256:
        raise SystemExit('requires the verified original fp 5.02 MAIN image')
    spec = importlib.util.spec_from_file_location('module_armasm', upstream / 'fp_usb_shell/armasm.py')
    assembler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembler)
    out.mkdir(parents=True, exist_ok=True)

    def build(name, identifier, initial, step, *extra):
        blob = assembler.assemble(ROOT / 'src/module_probe.S',
                                  [f'MODULE_ID={identifier}', f'INITIAL_VALUE={initial}', f'STEP={step}', *extra])
        path = out / f'{name}.bin'
        path.write_bytes(blob)
        return path

    def package(name, modules):
        return build_card(upstream, out / name, modules)

    cases = {}

    def exercise(name, card, rows, counters, fail=0, failure_result=None):
        model = CameraModel(firmware, card, 0x45000000 + len(cases) * 0x100000, fail)
        cases[name] = model.run(rows, counters, failure_result)
        return model

    a = build('a-v1', 1, 10, 1)
    b = build('b', 2, 100, 7)
    b_hash = digest(b.read_bytes())
    baseline = package('baseline', [a, b])
    model = exercise('baseline', baseline, [(1, 0), (2, 0)], {1: (10, 1), 2: (100, 7)})
    # A future incompatible service table must not be dereferenced by a module.
    record = model.service('API_LOOKUP', 1)[0]
    init = model.get(record + ABI['REC_INIT'])
    prior_ticks = model.ticks
    model.put(model.api + ABI['API_ABI'], ABI['FP_ABI'] + 1)
    assert signed(model.call(init, model.api, record)[0]) == ABI['FP_EABI']
    assert model.ticks == prior_ticks and model.get(record + ABI['REC_VALUE']) == 16
    model.put(model.api + ABI['API_ABI'], ABI['FP_ABI'])
    cases['service_version_mismatch'] = {'rejected_before_service_calls': True}
    # Only A is compiled here. B's already-built artifact is carried unchanged.
    replacement = build('a-v2', 1, 1000, 3)
    changed = package('replace-a', [replacement, b])
    assert digest(b.read_bytes()) == b_hash
    assert b.read_bytes() in (changed / 'fpSup.BIN').read_bytes()
    exercise('replace_a', changed, [(1, 0), (2, 0)], {1: (1000, 3), 2: (100, 7)})
    exercise('reverse_order', package('reverse', [b, replacement]), [(2, 0), (1, 0)], {1: (1000, 3), 2: (100, 7)})
    exercise('refuse_a', baseline, [(1, -1), (2, 0)], {2: (100, 7)}, fail=3)
    exercise('refuse_b', baseline, [(1, 0), (2, -1)], {1: (10, 1)}, fail=4)
    exercise('duplicate_id', package('duplicate', [a, b, replacement]), [(1, 0), (2, 0), (1, -4)], {1: (10, 1), 2: (100, 7)})
    incompatible = build('abi-mismatch', 1, 10, 1, 'MODULE_ABI=2')
    exercise('abi_mismatch', package('abi-mismatch', [incompatible, b]), [(1, -2), (2, 0)], {2: (100, 7)})
    failing = build('init-failure', 1, 10, 1, 'INIT_RESULT=-77')
    failed_model = exercise('init_failure', package('init-failure', [failing, b]), [(1, -5), (2, 0)], {2: (100, 7)}, failure_result=-77)
    assert {'freed_request': 3} in failed_model.events
    exercise('duplicate_failed_id', package('duplicate-failed', [failing, replacement, b]),
             [(1, -5), (1, -4), (2, 0)], {2: (100, 7)}, failure_result=-77)
    # Runtime rejects an out-of-range callback before it can allocate or jump.
    malformed = out / 'invalid-callback.bin'
    data = bytearray(a.read_bytes())
    struct.pack_into('<I', data, ABI['MOD_INVOKE'], 0xFFFFFFFC)
    malformed.write_bytes(data)
    exercise('invalid_callback', package('invalid-callback', [malformed, b]), [(1, -3), (2, 0)], {2: (100, 7)})
    truncated = out / 'short-header.bin'
    truncated.write_bytes(a.read_bytes()[:20])
    exercise('short_header', package('short-header', [truncated, b]),
             [(0, -3), (2, 0)], {2: (100, 7)})
    capacity_modules = [build(f'capacity-{i}', i, i * 10, i) for i in range(1, ABI['FP_CAPACITY'] + 1)]
    identifiers = range(1, ABI['FP_CAPACITY'] + 1)
    exercise('capacity', package('capacity', capacity_modules),
             [(i, 0) for i in identifiers], {i: (i * 10, i) for i in identifiers})
    refused = CameraModel(firmware, baseline, 0x47000000, 2)
    refused.boot(ABI['FP_ENOMEM'])
    assert all(block['freed'] for block in refused.blocks)
    cases['runtime_allocation_failure'] = {'directory_status': -1, 'stale_root_cleared': True}
    try:
        package('over-capacity', capacity_modules + [a])
    except ValueError:
        cases['over_capacity'] = {'rejected_before_packaging': True}
    else:
        raise AssertionError('packager accepted too many modules')
    # Corrupt the packed catalog, bypassing host checks to exercise runtime bounds.
    runtime = (baseline / 'runtime.bin').read_bytes()
    catalog = assembler.symbols(ROOT / 'src/module_runtime.S')['catalog']
    corrupt_count = CameraModel(firmware, baseline, 0x48000000)
    runtime_at = corrupt_count.blob.index(runtime)
    damaged = bytearray(corrupt_count.blob)
    struct.pack_into('<I', damaged, runtime_at + catalog, ABI['FP_CAPACITY'] + 1)
    corrupt_count.blob = bytes(damaged)
    corrupt_count.boot(ABI['FP_EFORMAT'])
    assert all(block['freed'] for block in corrupt_count.blocks)
    cases['catalog_count_overflow'] = {'directory_status': -3, 'allocations_released': True}

    corrupt_extent = CameraModel(firmware, baseline, 0x49000000)
    damaged = bytearray(corrupt_extent.blob)
    struct.pack_into('<I', damaged, runtime_at + catalog + 12, 0xFFFFFFFC)
    corrupt_extent.blob = bytes(damaged)
    cases['catalog_extent_overflow'] = corrupt_extent.run([(0, -3), (2, 0)], {2: (100, 7)})

    debug_card = build_card(upstream, out / 'debug', [a, b], debug=True)
    debug_model = CameraModel(firmware, debug_card, 0x4A000000)
    debug_rows = debug_model.boot()
    assert [(debug_model.get(r + ABI['REC_ID']), debug_model.get(r + ABI['REC_STATUS']))
            for r in debug_rows] == [(1, 0), (2, 0)]
    assert debug_model.service('API_CALL', 1, 2) == (0, 13)
    assert debug_model.service('API_CALL', 2, 2) == (0, 109)
    assert len(debug_model.tasks) == 1 and debug_model.tasks[0]['started']
    cases['debug_boot_chain'] = {'registry_ready': True, 'modules_callable': True,
                                'shell_task_start_requested': True, 'usb_transport_tested': False}

    sources = ['build_autorun.py', 'armasm.py', 'templates/loader.S', 'templates/stage2.S', 'templates/entries.S']
    report = {'status': 'OFFLINE_ONLY_NOT_CAMERA_VALIDATED', 'firmware_sha256': FIRMWARE_SHA256,
              'discovery': 'public cave directory and versioned runtime service table',
              'modeled_services': ['allocator', 'file I/O', 'D/I cache calls', 'clock', 'task creation/start'],
              'not_exercised': ['AutoRun interpreter', 'USB shell', 'hardware caches', 'native menus', 'hooks', 'hot reload', 'concurrency'],
              'upstream_source_sha256': {p: digest((upstream / 'fp_usb_shell' / p).read_bytes()) for p in sources},
              'module_sha256': {p.name: digest(p.read_bytes()) for p in [a, b, replacement]},
              'cases': cases}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, result in cases.items():
        print(f'{name}: {result.get("modules", result)}')
    print(f'{len(cases)} scenarios passed; B unchanged: {b_hash}; report: {out / "report.json"}')
    print('OFFLINE ONLY. Generated boot files are not approved for camera installation.')


if __name__ == '__main__':
    main()
