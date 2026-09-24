#!/usr/bin/env python3
"""Execute the compiled C native toggle and boot loader offline, not on USB."""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys

from unicorn import arm_const as ar

from verify_modules import ABI, CameraModel, DCACHE, ICACHE, ROOT, FIRMWARE_SHA256, digest, signed

MODULE_ID = 0x103
PRESS, RELEASE = 0xC03722E8, 0xC0372330
MEMSET, POST = 0xC0015058, 0xC03A0798
CAMERA, CONTROLLER = 0x51000000, 0x51000100


class LatchModel(CameraModel):
    def __init__(self, firmware, card, heap, sabotage=None):
        self.base = self.end = self.memset_return = 0
        self.sabotage = sabotage
        self.posts, self.patches, self.veneers = [], [], []
        self.post_result = 1
        super().__init__(firmware, card, heap)
        self.u.mem_map(CAMERA, 4096)
        self.put(CAMERA + 4, CONTROLLER)

    def check_write(self, u, access, address, size, value, data):
        super().check_write(u, access, address, size, value, data)
        if not self.base <= u.reg_read(ar.UC_ARM_REG_PC) < self.end:
            return
        if 0xC0000000 <= address < 0xC3000000:
            assert u.reg_read(ar.UC_ARM_REG_CPSR) & 0xC0 == 0xC0
            if address in (PRESS, RELEASE):
                assert self.veneers and self.cache_calls[self.veneers[-1]:] == [DCACHE, ICACHE]
                self.patches.append(len(self.cache_calls))
            elif self.bump <= address < self.bump + 16:
                self.veneers.append(len(self.cache_calls))
            else:
                assert address == ABI['FP_CAVE_BUMP'], hex(address)
            assert size == 4

    def execute(self, u, address, size, data):
        if not self.base:
            for block in self.blocks:
                base = block['address']
                if (not block['freed'] and block['request'] != 1 and
                    base <= address < base + block['size'] and
                    self.get(base) == ABI['FP_MODULE_MAGIC'] and
                    self.get(base + ABI['MOD_ID']) == MODULE_ID):
                    self.base, self.end = base, base + block['size']
                    if self.sabotage:
                        self.sabotage(self)
                    self.bump = self.get(ABI['FP_CAVE_BUMP'])
                    self.sites = [self.get(PRESS), self.get(RELEASE)]
                    self.cave = bytes(u.mem_read(ABI['FP_CAVE_BEGIN'] + 16,
                                               ABI['FP_CAVE_END'] - ABI['FP_CAVE_BEGIN'] - 16))
                    self.flags = u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF
                    break
        if self.memset_return:
            if address != self.memset_return:
                return  # Execute actual firmware memset, not a substitute.
            self.memset_return = 0
        if address == MEMSET:
            assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0
            self.memset_return = u.reg_read(ar.UC_ARM_REG_LR)
            return
        if address in (PRESS, RELEASE):
            return  # Execute the patched prologue.
        if self.base and self.bump <= address < self.bump + 16:
            assert address in (self.bump, self.bump + 8)
            assert self.get(address) == 0xE51FF004
            assert self.base + 24 <= self.get(address + 4) < self.end
            assert self.cache_calls[self.patches[-1]:self.patches[-1] + 2] == [DCACHE, ICACHE]
            return
        if address == POST:
            assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0
            assert u.reg_read(ar.UC_ARM_REG_R0) == CONTROLLER
            request = bytes(u.mem_read(u.reg_read(ar.UC_ARM_REG_R1), 0xBC))
            event = struct.unpack_from('<I', request)[0]
            assert event in (0x21, 0x22)
            assert request == struct.pack('<II', event, 1) + bytes(0xBC - 8)
            self.posts.append(event)
            for register in (ar.UC_ARM_REG_R1, ar.UC_ARM_REG_R2, ar.UC_ARM_REG_R3, ar.UC_ARM_REG_IP):
                u.reg_write(register, 0xDEADBEEF)
            u.reg_write(ar.UC_ARM_REG_R0, self.post_result)
            u.reg_write(ar.UC_ARM_REG_PC, u.reg_read(ar.UC_ARM_REG_LR))
            return
        super().execute(u, address, size, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', default='builds/module-upstream')
    args = parser.parse_args()
    out = ROOT / 'builds/module-fclatch'
    subprocess.run([sys.executable, '-B', str(ROOT / 'tools/build_module.py'),
                    str(ROOT / 'src/module_fclatch.c'), '--module-id', hex(MODULE_ID),
                    '--upstream', args.upstream, '--out', str(out / 'module.bin'),
                    '--card-out', str(out / 'card'), '--debug'], check=True, cwd=ROOT)
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    assert digest(firmware) == FIRMWARE_SHA256
    results = []
    for heap in (0x45000000, 0x57300000):
        m = LatchModel(firmware, out / 'card', heap)
        record, = m.boot()
        assert signed(m.get(record + ABI['REC_STATUS'])) == 0
        assert m.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF == m.flags
        assert m.posts == []  # Boot never enables False Color.
        assert m.call(RELEASE, CAMERA)[0] == 1 and m.posts == []
        for state in (1, 0, 1, 0):
            assert m.call(PRESS, CAMERA)[0] == 1
            assert m.posts[-1] == (0x21 if state else 0x22)
            assert m.service('API_CALL', MODULE_ID, 1) == (0, state)
            previous = list(m.posts)
            for _ in range(3):
                assert m.call(RELEASE, CAMERA)[0] == 1
            assert m.posts == previous
        assert m.posts == [0x21, 0x22, 0x21, 0x22]
        bump = m.get(ABI['FP_CAVE_BUMP'])
        assert m.call(m.get(record + ABI['REC_INIT']), m.api, record)[0] == 0
        assert m.get(ABI['FP_CAVE_BUMP']) == bump and len(m.patches) == 2
        # Preserve the native method's return value, including a failed post.
        m.post_result = 0
        assert m.call(PRESS, CAMERA)[0] == 0
        results.append({'heap': hex(heap), 'events': m.posts, 'release_posts': 0})
    failures = [
        ('press owned', lambda m: m.put(PRESS, 0xEA000000), -8),
        ('release owned', lambda m: m.put(RELEASE, 0xEA000000), -8),
        ('occupied cave', lambda m: m.put(m.get(ABI['FP_CAVE_BUMP']), 123), -8),
        ('full cave', lambda m: m.put(ABI['FP_CAVE_BUMP'], ABI['FP_CAVE_END'] - 12), -9),
        ('unaligned cave', lambda m: m.put(ABI['FP_CAVE_BUMP'], ABI['FP_CAVE_BEGIN'] + 17), -9),
        ('invalid cave', lambda m: m.put(ABI['FP_CAVE_BUMP'], 0xFFFFFFFC), -9),
    ]
    for name, sabotage, error in failures:
        m = LatchModel(firmware, out / 'card', 0x45000000, sabotage)
        record, = m.boot()
        assert signed(m.get(record + ABI['REC_STATUS'])) == ABI['FP_EINIT']
        assert signed(m.get(record + ABI['REC_INIT_RESULT'])) == error
        assert m.get(record + ABI['REC_IMAGE']) == 0
        assert any(b['address'] == m.base and b['freed'] for b in m.blocks)
        assert m.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF == m.flags
        assert [m.get(PRESS), m.get(RELEASE)] == m.sites
        assert m.get(ABI['FP_CAVE_BUMP']) == m.bump
        assert bytes(m.u.mem_read(ABI['FP_CAVE_BEGIN'] + 16, len(m.cave))) == m.cave
        assert not m.posts and not m.patches and not m.veneers
        results.append({'rejection': name, 'error': error})
    (out / 'report.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'PASS: {len(results)} native C toggle scenarios. Offline ARM execution only.')


if __name__ == '__main__':
    main()
