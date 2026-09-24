#!/usr/bin/env python3
"""Execute compiled C modules through the unchanged boot loader and ABI1 API.

Offline only. The emitted ARM code executes; allocation, files, clock and cache
calls are modeled. No camera access or hardware cache-coherence claim.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from unicorn import arm_const as ar

from verify_modules import (
    ABI, CameraModel, FIRMWARE_SHA256, H_GET, ROOT, TICK, digest, signed,
)

MODULE_ID = 513
PROBE_ID = 17


class CModuleModel(CameraModel):
    """Nonzero fresh heap and a clock explicitly advanced by the scenario."""
    clock_value = 123456

    def execute(self, u, address, size, data):
        super().execute(u, address, size, data)
        if address == H_GET and self.requests != self.fail_request:
            block = self.blocks[-1]
            u.mem_write(block['address'], b'\xA5' * block['size'])
        elif address == TICK:
            u.reg_write(ar.UC_ARM_REG_R0, self.clock_value)


def compile_module(upstream, source, target, *arguments, rejected_symbol=None):
    command = [sys.executable, str(ROOT / 'tools/build_module.py'), str(source),
               '--upstream', str(upstream), '--out', str(target), *map(str, arguments)]
    result = subprocess.run(command, capture_output=True, text=True)
    diagnostic = result.stdout + result.stderr
    if rejected_symbol is not None:
        assert result.returncode != 0, f'accepted invalid module: {source.name}'
        assert not target.exists(), f'published invalid module: {source.name}'
        assert rejected_symbol in diagnostic, (rejected_symbol, diagnostic)
        return {'rejected': True, 'diagnostic': diagnostic.strip()}
    if result.returncode:
        raise RuntimeError(f'build failed: {source.name}\n{diagnostic}')
    assert target.is_file(), diagnostic
    return target


def exercise(firmware, card, heap):
    model = CModuleModel(firmware, card, heap)
    rows = model.boot()
    observed = [(model.get(r + ABI['REC_ID']), signed(model.get(r + ABI['REC_STATUS'])))
                for r in rows]
    assert observed == [(PROBE_ID, 0), (MODULE_ID, 0)], observed
    probe, record = rows
    assert model.service('API_LOOKUP', MODULE_ID)[0] == record
    assert model.service('API_LOOKUP', PROBE_ID)[0] == probe
    assert model.get(record + ABI['REC_INIT_RESULT']) == 0
    assert model.get(record + ABI['REC_VALUE']) == 100
    assert model.get(probe + ABI['REC_VALUE']) == 40
    initial_clock_calls = model.ticks
    # boot() has already required staging to be unmapped. Only the resident
    # allocations and public API remain available during these calls.
    values = []
    expected = 100
    arguments = (0, 1, 2, 3, 0xFFFFFFF0)
    for count, argument in enumerate(arguments, 1):
        model.clock_value += 7
        expected = (expected + argument + (3, 5, 11, 17)[argument & 3]
                    + ord('C') + count + 7) & 0xFFFFFFFF
        status, value = model.service('API_CALL', MODULE_ID, argument)
        assert (status, value) == (0, expected), (argument, status, value, expected)
        assert model.get(record + ABI['REC_VALUE']) == expected, 'shared report missing'
        # The assembly dependency continues to execute without modifying C state.
        assert model.service('API_CALL', PROBE_ID, 2) == (0, 40 + count * 5)
        assert model.get(probe + ABI['REC_VALUE']) == 40 + count * 5
        values.append(value)
        if count == 1:
            # The C callback rejects resetting an already-used instance. Its
            # pointer reads still must work: the wrapper must not relocate twice.
            result, _ = model.call(model.get(record + ABI['REC_INIT']), model.api, record)
            assert signed(result) == ABI['FP_EFORMAT']
    assert model.ticks == initial_clock_calls + len(arguments)
    assert len([b for b in model.blocks if not b['freed']]) == 3
    image = model.get(record + ABI['REC_IMAGE'])
    size = model.get(record + ABI['REC_BYTES'])
    for field in ('REC_INIT', 'REC_INVOKE'):
        assert image + ABI['MOD_HEADER_BYTES'] <= model.get(record + ABI[field]) < image + size
    return {
        'heap_base': hex(heap), 'c_image': hex(image), 'records': observed,
        'initial_report': 100, 'arguments': list(arguments), 'values': values,
        'clock_calls': model.ticks, 'staging_unmapped': True,
        'fresh_allocations_poisoned': True,
        'bss_and_initialized_pointer_checks': 'C init accepted and all calls matched',
        'assembly_dependency_final_report': model.get(probe + ABI['REC_VALUE']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/module-c-proof')
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    if digest(firmware) != FIRMWARE_SHA256:
        raise SystemExit('requires the verified original fp 5.02 MAIN image')
    out.mkdir(parents=True, exist_ok=True)

    probe = compile_module(upstream, ROOT / 'src/module_probe.S', out / 'probe.bin',
                           '-D', f'MODULE_ID={PROBE_ID}', '-D', 'INITIAL_VALUE=40', '-D', 'STEP=3')
    probe_hash = digest(probe.read_bytes())
    card = out / 'card'
    module = compile_module(upstream, ROOT / 'src/module_c_example.c', out / 'example.bin',
                            '--module-id', str(MODULE_ID), '--dependency', probe,
                            '--card-out', card)
    cases = {}
    for name, heap in (('relocation_base_a', 0x45000000), ('relocation_base_b', 0x57300000)):
        cases[name] = exercise(firmware, card, heap)
    assert cases['relocation_base_a']['c_image'] != cases['relocation_base_b']['c_image']
    assert cases['relocation_base_a']['values'] == cases['relocation_base_b']['values']
    assert digest(probe.read_bytes()) == probe_hash

    failed_card = out / 'failed-card'
    failed = compile_module(upstream, ROOT / 'src/module_c_example.c', out / 'failed.bin',
                            '--module-id', str(MODULE_ID), '-D', 'MODULE_INIT_RESULT=-77',
                            '--dependency', probe, '--card-out', failed_card)
    model = CModuleModel(firmware, failed_card, 0x61000000)
    probe_record, record = model.boot()
    assert signed(model.get(record + ABI['REC_STATUS'])) == ABI['FP_EINIT']
    assert signed(model.get(record + ABI['REC_INIT_RESULT'])) == -77
    assert model.get(record + ABI['REC_IMAGE']) == 0
    assert model.get(record + ABI['REC_INIT']) == 0
    assert model.get(record + ABI['REC_INVOKE']) == 0
    assert model.service('API_CALL', MODULE_ID, 1) == (ABI['FP_ENOTREADY'] & 0xFFFFFFFF, 0)
    assert model.service('API_CALL', PROBE_ID, 2) == (0, 45)
    assert model.get(probe_record + ABI['REC_VALUE']) == 45
    assert len([b for b in model.blocks if not b['freed']]) == 2
    cases['init_failure'] = {
        'status': ABI['FP_EINIT'], 'init_result': -77, 'failed_image_released': True,
        'failed_module_not_callable': True, 'assembly_dependency_value': 45,
    }

    # Invalid sources live only for this run; exercise the public build command,
    # its exit status, diagnostics and refusal to publish an artifact.
    with tempfile.TemporaryDirectory(prefix='fp-c-invalid-') as temporary:
        temporary = Path(temporary)
        sources = {
            'missing_init': (
                '#include "module_abi.h"\n'
                'uint32_t fp_module_invoke(const struct fp_api *a, '
                'const struct fp_record *r, uint32_t n) { return n; }\n',
                'fp_module_init'),
            'missing_invoke': (
                '#include "module_abi.h"\n'
                'int32_t fp_module_init(const struct fp_api *a, '
                'const struct fp_record *r) { return 0; }\n',
                'fp_module_invoke'),
            'unresolved_external': (
                '#include "module_abi.h"\n'
                'extern uint32_t unavailable_service(uint32_t);\n'
                'int32_t fp_module_init(const struct fp_api *a, '
                'const struct fp_record *r) { return 0; }\n'
                'uint32_t fp_module_invoke(const struct fp_api *a, '
                'const struct fp_record *r, uint32_t n) { return unavailable_service(n); }\n',
                'unavailable_service'),
        }
        for name, (source, symbol) in sources.items():
            path = temporary / f'{name}.c'
            path.write_text(source)
            cases[name] = compile_module(upstream, path, temporary / f'{name}.bin',
                                         '--module-id', str(MODULE_ID), rejected_symbol=symbol)

    report = {
        'status': 'OFFLINE_ONLY_NOT_CAMERA_VALIDATED',
        'firmware_sha256': FIRMWARE_SHA256,
        'discovery': 'public cave directory and versioned runtime service table',
        'module_sha256': {p.name: digest(p.read_bytes()) for p in (module, probe, failed)},
        'modeled_services': ['allocator with poisoned fresh memory', 'file I/O',
                             'D/I cache calls', 'clock advanced seven between invocations'],
        'not_exercised': ['AutoRun interpreter', 'physical camera', 'hardware caches',
                          'concurrency', 'unload', 'hot reload'],
        'cases': cases,
    }
    report_path = out / 'report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    for name, result in cases.items():
        print(f'{name}: {result.get("values", result)}')
    print(f'{len(cases)} C scenarios passed; report: {report_path}')
    print('OFFLINE ONLY. Generated boot files are not approved for camera installation.')


if __name__ == '__main__':
    main()
