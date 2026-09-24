#!/usr/bin/env python3
"""Build the real False Color toggle/scale as the sole boot-enabled ABI1 module.

Requires the verified fp 5.02 MAIN image and an explicit upstream fpSup checkout.
The card retains the upstream USB shell; no host activation, module_ui, demo,
standalone cave deployment, camera access, or SD installation is performed.

    python3 tools/build_module_fcscale.py --upstream PATH --out DIR
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
import tempfile

from build_module_card import build_card

# Reuse only the native-scale generator, not its standalone build/deployment.
# Its legacy assembler import must not leave bytecode in the reference tree.
_previous_bytecode = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True
    import build_fcscale_autorun as native
finally:
    sys.dont_write_bytecode = _previous_bytecode

ROOT = Path(__file__).resolve().parent.parent
MODULE_ID = 0x102
BIN_LIMIT = 32768


def build(upstream: Path, out: Path) -> Path:
    """Return the output directory containing one FC module and a debug card."""
    upstream, out = upstream.resolve(), out.resolve()
    image = native.MAIN.read_bytes()
    firmware_sha = hashlib.sha256(image).hexdigest()
    if firmware_sha != native.MAIN_SHA:
        raise ValueError('requires the verified SIGMA fp Ver.5.02 MAIN image')
    sites = ((native.PRESS_SITE, native.SITE_STOCK, 'press'),
             (native.RELEASE_SITE, native.SITE_STOCK, 'release'),
             (native.SUBMIT_SITE, native.SUBMIT_STOCK, 'submit'))
    for address, expected, name in sites:
        actual = native.word(image, address)
        if actual != expected:
            raise ValueError(f'{name} at {address:#010x}: stock {actual:#010x}, '
                             f'expected {expected:#010x}')

    spec = importlib.util.spec_from_file_location(
        'fcscale_module_armasm', upstream / 'fp_usb_shell/armasm.py')
    if spec is None or spec.loader is None:
        raise ImportError('cannot load upstream ARM assembler')
    assembler = importlib.util.module_from_spec(spec)
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(assembler)
    finally:
        sys.dont_write_bytecode = previous_bytecode

    # In particular, never inherit native.STATE (0xC072E200), which belongs to
    # the old standalone card and now contains the upstream loader/shell.
    defines = tuple(value for value in native.defines()
                    if not value.startswith(('STATE=', 'PROBE='))) + ('PROBE=0',)
    generated = native.native_include(image)
    with tempfile.TemporaryDirectory(prefix='fp-module-fcscale-') as temporary:
        source_dir = Path(temporary)
        for name in ('module_fcscale.S', 'fcscale.S', 'module_abi.h'):
            (source_dir / name).write_bytes((ROOT / 'src' / name).read_bytes())
        # Quoted includes resolve beside these source snapshots. The shared
        # generated include and every upstream/reference file remain untouched.
        (source_dir / 'fcscale_native.inc.S').write_text(generated)
        source = source_dir / 'module_fcscale.S'
        blob = assembler.assemble(source, defines)
        marks = assembler.symbols(source, defines)

    expected_header = (0x444F4D46, 1, MODULE_ID, len(blob),
                       marks['initialize'], marks['invoke'])
    if (marks['module_start'] != 0 or marks['module_end'] != len(blob)
            or len(blob) % 4 or struct.unpack_from('<6I', blob) != expected_header):
        raise ValueError('assembled False Color module has an invalid ABI1 extent/header')
    if len(blob) > BIN_LIMIT:
        raise ValueError(f'fpSup.BIN bounds error: module alone is {len(blob)} bytes, '
                         f'exceeding the {BIN_LIMIT}-byte upstream image limit')
    for name in ('fc_state', 'fc_module_state', 'fc_press', 'fc_release', 'fc_submit'):
        if not 24 <= marks[name] < len(blob) or marks[name] % 4:
            raise ValueError(f'invalid resident False Color symbol: {name}')

    out.mkdir(parents=True, exist_ok=True)
    module_path = out / 'module_fcscale.bin'
    module_path.write_bytes(blob)
    try:
        build_card(upstream, out, [module_path], debug=True)
    except RuntimeError as error:
        # Keep upstream's implementation and ceiling unchanged, but surface its
        # actual image-size failure instead of hiding it behind a build-log path.
        log = (out / 'build.log').read_text()
        bounds = re.search(r'binary is (\d+) bytes, past the (\d+) it pads to', log)
        if bounds:
            raise ValueError(f'fpSup.BIN bounds error: {bounds[1]} bytes exceeds '
                             f'upstream {bounds[2]}-byte image limit') from error
        raise
    vshl_bytes = (out / 'fpSup.BIN').stat().st_size
    if vshl_bytes > BIN_LIMIT:
        raise ValueError(f'fpSup.BIN bounds error: {vshl_bytes} bytes exceeds '
                         f'{BIN_LIMIT}-byte image limit')
    metadata = {
        'module_id': MODULE_ID,
        'bytes': len(blob),
        'symbols': marks,
        'defines': defines,
        'geometry': native.geometry(),
        'firmware_sha256': firmware_sha,
        'native_include_sha256': hashlib.sha256(generated.encode()).hexdigest(),
        'module_sha256': hashlib.sha256(blob).hexdigest(),
        'vshl_bytes': vshl_bytes,
        'limit_bytes': BIN_LIMIT,
        'debug': True,
        'diagnostics': {
            'version': 0, 'mode': 1, 'scale': 2, 'presses': 3, 'releases': 4,
            'holds': 5, 'submits': 6, 'painted_frames': 7, 'state_address': 8,
            'armed': 9, 'veneer_address': 10, 'duration_ms': 11, 'outstanding': 12,
        },
    }
    (out / 'module_fcscale.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        out = build(args.upstream, args.out)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    print(f'False Color module 0x{MODULE_ID:x}: {out}')
    print('Boot-enabled toggle and native scale; upstream USB shell retained.')
    print('No camera or SD operations performed; hardware behavior needs verification.')


if __name__ == '__main__':
    main()
