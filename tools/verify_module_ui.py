#!/usr/bin/env python3
"""Run the real module UI ARM code against bounded synthetic LCD buffers.

No camera access. Firmware display continuations and cache effects are modeled;
this is not proof of panel visibility, scheduling, or hardware cache coherence.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import struct

from unicorn import arm_const as ar

from build_module_card import build_card
from verify_modules import ABI, CameraModel, FIRMWARE_SHA256, ROOT, digest, signed

PRESS = 0xC03722E8
SUBMIT = 0xC02E8A08
UI_STATE = 0xC3033A44
PROVIDER, DEMO = 0x100, 0x101
WIDTH, HEIGHT = 1024, 682
X, Y, W, H = 464, 560, 96, 32
META = 0x51000000
REQUEST = META + 0x100


class DisplayModel(CameraModel):
    def __init__(self, firmware, card, heap):
        super().__init__(firmware, card, heap)
        self.u.mem_map(UI_STATE & ~4095, 4096)
        self.put(UI_STATE, 2)
        self.u.mem_map(META, 4096)
        self.buffers = [0x50000000 + i * 0x200000 for i in range(4)]
        for address in self.buffers:
            self.u.mem_map(address, 0x200000)
            self.u.mem_write(address, b'\x23\xF1' * (WIDTH * HEIGHT))
        self.forwarded = []
        self.expected = None

    def execute(self, u, address, size, data):
        if (ABI['FP_CAVE_BEGIN'] + ABI['DIR_SIZE'] <= address
                < self.get(ABI['FP_CAVE_BUMP'])):
            assert self.get(address) == 0xE51FF004, 'invalid hook veneer'
            target = self.get(address + 4)
            records = self.get(self.api + ABI['API_RECORDS'])
            count = self.get(self.api + ABI['API_COUNT'])
            owners = [records + i * ABI['REC_SIZE'] for i in range(count)
                      if self.get(records + i * ABI['REC_SIZE']) == PROVIDER]
            assert len(owners) == 1
            image = self.get(owners[0] + ABI['REC_IMAGE'])
            length = self.get(owners[0] + ABI['REC_BYTES'])
            assert image + ABI['MOD_HEADER_BYTES'] <= target < image + length
            return
        if address in (PRESS, SUBMIT):
            return
        if address in (PRESS + 4, SUBMIT + 4):
            args = tuple(u.reg_read(getattr(ar, f'UC_ARM_REG_R{i}')) for i in range(4))
            assert args == self.expected, ('stock arguments corrupted', args, self.expected)
            self.forwarded.append((address, args))
            # The wrapper replayed the original prologue. Model the untouched
            # stock body and its matching epilogue, not the firmware UI engine.
            saved = [4, 14] if address == PRESS + 4 else [4, 5, 6, 7, 11, 14]
            sp = u.reg_read(ar.UC_ARM_REG_SP)
            values = struct.unpack('<' + 'I' * len(saved), u.mem_read(sp, 4 * len(saved)))
            for register, value in zip(saved, values):
                u.reg_write(getattr(ar, f'UC_ARM_REG_R{register}') if register < 13 else ar.UC_ARM_REG_LR, value)
            u.reg_write(ar.UC_ARM_REG_SP, sp + len(saved) * 4)
            u.reg_write(ar.UC_ARM_REG_R0, 0x12345678)
            u.reg_write(ar.UC_ARM_REG_PC, u.reg_read(ar.UC_ARM_REG_LR))
            return
        super().execute(u, address, size, data)

    def command(self, op, *args):
        self.put(REQUEST, op, *args, *([0] * (5 - len(args))))
        status, value = self.service('API_CALL', PROVIDER, REQUEST)
        assert status == 0, ('provider not callable', signed(status))
        return signed(value)

    def submit(self, index=0, fmt=1, sub=0, width=WIDTH, height=HEIGHT):
        self.put(META, fmt, self.buffers[index], META + 0x20, 0)
        self.put(META + 0x20, width, height)
        self.expected = (0x11223344, META, sub, 0x55667788)
        result = self.call(SUBMIT, *self.expected)
        assert result[0] == 0x12345678

    def press(self):
        self.expected = (0xAABBCCDD, 0x11223344, 0x55667788, 0x99AABBCC)
        assert self.call(PRESS, *self.expected)[0] == 0x12345678

    def tile(self, index=0):
        base = self.buffers[index]
        return b''.join(bytes(self.u.mem_read(base + ((Y + row) * WIDTH + X) * 2, W * 2)) for row in range(H))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/module-ui-proof')
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    assert digest(firmware) == FIRMWARE_SHA256, 'requires original fp 5.02 MAIN'
    spec = importlib.util.spec_from_file_location('ui_armasm', upstream / 'fp_usb_shell/armasm.py')
    assembler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembler)
    out.mkdir(parents=True, exist_ok=True)
    modules = []
    for name in ('module_ui', 'module_ui_demo'):
        path = out / f'{name}.bin'
        path.write_bytes(assembler.assemble(ROOT / 'src' / f'{name}.S'))
        modules.append(path)
    card = build_card(upstream, out / 'card', modules, debug=True)
    cases = {}

    def fresh(name, selected=card):
        model = DisplayModel(firmware, selected, 0x45000000)
        rows = model.boot()
        result = [(model.get(r + ABI['REC_ID']), signed(model.get(r + ABI['REC_STATUS']))) for r in rows]
        return model, result

    model, rows = fresh('baseline')
    assert rows == [(PROVIDER, 0), (DEMO, 0)], rows
    assert model.get(PRESS) == 0xE92D4010 and model.get(SUBMIT) == 0xE92D48F0
    assert model.command(0) == 1
    cases['passive_boot'] = 'both ready; no hooks armed at initialization'

    before = [bytes(model.u.mem_read(b, WIDTH * HEIGHT * 2)) for b in model.buffers]
    assert model.command(1, 0, 0, W, H, 0xFFFF) == 0
    assert model.command(2) == 0
    assert model.get(PRESS) != 0xE92D4010 and model.get(SUBMIT) != 0xE92D48F0
    for index in range(3):
        model.submit(index)
        expected = bytearray(before[index])
        for row in range(H):
            offset = ((Y + row) * WIDTH + X) * 2
            expected[offset:offset + W * 2] = b'\xFF\xFF' * W
        assert bytes(model.u.mem_read(model.buffers[index], len(expected))) == expected, 'paint escaped tile or missed pixels'
    cases['three_buffers'] = 'exact white tile, all surrounding native pixels unchanged'

    model.submit(3)
    assert bytes(model.u.mem_read(model.buffers[3], len(before[3]))) == before[3]
    assert model.command(5) == -20
    unchanged = model.tile(0)
    model.submit(0, height=HEIGHT + 1)
    assert model.tile(0) == unchanged
    assert model.command(5) == -21
    model.submit(0)
    assert model.command(5) == 0
    cases['painted_buffer_ownership'] = 'fourth buffer and changed live identity refused without eviction or writes'

    # A later native write must survive hide, rather than being replaced by the
    # saved background. HIDE itself must not dereference cached buffer addresses.
    changed_at = model.buffers[0] + (Y * WIDTH + X) * 2
    model.u.mem_write(changed_at, b'\x56\xF4')
    painted = model.tile()
    assert model.command(3) == 0
    assert model.tile() == painted
    for index in range(3):
        model.submit(index)
        expected = bytearray(before[index])
        if index == 0:
            offset = (Y * WIDTH + X) * 2
            expected[offset:offset + 2] = b'\x56\xF4'
        assert bytes(model.u.mem_read(model.buffers[index], len(expected))) == expected
    cases['hide_preserves_native_updates'] = 'restores at submission, leaves changed native pixel intact'

    assert model.command(1, 0, 0, W, H, 0xF000) == 0
    assert model.command(1, W - 2, H - 2, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF) == 0
    assert model.command(1, 0xFFFFFFFF, 0, 1, 1, 0xFFFF) == 0
    assert model.command(2) == 0
    model.submit(1)
    expected_tile = bytearray(b'\x00\xF0' * W * H)
    for row in (H - 2, H - 1):
        expected_tile[(row * W + W - 2) * 2:(row * W + W) * 2] = b'\xFF\xFF' * 2
    assert model.tile(1) == expected_tile
    cases['overflow_clipping'] = 'huge extents clipped by subtraction, outside origin ignored'

    model.put(UI_STATE, 5)
    model.submit(1)
    assert model.tile(1) == b'\x23\xF1' * W * H
    model.put(UI_STATE, 4)
    model.submit(2)
    assert model.tile(2) == b'\x23\xF1' * W * H
    model.put(UI_STATE, 2)
    for params in ({'fmt': 3}, {'sub': 1}, {'width': 512}, {'height': 32}):
        untouched = bytes(model.u.mem_read(model.buffers[3], WIDTH * HEIGHT * 2))
        model.submit(3, **params)
        assert bytes(model.u.mem_read(model.buffers[3], len(untouched))) == untouched
    cases['surface_and_mode_gates'] = 'no overlay on menu, playback, indexed, sublayer or unsupported geometry'

    start_count = model.command(4)
    model.press()
    model.press()
    assert model.command(4) == start_count + 2
    cases['button_passthrough'] = 'two observations, all stock arguments and stock return preserved'

    status, result = model.service('API_CALL', DEMO, 0)
    assert status == 0 and signed(result) >= 0, (status, signed(result))
    model.submit(0)
    first = model.tile()
    model.press()
    status, result = model.service('API_CALL', DEMO, 0)
    assert status == 0 and signed(result) >= 0
    model.submit(0)
    second = model.tile()
    assert first != second, 'independent demo did not update its rendered counters'
    # Portable grayscale preview, alpha composited onto black for inspection.
    rgb = bytearray()
    for (pixel,) in struct.iter_unpack('<H', second):
        alpha = pixel >> 12
        gray = (pixel & 15) * 17 * alpha // 15
        rgb.extend((gray, gray, gray))
    (out / 'demo.ppm').write_bytes(f'P6\n{W} {H}\n255\n'.encode() + rgb)
    assert model.service('API_CALL', DEMO, 1)[0] == 0
    model.submit(0)
    cases['independent_consumer'] = 'demo invokes provider through registry and changes visible counter pixels'

    for site in (PRESS, SUBMIT):
        conflict, _ = fresh('conflict')
        conflict.put(site, 0xE1A00000)
        stock_words = (conflict.get(PRESS), conflict.get(SUBMIT))
        bump = conflict.get(ABI['FP_CAVE_BUMP'])
        assert conflict.command(2) < 0
        assert (conflict.get(PRESS), conflict.get(SUBMIT)) == stock_words
        assert conflict.get(ABI['FP_CAVE_BUMP']) == bump
    cases['hook_conflicts'] = 'refused without patching either site or consuming cave space'

    exhausted, _ = fresh('exhausted')
    exhausted.put(ABI['FP_CAVE_BUMP'], ABI['FP_CAVE_END'])
    assert exhausted.command(2) < 0
    assert exhausted.get(PRESS) == 0xE92D4010 and exhausted.get(SUBMIT) == 0xE92D48F0
    cases['cave_exhaustion'] = 'no partial hook installation'

    reversed_card = build_card(upstream, out / 'reversed', modules[::-1])
    reversed_model, reversed_rows = fresh('dependency_order', reversed_card)
    assert reversed_rows == [(DEMO, ABI['FP_EINIT']), (PROVIDER, 0)], reversed_rows
    assert reversed_model.get(PRESS) == 0xE92D4010 and reversed_model.get(SUBMIT) == 0xE92D48F0
    cases['dependency_order'] = 'demo refuses missing provider; provider still loads without hooks'

    # Locate the allocation only to inject a firmware allocator failure.
    # Read outcomes from the public registry, as a consumer would.
    provider_row = model.service('API_LOOKUP', PROVIDER)[0]
    provider_image = model.get(provider_row + ABI['REC_IMAGE'])
    allocation = provider_image + assembler.symbols(ROOT / 'src/module_ui.S')['ui_allocation']
    storage_request = model.descriptors[allocation]['request']
    refused = CameraModel(firmware, card, 0x46000000, fail_request=storage_request)
    failed_rows = refused.boot()
    assert [(refused.get(r + ABI['REC_ID']), signed(refused.get(r + ABI['REC_STATUS'])))
            for r in failed_rows] == [(PROVIDER, ABI['FP_EINIT']), (DEMO, ABI['FP_EINIT'])]
    assert signed(refused.get(failed_rows[0] + ABI['REC_INIT_RESULT'])) == ABI['FP_ENOMEM']
    assert refused.get(PRESS) == 0xE92D4010 and refused.get(SUBMIT) == 0xE92D48F0
    assert refused.service('API_CALL', PROVIDER, REQUEST)[0] == ABI['FP_ENOTREADY'] & 0xFFFFFFFF
    cases['storage_allocation_failure'] = 'provider and dependent refuse cleanly, no hooks or callable failed images'

    class MisalignedStorage(CameraModel):
        def execute(self, u, address, size, data):
            corrupt = False
            if address == 0xC001D7F0:
                descriptor = u.reg_read(ar.UC_ARM_REG_R0)
                block = self.descriptors.get(descriptor)
                corrupt = bool(block and block['request'] == storage_request)
            super().execute(u, address, size, data)
            if corrupt:
                u.reg_write(ar.UC_ARM_REG_R0, u.reg_read(ar.UC_ARM_REG_R0) + 1)

    malformed = MisalignedStorage(firmware, card, 0x47000000)
    malformed_rows = malformed.boot()
    assert signed(malformed.get(malformed_rows[0] + ABI['REC_INIT_RESULT'])) == ABI['FP_EFORMAT']
    assert next(b for b in malformed.blocks if b['request'] == storage_request)['freed']
    assert malformed.get(PRESS) == 0xE92D4010 and malformed.get(SUBMIT) == 0xE92D48F0
    cases['malformed_owned_storage'] = 'misaligned storage freed before init failure without arming hooks'

    report = {'status': 'OFFLINE_ONLY', 'firmware_sha256': FIRMWARE_SHA256,
              'module_sha256': {p.name: digest(p.read_bytes()) for p in modules},
              'cases': cases, 'preview': str(out / 'demo.ppm'),
              'not_exercised': ['physical LCD', 'physical button', 'hardware cache coherence',
                                'concurrent scheduling', 'buffer lifetime/address reuse']}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, result in cases.items():
        print(f'{name}: {result}')
    print(f'{len(cases)} UI scenarios passed; {out / "report.json"}')


if __name__ == '__main__':
    main()
