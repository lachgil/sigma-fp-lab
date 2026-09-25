#!/usr/bin/env python3
"""Execute the compiled C native toggle and boot loader offline, not on USB."""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys

from unicorn import arm_const as ar

from verify_modules import ABI, CameraModel, ROOT, FIRMWARE_SHA256, digest, signed

MODULE_ID = 0x103
PRESS, RELEASE = 0xC03722E8, 0xC0372330
MEMSET, POST = 0xC0015058, 0xC03A0798
CAMERA, CONTROLLER = 0x51000000, 0x51000100


class LatchModel(CameraModel):
    def __init__(self, firmware, card, heap, sabotage=None):
        self.base = self.end = self.memset_return = 0
        self.sabotage = sabotage
        self.posts, self.patches = [], []
        self.post_result = 1
        super().__init__(firmware, card, heap)
        self.u.mem_map(CAMERA, 4096)
        self.put(CAMERA + 4, CONTROLLER)

    def check_write(self, u, access, address, size, value, data):
        super().check_write(u, access, address, size, value, data)
        if address in (PRESS, RELEASE):
            self.patches.append((address, value))
        if self.base <= u.reg_read(ar.UC_ARM_REG_PC) < self.end:
            assert not 0xC0000000 <= address < 0xC3000000, (
                f'module wrote firmware/cave directly at {address:#x}')

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
        assert m.service('API_CALL', MODULE_ID, 2) == (0, 4)
        assert m.service('API_CALL', MODULE_ID, 3) == (0, 13)
        assert m.service('API_CALL', MODULE_ID, 4) == (0, 1)
        assert m.service('API_CALL', MODULE_ID, 99) == (0, ABI['FP_EFORMAT'] & 0xFFFFFFFF)
        # Preserve the native method's return value, including a failed post.
        m.post_result = 0
        assert m.call(PRESS, CAMERA)[0] == 0
        results.append({'heap': hex(heap), 'events': m.posts, 'release_posts': 0})
        allocations = [(b['address'], b['freed']) for b in m.blocks]
        for forced in (heap == 0x57300000, heap != 0x57300000):
            m.shutdown(forced=forced)
            assert m.get(PRESS) == m.get(RELEASE) == 0xE92D4010
            assert m.service('API_CALL', MODULE_ID, 1) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
            assert [(b['address'], b['freed']) for b in m.blocks] == allocations
        results[-1]['shutdown_restored_both_hooks_without_free'] = True
        warm = LatchModel(m.retained_image(), out / 'card', heap + 0x02000000)
        warm_record, = warm.boot()
        assert signed(warm.get(warm_record + ABI['REC_STATUS'])) == 0
        assert warm.get(warm_record + ABI['REC_IMAGE']) != m.get(record + ABI['REC_IMAGE'])
        assert warm.service('API_CALL', MODULE_ID, 1) == (0, 0)
        for site in (PRESS, RELEASE, PRESS, RELEASE):
            assert warm.call(site, CAMERA)[0] == 1
        assert warm.posts == [0x21, 0x22]
        warm.shutdown(forced=True)
        assert warm.get(PRESS) == warm.get(RELEASE) == 0xE92D4010
        results[-1]['warm_reload_with_new_heap_toggles_again'] = True
    failures = [
        ('press owned', lambda m: m.put(PRESS, 0xEA000000), -8),
        ('release owned', lambda m: m.put(RELEASE, 0xEA000000), -8),
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
        assert not m.posts and not m.patches
        results.append({'rejection': name, 'error': error})
    (out / 'report.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'PASS: {len(results)} native C toggle scenarios. Offline ARM execution only.')


if __name__ == '__main__':
    main()
