#!/usr/bin/env python3
"""Boot and exercise the real False Color module entirely offline.

The unchanged upstream loader, registry, module hooks, painter and firmware
memset execute in Unicorn. Only firmware allocation/file/cache/task services,
the display manager, event post, clock and stock submit continuation are modeled.
This does not access the camera or SD card and does not prove hardware timing,
USB transport, cache coherence or physical LCD colour order.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import traceback

from unicorn import arm_const as ar

from build_module_fcscale import build, native
from verify_fcscale import render
from verify_modules import (ABI, CameraModel, FIRMWARE_SHA256, ROOT, SAVED, SP,
                            STOP, digest, signed)

MODULE_ID = 0x102
PRESS, RELEASE, SUBMIT = native.PRESS_SITE, native.RELEASE_SITE, native.SUBMIT_SITE
STOCK = {PRESS: native.SITE_STOCK, RELEASE: native.SITE_STOCK,
         SUBMIT: native.SUBMIT_STOCK}
HOOKS = {PRESS: 'fc_press', RELEASE: 'fc_release', SUBMIT: 'fc_submit'}
WIDTH, HEIGHT = native.SURFACE_W, native.SURFACE_H
FRAME_BYTES = WIDTH * HEIGHT * 2
PLACE = native.geometry()
SCALE_ROWS = set(range(PLACE['clear_y'], PLACE['clear_y'] + PLACE['clear_h']))
UI_STATE, MEMSET, POST, DRAW_MGR = 0xC3033A44, 0xC0015058, 0xC03A0798, 0xC0698D80
META = 0x51000000
MGR, MGR_VT, DRAWABLE, DRAW_VT = META, META + 0x20, META + 0x40, META + 0x60
GEOM, CAMERA_IF, CTRL = META + 0xE0, META + 0x800, META + 0x900
DESCRIPTORS = [META + 0xA0 + 0x10 * i for i in range(4)]
STUBS = [META + 0x100 + 8 * i for i in range(6)]
RESOLVE, FRONT, BACK, ROTATE, SETPAL, RESTORE = STUBS
PIXELS = [0x52000000 + 0x200000 * i for i in range(4)]
BACKGROUND = b'\x21\x03' * (WIDTH * HEIGHT)


def expected_frame(firmware: bytes, scale: bool) -> bytes:
    """Independent pixel oracle, using the verified native bands and XCI glyphs."""
    pixels = list(struct.unpack(f'<{WIDTH * HEIGHT}H', BACKGROUND))
    for y in SCALE_ROWS:
        pixels[y * WIDTH:(y + 1) * WIDTH] = [0] * WIDTH
    if not scale:
        return struct.pack(f'<{len(pixels)}H', *pixels)
    palette = native.palette(firmware)
    for x0, x1, index in native.scaled_bands(firmware):
        colour = native.pack16(native.ayuv_to_rgb(palette[index]))
        for y in range(PLACE['bar_y'], PLACE['bar_y'] + PLACE['bar_h']):
            pixels[y * WIDTH + x0:y * WIDTH + x1 + 1] = [colour] * (x1 + 1 - x0)
    glyphs = {}
    for name, address in native.GLYPHS:
        width, height, alpha = native.decode_xci(firmware, address)
        target_width = max(1, round(width * native.SCALE))
        target_height = PLACE['glyph_h']
        threshold = 128
        if (target_width, target_height) != (width, height):
            alpha = native.resize_glyph(width, height, alpha, target_width, target_height)
            threshold = native.RESIZED_COVERAGE
        glyphs[name] = (target_width, target_height, alpha, threshold)
    for x, first, second in native.scaled_labels():
        for name in (first, second):
            if name is None:
                continue
            width, height, alpha, threshold = glyphs[name]
            for y in range(height):
                for dx in range(width):
                    if alpha[y * width + dx] >= threshold:
                        pixels[(PLACE['label_y'] + y) * WIDTH + x + dx] = 0xFFFF
            x += glyphs['minus'][0]
    return struct.pack(f'<{len(pixels)}H', *pixels)


class FalseColorModel(CameraModel):
    """CameraModel with native UI boundaries, never replacements for module code."""

    def __init__(self, firmware, card, marks, heap=0x45000000, *, before_init=None,
                 fail_request=0):
        self.marks = marks
        self.before_init = before_init
        self.module_base = self.module_bytes = self.record = 0
        self.memset_return = 0
        self.patch_writes, self.pixel_writes = [], {}
        self.post_events, self.selectors, self.submitted = [], [], []
        self.expected_submit = None
        self.now = 1000
        super().__init__(firmware, card, heap, fail_request)
        self.u.mem_map(UI_STATE & ~4095, 4096)
        self.put(UI_STATE, 2)
        self.u.mem_map(META, 4096)
        self.put(MGR + 4, MGR_VT)
        self.put(MGR_VT + 0xC, RESOLVE)
        self.put(DRAWABLE, DRAW_VT)
        self.put(DRAW_VT + 0xC, FRONT, BACK, ROTATE, SETPAL, RESTORE)
        self.put(GEOM, WIDTH, HEIGHT)
        self.put(CAMERA_IF + 4, CTRL)
        for index, (descriptor, pixels) in enumerate(zip(DESCRIPTORS, PIXELS)):
            self.put(descriptor, 3 if index == 3 else 1, pixels, GEOM, 0)
            self.u.mem_map(pixels, 0x200000)
            self.u.mem_write(pixels, BACKGROUND)
        for address in STUBS:
            self.put(address, 0xE12FFF1E)

    def check_write(self, u, access, address, size, value, data):
        super().check_write(u, access, address, size, value, data)
        for base in PIXELS:
            if base <= address < base + 0x200000:
                assert address + size <= base + FRAME_BYTES, 'pixel write exceeds surface'
                assert size == 2 and (address - base) % 2 == 0, 'not a 16-bit pixel write'
                assert (address - base) // (WIDTH * 2) in SCALE_ROWS, 'paint escaped scale rows'
                assert value == 0 or value >> 12 == 15, 'painted pixel lacks opaque alpha'
                self.pixel_writes[address] = size
                return
        if address in STOCK:
            self.patch_writes.append((address, value))
        pc = u.reg_read(ar.UC_ARM_REG_PC)
        if self.module_base <= pc < self.module_base + self.module_bytes:
            assert not 0xC0000000 <= address < 0xC3000000, (
                f'module wrote firmware/cave directly at {address:#x}')

    def native_return(self, value):
        for register in (ar.UC_ARM_REG_R1, ar.UC_ARM_REG_R2, ar.UC_ARM_REG_R3, ar.UC_ARM_REG_IP):
            self.u.reg_write(register, 0xDEADBEEF)
        self.u.reg_write(ar.UC_ARM_REG_R0, value)
        self.u.reg_write(ar.UC_ARM_REG_PC, self.u.reg_read(ar.UC_ARM_REG_LR))

    def execute(self, u, address, size, data):
        if not self.module_base:
            for block in self.blocks:
                base = block['address']
                if (not block['freed'] and block['request'] != 1
                        and base <= address < base + block['size']
                        and self.get(base) == ABI['FP_MODULE_MAGIC']
                        and self.get(base + ABI['MOD_ID']) == MODULE_ID):
                    self.module_base, self.module_bytes = base, block['size']
                    assert address == base + self.marks['initialize']
                    self.api = u.reg_read(ar.UC_ARM_REG_R0)
                    self.record = u.reg_read(ar.UC_ARM_REG_R1)
                    if self.before_init:
                        self.before_init(self)
                    self.initial_bump = self.get(ABI['FP_CAVE_BUMP'])
                    self.initial_sites = {site: self.get(site) for site in STOCK}
                    self.initial_cave = bytes(u.mem_read(ABI['FP_CAVE_BEGIN'] + ABI['DIR_SIZE'],
                                                       ABI['FP_CAVE_END'] - ABI['FP_CAVE_BEGIN'] - ABI['DIR_SIZE']))
                    break
        if self.memset_return:
            if address == self.memset_return:
                self.memset_return = 0
            else:
                return  # Execute the actual verified firmware memset body.
        if address == MEMSET:
            assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0
            self.memset_return = u.reg_read(ar.UC_ARM_REG_LR)
            return
        if address in STOCK:
            return  # Execute the actual ARM branch installed at the stock site.
        if address == SUBMIT + 4:
            args = tuple(u.reg_read(getattr(ar, f'UC_ARM_REG_R{i}')) for i in range(4))
            assert args == self.expected_submit, ('stock submit arguments corrupted', args, self.expected_submit)
            self.submitted.append(args)
            sp = u.reg_read(ar.UC_ARM_REG_SP)
            saved = struct.unpack('<6I', u.mem_read(sp, 24))
            for register, value in zip((4, 5, 6, 7, 11, 14), saved):
                u.reg_write(getattr(ar, f'UC_ARM_REG_R{register}') if register < 13 else ar.UC_ARM_REG_LR, value)
            u.reg_write(ar.UC_ARM_REG_SP, sp + 24)
            self.native_return(1)
            return
        if address in (DRAW_MGR, POST, native.CLOCK, *STUBS):
            assert u.reg_read(ar.UC_ARM_REG_SP) % 8 == 0, 'unaligned native UI call'
            r0, r1 = (u.reg_read(ar.UC_ARM_REG_R0), u.reg_read(ar.UC_ARM_REG_R1))
            if address == DRAW_MGR:
                result = MGR
            elif address == RESOLVE:
                assert r0 == MGR and r1 == 0, 'wrong display layer resolved'
                self.selectors.append(r1)
                result = DRAWABLE
            elif address == FRONT:
                assert r0 == DRAWABLE
                result = DESCRIPTORS[1]
            elif address in (BACK, ROTATE, SETPAL, RESTORE):
                raise AssertionError('button path requested back buffer, rotation or palette mutation')
            elif address == POST:
                assert r0 == CTRL
                assert self.get(r1) in (0x21, 0x22) and bytes(u.mem_read(r1 + 4, 1)) == b'\1'
                self.post_events.append(self.get(r1))
                result = 1
            else:
                result = self.now
            self.native_return(result)
            return
        super().execute(u, address, size, data)

    def call(self, address, *arguments):
        self.u.reg_write(ar.UC_ARM_REG_SP, SP)
        self.u.reg_write(ar.UC_ARM_REG_LR, STOP)
        for i, value in enumerate(arguments):
            self.u.reg_write(getattr(ar, f'UC_ARM_REG_R{i}'), value & 0xFFFFFFFF)
        for i, register in enumerate(SAVED):
            self.u.reg_write(register, 0x12340000 + i)
        self.u.emu_start(address, STOP, count=20_000_000)
        assert self.u.reg_read(ar.UC_ARM_REG_PC) == STOP, f'{address:#x} did not return'
        assert self.u.reg_read(ar.UC_ARM_REG_SP) == SP, 'unbalanced stack'
        assert [self.u.reg_read(r) for r in SAVED] == [0x12340000 + i for i in range(8)], 'callee-saved register corrupted'
        return self.u.reg_read(ar.UC_ARM_REG_R0), self.u.reg_read(ar.UC_ARM_REG_R1)

    def query(self, operation):
        status, result = self.service('API_CALL', MODULE_ID, operation)
        assert status == 0, ('module not callable', signed(status))
        return result

    def press(self):
        self.pixel_writes.clear()
        return self.call(PRESS, CAMERA_IF)[0]

    def release(self, elapsed=0):
        self.now = (self.now + elapsed) & 0xFFFFFFFF
        self.pixel_writes.clear()
        assert self.call(RELEASE, CAMERA_IF)[0] == 1

    def hold(self, elapsed=native.HOLD_MS):
        self.press()
        self.release(elapsed)

    def submit(self, index=0, *, sub=0):
        self.pixel_writes.clear()
        self.expected_submit = (CTRL, DESCRIPTORS[index], sub, 0x55667788)
        assert self.call(SUBMIT, *self.expected_submit)[0] == 1

    def frame(self, index=0):
        return bytes(self.u.mem_read(PIXELS[index], FRAME_BYTES))

    def state(self, offset):
        return self.get(self.module_base + self.marks['fc_state'] + offset)

    def rows_written(self, index=0):
        base = PIXELS[index]
        return {(address - base) // (WIDTH * 2) for address in self.pixel_writes
                if base <= address < base + FRAME_BYTES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/module-fcscale-proof')
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {'status': 'RUNNING', 'cases': {}, 'modeled_services': [
        'USER allocation, card file I/O, cache publication calls, clock, task creation/start',
        'display manager and front descriptor', 'event post', 'stock submit continuation'],
        'not_exercised': ['AutoRun interpreter', 'physical button and LCD', 'USB transport',
                          'hardware cache coherence', 'concurrent scheduling', 'physical pixel colour order']}
    cases = report['cases']
    current_case = 'build_and_boot'
    model = None
    try:
        firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
        assert digest(firmware) == FIRMWARE_SHA256, 'requires the verified original fp 5.02 MAIN'
        report['firmware_sha256'] = FIRMWARE_SHA256
        card = build(upstream, out / 'card')
        metadata = json.loads((card / 'module_fcscale.json').read_text())
        marks = metadata['symbols']
        module = (card / 'module_fcscale.bin').read_bytes()
        report['module_sha256'] = digest(module)
        report['module_bytes'] = len(module)
        assert len(module) == metadata['bytes']
        vshl_bytes = (card / 'fpSup.BIN').stat().st_size
        assert vshl_bytes == metadata['vshl_bytes'] <= metadata['limit_bytes'] == 32768
        report['card_footprint'] = {'vshl_bytes': vshl_bytes, 'limit_bytes': 32768}
        scale, clear = expected_frame(firmware, True), expected_frame(firmware, False)

        def boot(*, hook=None, fail_request=0, heap=0x45000000):
            camera = FalseColorModel(firmware, card, marks, heap,
                                     before_init=hook, fail_request=fail_request)
            rows = camera.boot()
            assert len(rows) == 1 and camera.get(rows[0] + ABI['REC_ID']) == MODULE_ID, 'unexpected competing module'
            assert camera.service('API_LOOKUP', 0x100)[0] == 0, 'UI provider must not be packaged'
            assert camera.service('API_LOOKUP', 0x101)[0] == 0, 'counter demo must not be packaged'
            assert len(camera.tasks) == 1 and camera.tasks[0]['started'], 'USB shell task was not started'
            return camera, rows[0]

        model, row = boot()
        assert signed(model.get(row + ABI['REC_STATUS'])) == 0
        assert signed(model.get(row + ABI['REC_INIT_RESULT'])) == 0
        assert model.query(0) == 1 and model.query(9) == 1
        assert model.query(1) == model.query(2) == model.query(12) == 0
        assert model.query(8) == model.module_base + marks['fc_state']
        assert model.module_base + ABI['MOD_HEADER_BYTES'] <= model.query(8)
        assert model.query(8) + native.STATE_SIZE <= model.module_base + model.module_bytes
        owner = next(block for block in model.blocks if block['address'] == model.module_base)
        assert not owner['freed'] and owner['request'] != 1 and model.blocks[0]['freed']
        assert model.module_base != model.api, 'module state must not alias registry state'
        cave = model.query(10)
        assert ABI['FP_CAVE_BEGIN'] + ABI['DIR_SIZE'] <= cave < ABI['FP_CAVE_END']
        assert len(model.patch_writes) == 3
        for site, symbol in HOOKS.items():
            word = model.get(site)
            assert word >> 24 == 0xEA, 'hook must preserve the native caller LR'
            displacement = (word & 0xFFFFFF) << 2
            if displacement & 0x2000000:
                displacement -= 0x4000000
            veneer = (site + 8 + displacement) & 0xFFFFFFFF
            assert ABI['FP_CAVE_BEGIN'] + ABI['DIR_SIZE'] <= veneer <= ABI['FP_CAVE_END'] - 8
            assert model.get(veneer) == 0xE51FF004
            assert model.get(veneer + 4) == model.module_base + marks[symbol]
            if site == PRESS:
                assert cave == veneer, 'veneer diagnostic must describe the installed press hook'
        cases['automatic_boot_and_ownership'] = {
            'record_id': MODULE_ID, 'state': hex(model.query(8)), 'USER_module': hex(model.module_base),
            'staging_unmapped': True, 'competing_modules': [], 'shell_task_started': True,
            'module_bytes': len(module), 'press_veneer': hex(cave)}

        current_case = 'press_hold_release_and_native_drawing'
        model.release(30_000)
        assert model.query(1) == 0 and model.query(5) == 0 and not model.post_events
        model.press()
        assert model.query(1) == 1 and model.query(12) == 1
        assert model.post_events == [0x21] and not model.pixel_writes and not model.submitted
        model.release(120)
        assert model.query(1) == 1 and model.query(2) == model.query(12) == 0
        model.submit()
        assert model.frame() == BACKGROUND and not model.pixel_writes
        model.hold()
        assert model.query(1) == 2 and model.query(2) == 1 and model.query(5) == 1
        assert model.frame(1) == scale and model.frame() == BACKGROUND
        assert model.selectors and set(model.selectors) == {0} and len(model.submitted) == 1
        for index in range(3):
            model.submit(index)
            assert model.frame(index) == scale, f'buffer {index} differs from native pixel oracle'
            assert model.rows_written(index) == SCALE_ROWS
        assert {model.state(offset) for offset in (0x28, 0x2C, 0x30)} == set(PIXELS[:3])
        before = (model.query(1), model.query(2), model.query(5), list(model.post_events))
        model.release()
        model.release(30_000)
        assert before == (model.query(1), model.query(2), model.query(5), model.post_events)
        assert not model.pixel_writes and model.query(12) == 0
        render(struct.unpack(f'<{WIDTH * HEIGHT}H', model.frame()), out / 'scale.png')
        cases['press_hold_release_and_native_drawing'] = {
            'short_press_posts': [0x21], 'hold_threshold_ms': native.HOLD_MS,
            'three_buffers_match_every_native_pixel': True, 'stale_unpaired_release_ignored': True,
            'scale_sha256': digest(scale), 'preview': str(out / 'scale.png')}

        current_case = 'surface_gates_and_paint_lock'
        model.submit(3, sub=1)
        assert not model.pixel_writes and model.frame(3) == BACKGROUND
        for width, height in ((WIDTH - 1, HEIGHT), (WIDTH, PLACE['clear_y'] + PLACE['clear_h'] - 1)):
            model.put(GEOM, width, height)
            model.submit()
            assert not model.pixel_writes and model.frame() == scale
        model.put(GEOM, WIDTH, HEIGHT)
        model.put(UI_STATE, 9)
        model.submit()
        assert model.frame() == scale
        model.put(UI_STATE, 4)
        model.submit()
        assert all(model.frame(i) == clear for i in range(3)), 'gate closure did not clear all buffers'
        assert all(model.state(offset) == 0 for offset in (0x60, 0x64, 0x68))
        model.submit()
        assert not model.pixel_writes
        model.put(UI_STATE, 5)
        model.submit(1)
        assert not model.pixel_writes
        model.put(UI_STATE, 2)
        model.submit()
        assert model.frame() == scale
        model.put(model.query(8) + 0x6C, 1)
        model.put(UI_STATE, 5)
        model.submit()
        assert not model.pixel_writes and model.frame() == scale
        model.put(model.query(8) + 0x6C, 0)
        model.put(UI_STATE, 2)
        cases[current_case] = 'indexed layer and unsupported geometry unchanged; menu/playback clear all; held lock skips writes'

        current_case = 'state_transitions_and_clock_wrap'
        for index in range(3):
            model.submit(index)
        model.hold(native.HOLD_MS + 400)
        assert model.query(1) == 1 and model.query(2) == 0 and model.query(11) == native.HOLD_MS + 400
        assert all(model.frame(i) == clear for i in range(3)) and model.post_events == [0x21]
        model.hold(native.HOLD_MS - 1)
        assert model.query(1) == 0 and model.post_events == [0x21, 0x22]
        model.now = 0xFFFFFF00
        model.hold(native.HOLD_MS)
        assert model.query(1) == 2 and model.query(11) == native.HOLD_MS
        assert model.post_events == [0x21, 0x22, 0x21]
        model.submit()
        assert model.frame() == scale
        bump, patches, allocations = model.get(ABI['FP_CAVE_BUMP']), len(model.patch_writes), model.requests
        assert model.call(model.module_base + marks['initialize'], model.api, row)[0] == 0
        assert (model.get(ABI['FP_CAVE_BUMP']), len(model.patch_writes), model.requests) == (bump, patches, allocations)
        assert model.query(1) == 2 and model.frame() == scale, 'reinitialization reset live state'
        model.hold(80)
        assert model.query(1) == model.query(2) == 0
        assert model.post_events == [0x21, 0x22, 0x21, 0x22]
        assert all(model.frame(i) == clear for i in range(3))
        model.submit()
        assert not model.pixel_writes
        assert model.query(3) == 6 and model.query(4) == 9
        assert model.query(6) == len(model.submitted)
        cases[current_case] = {'events': model.post_events, 'presses': model.query(3),
                              'releases': model.query(4), 'submits': len(model.submitted),
                              'clock_wrap_hold': True, 'repeat_init_preserves_state': True}

        current_case = 'atomic_stock_conflicts'
        failures = {}

        def refusal(name, hook, result):
            rejected, rejected_row = boot(hook=hook)
            assert signed(rejected.get(rejected_row + ABI['REC_STATUS'])) == ABI['FP_EINIT'], name
            assert signed(rejected.get(rejected_row + ABI['REC_INIT_RESULT'])) == result, name
            assert {site: rejected.get(site) for site in STOCK} == rejected.initial_sites, name
            assert rejected.get(ABI['FP_CAVE_BUMP']) == rejected.initial_bump, name
            assert not rejected.patch_writes, name
            assert bytes(rejected.u.mem_read(ABI['FP_CAVE_BEGIN'] + ABI['DIR_SIZE'], len(rejected.initial_cave))) == rejected.initial_cave, name
            assert rejected.get(rejected_row + ABI['REC_IMAGE']) == rejected.get(rejected_row + ABI['REC_INVOKE']) == 0
            assert next(block for block in rejected.blocks if block['address'] == rejected.module_base)['freed'], name
            assert rejected.service('API_CALL', MODULE_ID, 1) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
            failures[name] = {'init_result': result, 'no_partial_hooks': True, 'module_freed': True,
                              'shell_task_started': True}

        for site in STOCK:
            refusal(f'stock_conflict_{site:08x}', lambda camera, site=site: camera.put(site, 0xE1A00000), -8)
        cases[current_case] = failures

        current_case = 'allocation_refusal_and_relocation'
        refused, refused_row = boot(fail_request=owner['request'])
        assert signed(refused.get(refused_row + ABI['REC_STATUS'])) == ABI['FP_ENOMEM']
        assert {site: refused.get(site) for site in STOCK} == STOCK
        assert not refused.patch_writes
        assert refused.service('API_CALL', MODULE_ID, 1) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
        relocated, relocated_row = boot(heap=0x49000000)
        assert signed(relocated.get(relocated_row + ABI['REC_STATUS'])) == 0
        assert relocated.query(8) != model.query(8)
        relocated.hold()
        relocated.submit()
        assert relocated.frame() == scale and relocated.post_events == [0x21]
        assert relocated.blocks[0]['freed']
        cases[current_case] = {'allocation_failure_left_stock_hooks': True,
                              'second_heap_base': hex(relocated.module_base),
                              'relocated_scale_sha256': digest(relocated.frame()),
                              'shell_task_started_in_both': True}

        current_case = 'shared_shutdown_lifetime'
        shutdowns = []
        for camera, forced_first in ((model, False), (relocated, True)):
            allocations = [(b['address'], b['freed']) for b in camera.blocks]
            for forced in (forced_first, not forced_first):
                camera.shutdown(forced=forced)
                assert {site: camera.get(site) for site in STOCK} == STOCK
                assert camera.service('API_CALL', MODULE_ID, 1) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
                assert [(b['address'], b['freed']) for b in camera.blocks] == allocations
            shutdowns.append({'forced_first': forced_first, 'all_hooks_restored': True,
                              'live_images_not_freed': True})
        cases[current_case] = shutdowns
        report['status'] = 'OFFLINE_ONLY_PASS'
    except (Exception, SystemExit) as error:
        report['status'] = 'FAIL'
        report['failed_case'] = current_case
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()
        if model is not None:
            report['registers'] = {name: hex(model.u.reg_read(getattr(ar, f'UC_ARM_REG_{name}')))
                                   for name in ('PC', 'SP', 'LR', 'R0', 'R1', 'R2', 'R3')}
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, result in cases.items():
        print(f'PASS {name}: {result}')
    print(f'{len(cases)} scenario groups passed; {out / "report.json"}')
    print('OFFLINE ONLY. USB task creation/start is modeled, not USB transport or camera validation.')


if __name__ == '__main__':
    main()
