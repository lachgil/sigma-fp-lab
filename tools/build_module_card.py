#!/usr/bin/env python3
"""Package trusted registry modules for offline fp 5.02 experiments only.

Uses an explicitly supplied upstream fpSup checkout without patching its loader
or builder. Generated artifacts are not approved for camera installation.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
MAX_MODULES = int(re.search(
    r'^#define FP_CAPACITY (\d+)$',
    (ROOT / 'src/module_abi.h').read_text(), re.M).group(1))
UINT32_MAX = 0xFFFFFFFF


def build_card(upstream: Path, out: Path, modules: list[Path], *, debug: bool = False) -> Path:
    """Embed module files in one runtime catalog and build an offline card."""
    if not 1 <= len(modules) <= MAX_MODULES:
        raise ValueError(f'requires between 1 and {MAX_MODULES} module files')

    upstream, out = upstream.resolve(), out.resolve()
    blobs = []
    total = 8 + 8 * len(modules)
    for path in modules:
        size = path.stat().st_size
        if size % 4:
            raise ValueError(f'module length is not word-aligned: {path}')
        if size > UINT32_MAX - total:
            raise ValueError('module catalog exceeds uint32 size')
        blob = path.read_bytes()
        if len(blob) != size:
            raise ValueError(f'module changed while being read: {path}')
        blobs.append(blob)
        total += size

    spec = importlib.util.spec_from_file_location(
        'module_card_armasm', upstream / 'fp_usb_shell/armasm.py')
    if spec is None or spec.loader is None:
        raise ImportError('cannot load upstream ARM assembler')
    assembler = importlib.util.module_from_spec(spec)
    # Import through the supplied path without leaving bytecode in upstream.
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(assembler)
    finally:
        sys.dont_write_bytecode = previous_bytecode

    out.mkdir(parents=True, exist_ok=True)
    with (out / 'build.log').open('w', encoding='utf-8') as log:
        source = ROOT / 'src/module_runtime.S'
        with redirect_stdout(log), redirect_stderr(log):
            runtime = assembler.assemble(source)
            marks = assembler.symbols(source)
        catalog_at = marks['catalog']
        if (catalog_at % 4 or catalog_at < 0
                or catalog_at + 8 != len(runtime)
                or runtime[catalog_at:] != b'\0' * 8):
            raise ValueError('runtime catalog must end in two aligned zero words')
        if catalog_at > UINT32_MAX - total:
            raise ValueError('runtime image exceeds uint32 size')

        catalog = bytearray(struct.pack('<II', len(blobs), total))
        offset = 8 + 8 * len(blobs)
        for blob in blobs:
            catalog.extend(struct.pack('<II', offset, len(blob)))
            offset += len(blob)
        for blob in blobs:
            catalog.extend(blob)
        runtime_path = out / 'runtime.bin'
        runtime_path.write_bytes(runtime[:catalog_at] + catalog)

        command = [sys.executable, '-B',
                   str(upstream / 'fp_usb_shell/build_autorun.py'),
                   '--loader', '--no-ep-patches',
                   '--boot-bin', f'{runtime_path}:0',
                   '--out', str(out / 'AutoRun.txt')]
        if not debug:
            command.append('--no-shell')
        log.flush()
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, 'FPSUP_NO_BAR': '1'})
        if result.returncode:
            raise RuntimeError(
                f'upstream card build failed ({result.returncode}); see {out / "build.log"}')
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--module', type=Path, action='append', required=True,
                        help='independently assembled module binary; repeat in registry order')
    parser.add_argument('--debug', action='store_true',
                        help='include upstream USB shell for a supervised hardware experiment')
    args = parser.parse_args()
    try:
        out = build_card(args.upstream, args.out, args.module, debug=args.debug)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Offline experimental artifacts: {out}')
    print('Not approved for camera installation; no deployment was performed.')


if __name__ == '__main__':
    main()
