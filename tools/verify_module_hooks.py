#!/usr/bin/env python3
"""Offline ARM hook ownership, shutdown lifetime and retained-image regressions.

Executes the packaged loader/runtime/cave and native registration instructions.
The singleton/dispatch boundaries, USER allocator and cache operations are modeled;
this is not a hardware-cache, scheduler, or camera reboot proof. No camera access.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import struct

from unicorn import arm_const as ar

from build_module_card import build_card
from verify_modules import (ABI, CameraModel, DCACHE, ICACHE, FIRMWARE_SHA256,
                            H_GET, LOADER, POWEROFF_MANAGER, POWEROFF_REGISTER,
                            ROOT, STOP, digest, signed)

SITES = [0xC0100000 + index * 16 for index in range(12)]
REQUEST = STOP + 0x400
SENTINEL = 0xABCD1234
DSB, ISB = 0xF57FF04F, 0xF57FF06F
PUBLICATION = [('cache', DCACHE), ('cache', ICACHE), ('barrier', DSB), ('barrier', ISB)]


class HookModel(CameraModel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Default Unicorn masks interrupts, which would hide a missing runtime
        # critical section. Exercise the ordinary enabled-caller case instead.
        self.u.reg_write(ar.UC_ARM_REG_CPSR, self.u.reg_read(ar.UC_ARM_REG_CPSR) & ~0xC0)

    def check_write(self, u, access, address, size, value, data):
        if address in SITES:
            assert u.reg_read(ar.UC_ARM_REG_CPSR) & 0xC0 == 0xC0, (
                'hook mutation outside IRQ/FIQ exclusion')
        return super().check_write(u, access, address, size, value, data)

    def execute(self, u, address, size, user):
        if address in SITES:
            # Only the actual patched ARM B may execute at a synthetic test site.
            assert self.get(address) >> 24 == 0xEA
            return
        return super().execute(u, address, size, user)


class ClosingBootModel(HookModel):
    """Run real cave cleanup across a modeled native-call boundary.

    This is explicit reentrant dispatch, not a simulated RTOS scheduler.
    Saving/restoring the interrupted caller keeps the native call's inputs real.
    """
    RESUME = STOP + 0x300

    def __init__(self, *args, close_at, **kwargs):
        self.close_at = close_at
        self.interrupted = None
        self.injected = False
        super().__init__(*args, **kwargs)

    def execute(self, u, address, size, user):
        registers = [getattr(ar, f'UC_ARM_REG_R{i}') for i in range(13)] + [
            ar.UC_ARM_REG_SP, ar.UC_ARM_REG_LR, ar.UC_ARM_REG_CPSR]
        if address == self.RESUME:
            resume, values = self.interrupted
            for register, value in zip(registers, values):
                u.reg_write(register, value)
            u.reg_write(ar.UC_ARM_REG_PC, resume)
            return
        ready = ((self.close_at == 'registration' and address == POWEROFF_REGISTER
                  and len(self.poweroff_registration) == 1)
                 or (self.close_at == 'allocation' and address == H_GET
                     and self.requests == 1 and len(self.poweroff_registration) == 2))
        if ready and not self.injected:
            self.injected = True
            self.interrupted = (address, [u.reg_read(r) for r in registers])
            obj = self.get(POWEROFF_MANAGER + 0x0C)
            callback = self.get(self.get(obj) + 12)
            u.reg_write(ar.UC_ARM_REG_R0, obj)
            u.reg_write(ar.UC_ARM_REG_R1, 0)
            u.reg_write(ar.UC_ARM_REG_LR, self.RESUME)
            u.reg_write(ar.UC_ARM_REG_PC, callback)
            return
        super().execute(u, address, size, user)


def hook_source(identifier, hooks, result=0, close=False):
    """A real tiny ABI module; its target returns a distinguishable value."""
    rows = '\n'.join(f'    .word {site:#x}, {stock:#x}, 0, {SENTINEL:#x}'
                     for site, stock in hooks)
    installation = ''
    if hooks:
        installation = f'''
    adr r2, hook_rows
    adr r4, target
    mov r5, r2
    mov r6, #{len(hooks)}
1:  str r4, [r5, #8]
    add r5, r5, #16
    subs r6, r6, #1
    bne 1b
    mov r3, #{len(hooks)}
    ldr ip, [r0, #{ABI['API_INSTALL_HOOKS']}]
    blx ip
    cmp r0, #0
    bne 2f
'''
    shutdown = '''
    movw ip, #0x3a98
    movt ip, #0xc002
    blx ip
    ldr r0, [r0, #0x0c]
    ldr ip, [r0]
    ldr ip, [ip, #0x0c]
    mov r1, #0
    blx ip
''' if close else ''
    return f'''.syntax unified
.arm
.text
module_start:
.word {ABI['FP_MODULE_MAGIC']}, {ABI['FP_ABI']}, {identifier}
.word image_end - module_start, init - module_start, target - module_start
init:
    push {{r4-r6, lr}}
{installation}
{shutdown}
    mov r0, #{result}
2:  pop {{r4-r6, pc}}
target:
    movw r0, #{0x5600 + identifier}
    bx lr
hook_rows:
{rows}
image_end:
'''


def snapshot(model, sites=SITES):
    return (bytes(model.u.mem_read(ABI['FP_CAVE_BEGIN'],
                                  ABI['FP_CAVE_END'] - ABI['FP_CAVE_BEGIN'])),
            tuple(model.get(site) for site in sites))


def prepare_request(model, owner, sites, *, originals=None, targets=None):
    if originals is None:
        originals = [model.get(site) for site in sites]
    if targets is None:
        targets = [model.get(owner + ABI['REC_INVOKE'])] * len(sites)
    for index, (site, stock, target) in enumerate(zip(sites, originals, targets)):
        model.put(REQUEST + index * ABI['HOOK_SIZE'], site, stock, target, SENTINEL)
    return bytes(model.u.mem_read(REQUEST, len(sites) * ABI['HOOK_SIZE']))


def install(model, owner, sites, *, originals=None, targets=None, expected=0):
    request = prepare_request(model, owner, sites, originals=originals, targets=targets)
    before = snapshot(model)
    control = model.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF
    trace_at = len(model.trace)
    result = signed(model.service('API_INSTALL_HOOKS', owner, REQUEST, len(sites))[0])
    assert model.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF == control
    assert result == expected, (result, expected)
    if expected:
        assert snapshot(model) == before, 'failed batch changed code or cave ownership'
        assert bytes(model.u.mem_read(REQUEST, len(request))) == request, 'failed batch published outputs'
        return []
    veneers = [model.get(REQUEST + index * ABI['HOOK_SIZE'] + ABI['HOOK_VENEER'])
               for index in range(len(sites))]
    trace = model.trace[trace_at:]
    patch_indices = [index for index, event in enumerate(trace)
                     if event[0] == 'write' and event[1] in sites]
    assert len(patch_indices) == len(sites)
    first_patch, last_patch = min(patch_indices), max(patch_indices)
    assert [event for event in trace[:first_patch] if event[0] in ('cache', 'barrier')] == PUBLICATION
    after_patch = [event for event in trace[last_patch + 1:] if event[0] in ('cache', 'barrier')]
    assert after_patch == PUBLICATION
    for site, veneer, target in zip(sites, veneers,
                                    targets or [model.get(owner + ABI['REC_INVOKE'])] * len(sites)):
        assert ABI['FP_CAVE_BEGIN'] <= veneer <= ABI['FP_CAVE_END'] - 8
        assert model.get(veneer) == 0xE51FF004 and model.get(veneer + 4) == target
        assert model.call(site)[0] == 0x5600 + model.get(owner + ABI['REC_ID'])
    return veneers


def assert_revoked(model):
    root = ABI['FP_CAVE_BEGIN']
    assert model.get(root + ABI['DIR_RUNTIME']) == 0
    assert model.get(root + ABI['DIR_BYTES']) == 0
    assert signed(model.get(root + ABI['DIR_STATUS'])) == ABI['FP_ENOTREADY']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/module-hooks-proof')
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    assert digest(firmware) == FIRMWARE_SHA256
    spec = importlib.util.spec_from_file_location('hook_armasm', upstream / 'fp_usb_shell/armasm.py')
    assembler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembler)
    out.mkdir(parents=True, exist_ok=True)
    stock = {site: struct.unpack_from('<I', firmware, site - 0xC0000000)[0] for site in SITES}
    marks = assembler.symbols(ROOT / 'src/module_runtime.S')
    footprint = marks['cave_end'] - marks['cave_template']
    assert footprint <= ABI['FP_CAVE_END'] - ABI['FP_CAVE_BEGIN']

    def module(name, identifier, sites=(), result=0, close=False):
        source = out / f'{name}.S'
        source.write_text(hook_source(identifier, [(site, stock[site]) for site in sites], result, close))
        path = out / f'{name}.bin'
        path.write_bytes(assembler.assemble(source))
        return path

    plain = module('plain', 1)
    armed = module('armed', 1, SITES[:2])
    failing = module('failing', 2, SITES[2:4], -77)
    card = build_card(upstream, out / 'plain-card', [plain])
    armed_card = build_card(upstream, out / 'armed-card', [armed])
    failing_card = build_card(upstream, out / 'failing-card', [armed, failing])
    cases = {}
    sequence = 0

    def fresh(chosen=card, *, image=firmware, counts=(0, 0), fail=0):
        nonlocal sequence
        sequence += 1
        return HookModel(image, chosen, 0x45000000 + sequence * 0x100000,
                         fail, poweroff_counts=counts)

    model = fresh()
    owner, = model.boot()
    assert len(model.poweroff_registration) == 2
    registration_index = next(index for index, event in enumerate(model.trace) if event[0] == 'register')
    assert [event for event in model.trace[:registration_index]
            if event[0] in ('cache', 'barrier')][-4:] == PUBLICATION
    install(model, owner, SITES[:3])
    cases['atomic_install_and_branch_execution'] = True

    for mask in (0, 0x40, 0x80, 0xC0):
        masked = fresh()
        masked_owner, = masked.boot()
        control = (masked.u.reg_read(ar.UC_ARM_REG_CPSR) & ~0xC0) | mask
        masked.u.reg_write(ar.UC_ARM_REG_CPSR, control)
        install(masked, masked_owner, SITES[:2])
        install(masked, masked_owner, [SITES[0]], expected=ABI['FP_ECONFLICT'])
        masked.shutdown()
        assert masked.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF == control & 0xFF
        assert_revoked(masked)
    cases['all_incoming_interrupt_masks_restored'] = True

    root_slot = model.api + marks['lifecycle_root'] - marks['resident']
    saved_root = model.get(root_slot)
    for bad_api, null_root in ((0, False), (model.api + 4, False), (model.api, True)):
        if null_root:
            model.put(root_slot, 0)
        assert model.call(model.get(model.api + ABI['API_LOOKUP']), bad_api, 1)[0] == 0
        assert model.call(model.get(model.api + ABI['API_CALL']), bad_api, 1, 0) == (
            ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
        assert signed(model.call(model.get(model.api + ABI['API_INSTALL_HOOKS']),
                                 bad_api, 0, 0, 0)[0]) == ABI['FP_ENOTREADY']
        model.put(root_slot, saved_root)
    cases['null_and_stale_api_refused_before_argument_dereference'] = True

    # All validation errors leave all sites, ownership, outputs and bump intact.
    install(model, owner, SITES[3:5], originals=[stock[SITES[3]], stock[SITES[4]] ^ 1],
            expected=ABI['FP_ECONFLICT'])
    install(model, owner, [SITES[3], SITES[3]], expected=ABI['FP_ECONFLICT'])
    install(model, owner, [SITES[0]], expected=ABI['FP_ECONFLICT'])
    image = model.get(owner + ABI['REC_IMAGE'])
    image_end = image + model.get(owner + ABI['REC_BYTES'])
    for target in (image, image + ABI['MOD_HEADER_BYTES'] - 4, image_end, image_end + 4, image + 25):
        install(model, owner, [SITES[3]], targets=[target], expected=ABI['FP_EFORMAT'])
    for bad_site in (0, SITES[3] + 1, ABI['FP_CAVE_BEGIN'], 0xC2F30CC8, 0xC2F30000):
        install(model, owner, [bad_site], originals=[0], expected=ABI['FP_EFORMAT'])
    for bad_owner, pointer, count in ((0, REQUEST, 1), (owner + 4, REQUEST, 1),
                                      (owner + 1, REQUEST, 1), (owner, 0, 1),
                                      (owner, REQUEST + 1, 1), (owner, REQUEST, 0),
                                      (owner, REQUEST, 9), (owner, 0xFFFFFFF0, 2)):
        before = snapshot(model)
        assert signed(model.service('API_INSTALL_HOOKS', bad_owner, pointer, count)[0]) == ABI['FP_EFORMAT']
        assert snapshot(model) == before
    cases['conflicts_and_argument_bounds_atomic'] = True

    full = fresh()
    owner_full, = full.boot()
    install(full, owner_full, SITES[:8])
    install(full, owner_full, [SITES[8]], expected=ABI['FP_ENOSPACE'])
    cases['eight_hook_capacity_atomic'] = True

    before_free = list(model.events)
    trace_at = len(model.trace)
    control = model.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF
    model.shutdown()
    assert model.u.reg_read(ar.UC_ARM_REG_CPSR) & 0xFF == control
    assert_revoked(model)
    assert [model.get(site) for site in SITES[:3]] == [stock[site] for site in SITES[:3]]
    cleanup_trace = model.trace[trace_at:]
    last_restore = max(index for index, event in enumerate(cleanup_trace)
                       if event[0] == 'write' and event[1] in SITES[:3])
    assert [event for event in cleanup_trace[last_restore + 1:]
            if event[0] in ('cache', 'barrier')] == PUBLICATION
    assert model.events == before_free, 'shutdown freed a successful USER allocation'
    assert model.service('API_LOOKUP', 1)[0] == 0
    assert model.service('API_CALL', 1, 0) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
    assert signed(model.service('API_REPORT', 1, 9)[0]) == ABI['FP_ENOTREADY']
    assert signed(model.service('API_TICKS')[0]) == ABI['FP_ENOTREADY']
    before = snapshot(model)
    assert signed(model.service('API_INSTALL_HOOKS', 0, 0, 0)[0]) == ABI['FP_ENOTREADY']
    assert snapshot(model) == before
    for forced in (False, True, True, False):
        model.shutdown(forced=forced)
        assert snapshot(model) == before
    cases['closed_services_root_revocation_double_dispatch'] = True

    for forced_first in (False, True):
        detached = fresh(armed_card)
        detached.boot()
        detached.unmap_user_heap()
        detached.shutdown(forced=forced_first)
        detached.shutdown(forced=not forced_first)
        assert_revoked(detached)
        assert [detached.get(site) for site in SITES[:2]] == [stock[site] for site in SITES[:2]]
    cases['callbacks_execute_after_all_user_heap_unmapped'] = True

    for counts in ((0, 10), (10, 0), (10, 10)):
        partial = fresh(counts=counts)
        partial.boot(ABI['FP_EREGISTER'])
        assert partial.requests == 1 and partial.blocks[0]['freed']
        assert len(partial.poweroff_registration) == 2
        obj = partial.poweroff_registration[0][0]
        assert partial.poweroff_registration[1][0] == obj
        for forced, count in enumerate(counts):
            slots = POWEROFF_MANAGER + (0x34 if forced else 0x0C)
            assert (obj in [partial.get(slots + index * 4) for index in range(10)]) == (count < 10)
        callback = partial.get(partial.get(obj) + 12)
        partial.unmap_user_heap()
        partial.shutdown()
        partial.shutdown(forced=True)
        assert partial.get(partial.get(obj) + 12) == callback
        if min(counts) < 10:
            assert_revoked(partial)
    cases['both_partial_registration_directions_and_both_full'] = True

    for close_at in ('registration', 'allocation'):
        closing = ClosingBootModel(firmware, armed_card, 0x59000000, close_at=close_at)
        closing.boot(ABI['FP_ENOTREADY'])
        assert closing.injected
        assert_revoked(closing)
        assert [closing.get(site) for site in SITES[:2]] == [stock[site] for site in SITES[:2]]
        closing.shutdown()
        closing.shutdown(forced=True)
        assert_revoked(closing)
    cases['shutdown_during_registration_and_allocation_never_reopens_root'] = True

    rollback = fresh(failing_card)
    ready, failed = rollback.boot()
    assert signed(rollback.get(failed + ABI['REC_STATUS'])) == ABI['FP_EINIT']
    assert signed(rollback.get(failed + ABI['REC_INIT_RESULT'])) == -77
    assert rollback.get(failed + ABI['REC_IMAGE']) == 0
    assert [rollback.get(site) for site in SITES[2:4]] == [stock[site] for site in SITES[2:4]]
    assert rollback.call(SITES[0])[0] == 0x5601
    failed_block = next(block for block in rollback.blocks if block['request'] == 4)
    # The owner is uncallable, but a previously entered callback may still
    # return through this image. Keep its image and veneers until boot ends.
    assert not failed_block['freed']
    assert not any(event == ('free', failed_block['address']) for event in rollback.trace)
    assert rollback.service('API_CALL', 2, 0) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
    assert rollback.call(failed_block['address'] + struct.unpack_from(
        '<I', failing.read_bytes(), ABI['MOD_INVOKE'])[0])[0] == 0x5602
    install(rollback, ready, SITES[4:8])
    install(rollback, ready, [SITES[8]], expected=ABI['FP_ENOSPACE'])
    rollback.shutdown()
    rebooted = fresh(armed_card, image=rollback.retained_image())
    rebooted.boot()
    assert rebooted.call(SITES[0])[0] == 0x5601
    cases['failed_owner_detached_but_inflight_image_and_slots_retained'] = True

    closes = module('close-then-fail', 2, SITES[2:4], -77, close=True)
    never = module('not-started', 3, SITES[4:6])
    close_card = build_card(upstream, out / 'close-card', [armed, closes, never])
    closed_init = fresh(close_card)
    closed_init.boot(ABI['FP_ENOTREADY'])
    runtime_block = next(b for b in closed_init.blocks if b['request'] == 2)
    runtime_api = runtime_block['address']
    assert closed_init.get(runtime_api + ABI['API_COUNT']) == 2
    failed_row = closed_init.get(runtime_api + ABI['API_RECORDS']) + ABI['REC_SIZE']
    assert signed(closed_init.get(failed_row + ABI['REC_STATUS'])) == ABI['FP_EINIT']
    assert signed(closed_init.get(failed_row + ABI['REC_INIT_RESULT'])) == -77
    assert closed_init.get(failed_row + ABI['REC_IMAGE']) == 0
    assert len(closed_init.blocks) == 4 and all(not b['freed'] for b in closed_init.blocks[1:])
    assert [closed_init.get(site) for site in SITES[:6]] == [stock[site] for site in SITES[:6]]
    assert_revoked(closed_init)
    cases['init_shutdown_then_failure_preserves_inflight_code_and_skips_later_modules'] = True

    replaced = fresh(armed_card)
    replaced.boot()
    replaced.put(SITES[0], 0xE1A00000)
    replaced.shutdown()
    assert replaced.get(SITES[0]) == 0xE1A00000
    assert replaced.get(SITES[1]) == stock[SITES[1]]
    conflicted = fresh(armed_card, image=replaced.retained_image())
    conflict_row, = conflicted.boot()
    assert signed(conflicted.get(conflict_row + ABI['REC_STATUS'])) == ABI['FP_EINIT']
    assert signed(conflicted.get(conflict_row + ABI['REC_INIT_RESULT'])) == ABI['FP_ECONFLICT']
    assert conflicted.get(SITES[0]) == 0xE1A00000 and conflicted.get(SITES[1]) == stock[SITES[1]]
    cases['replacement_owner_preserved_and_warm_stock_conflict_rejected'] = True

    first = fresh(armed_card, counts=(2, 3))
    first_row, = first.boot()
    old_image = first.get(first_row + ABI['REC_IMAGE'])
    first.shutdown()
    first.shutdown(forced=True)
    retained = first.retained_image()
    assert any(retained[ABI['FP_CAVE_BEGIN'] - 0xC0000000:ABI['FP_CAVE_END'] - 0xC0000000])
    second = fresh(armed_card, image=retained, counts=(3, 2))
    assert all(second.get(site) == stock[site] for site in SITES[:2])
    second_row, = second.boot()
    new_image = second.get(second_row + ABI['REC_IMAGE'])
    assert new_image != old_image and second.call(SITES[0])[0] == 0x5601
    assert len(second.poweroff_registration) == 2
    assert second.get(ABI['FP_CAVE_BUMP']) == ABI['FP_CAVE_BEGIN'] + footprint
    cases['warm_retained_image_new_heap_reset_bss_and_reinstalled_hooks'] = True

    # A same-boot reload cannot reclaim even CLOSED code still in either list.
    before = snapshot(first)
    first.put(ABI['FP_CAVE_BUMP'], ABI['FP_CAVE_BEGIN'])
    prepare = first.api + marks['hooks_prepare'] - marks['resident']
    assert first.call(prepare)[0] == 0
    assert snapshot(first) == before
    cases['closed_storage_retained_through_both_native_lists'] = True

    for kind in ('unknown', 'active', 'tampered_closed'):
        if kind == 'unknown':
            refused = fresh()
            refused.put(ABI['FP_CAVE_BEGIN'], ABI['FP_DIRECTORY_MAGIC'], 0xDEADBEEF, 1234, 0)
        elif kind == 'active':
            refused = fresh(image=second.retained_image())
        else:
            refused = fresh(image=retained)
            callback_offset = marks['cave_cleanup'] - marks['cave_template']
            refused.put(ABI['FP_CAVE_BEGIN'] + callback_offset, 0xE1A00000)
        before = snapshot(refused)
        refused.call(LOADER)
        assert snapshot(refused) == before
        assert refused.requests == 1 and refused.blocks[0]['freed']
        assert not refused.poweroff_registration
    cases['unknown_active_and_code_identity_mismatch_refused'] = True

    # Allocation/catalog failures leave registered callback code entirely cave-owned.
    for failure in ('allocation', 'catalog'):
        broken = fresh(fail=2 if failure == 'allocation' else 0)
        expected = ABI['FP_ENOMEM']
        if failure == 'catalog':
            runtime = (card / 'runtime.bin').read_bytes()
            offset = broken.blob.index(runtime) + marks['catalog']
            blob = bytearray(broken.blob)
            struct.pack_into('<I', blob, offset, ABI['FP_CAPACITY'] + 1)
            broken.blob = bytes(blob)
            expected = ABI['FP_EFORMAT']
        broken.boot(expected)
        assert all(block['freed'] for block in broken.blocks)
        broken.shutdown()
        broken.shutdown(forced=True)
        assert_revoked(broken)
    cases['startup_failures_keep_only_safe_cave_callbacks'] = True

    report = {'status': 'OFFLINE_ONLY_NOT_CAMERA_VALIDATED',
              'firmware_sha256': FIRMWARE_SHA256, 'cave_bytes': footprint,
              'cave_capacity': ABI['FP_CAVE_END'] - ABI['FP_CAVE_BEGIN'],
              'native_registration': 'actual fp5.02 instructions; manager BSS and dispatcher boundary modeled',
              'not_proven': ['hardware cache effects', 'scheduler races', 'physical warm restart'],
              'cases': cases}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{len(cases)} hook lifecycle scenarios passed; cave {footprint}/852 bytes')
    print(f'Report: {out / "report.json"}')
    print('OFFLINE ONLY. No camera, card, settings or flash writes were performed.')


if __name__ == '__main__':
    main()
