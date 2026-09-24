#!/usr/bin/env python3
"""Build an ABI1 ARM assembly or freestanding C module, optionally with dependencies.

No camera access or deployment. Validating the header does not prove code is
position independent, hooks are compatible, or the module is safe to execute.
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

from build_module_card import ROOT, build_card
from build_module_c import compile_c_module

ABI = {name: int(value, 0) for name, value in re.findall(
    r'^#define (\w+) (-?(?:0x[0-9A-Fa-f]+|[0-9]+))$',
    (ROOT / 'src/module_abi.h').read_text(), re.M)}


def inspect_module(blob: bytes) -> dict:
    if len(blob) < ABI['MOD_HEADER_BYTES'] or len(blob) % 4:
        raise ValueError('module must contain a complete, word-aligned header/image')
    magic, version, identifier, size, initialize, invoke = struct.unpack_from('<6I', blob)
    if magic != ABI['FP_MODULE_MAGIC'] or version != ABI['FP_ABI']:
        raise ValueError('incompatible module magic or ABI')
    if identifier == 0 or size != len(blob):
        raise ValueError('module ID must be nonzero and declared size must match image')
    for name, offset in (('initialize', initialize), ('invoke', invoke)):
        if offset % 4 or not ABI['MOD_HEADER_BYTES'] <= offset < size:
            raise ValueError(f'{name} must be an aligned ARM entry inside the image')
    return {'id': identifier, 'abi': version, 'bytes': size,
            'initialize_offset': initialize, 'invoke_offset': invoke,
            'sha256': hashlib.sha256(blob).hexdigest()}


def build_c_module(source: Path, module_id: int, defines=(), *,
                   compiler: str | Path | None = None,
                   linker: str | Path | None = None) -> bytes:
    """Compile C callbacks into a checked, self-relocating ABI1 image."""
    return compile_c_module(source, module_id, ABI, defines, compiler=compiler, linker=linker)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='module .bin output')
    parser.add_argument('-D', '--define', action='append', default=[], metavar='NAME=VALUE')
    parser.add_argument('--module-id', type=lambda value: int(value, 0),
                        help='required nonzero uint32 identifier for C sources (accepts 0x...)')
    parser.add_argument('--compiler', type=Path,
                        help='C compiler: Clang (default: FP_MODULE_CLANG or clang)')
    parser.add_argument('--linker', type=Path,
                        help='C linker: LLD (default: FP_MODULE_LLD, ld.lld, or local toolchain)')
    parser.add_argument('--card-out', type=Path, help='also generate a boot card directory')
    parser.add_argument('--dependency', type=Path, action='append', default=[],
                        help='already-built module loaded before this module; repeat in load order')
    parser.add_argument('--debug', action='store_true', help='include debug USB shell in card')
    args = parser.parse_args()
    is_c = args.source.suffix == '.c'
    if is_c and args.module_id is None:
        parser.error('--module-id is required for C sources')
    if not is_c and (args.module_id is not None or args.compiler or args.linker):
        parser.error('--module-id, --compiler and --linker apply only to C sources')
    if (args.dependency or args.debug) and not args.card_out:
        parser.error('--dependency and --debug require --card-out')
    if args.source.resolve() == args.out.resolve():
        parser.error('source and output must differ')
    if any(path.resolve() == args.out.resolve() for path in args.dependency):
        parser.error('output must not overwrite a dependency')
    try:
        # Validate dependencies before writing anything; first declaration wins
        # in the runtime, so duplicate IDs are a packaging error here.
        dependencies = [inspect_module(path.read_bytes()) for path in args.dependency]
        if is_c:
            blob = build_c_module(args.source, args.module_id, args.define,
                                  compiler=args.compiler, linker=args.linker)
        else:
            spec = importlib.util.spec_from_file_location(
                'developer_armasm', args.upstream.resolve() / 'fp_usb_shell/armasm.py')
            if spec is None or spec.loader is None:
                raise ImportError('cannot load upstream assembler')
            assembler = importlib.util.module_from_spec(spec)
            sys.dont_write_bytecode = True
            spec.loader.exec_module(assembler)
            blob = assembler.assemble(args.source.resolve(), args.define)
        info = inspect_module(blob)
        ids = [entry['id'] for entry in dependencies] + [info['id']]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate module IDs in requested card')
        if len(ids) > ABI['FP_CAPACITY']:
            raise ValueError(f"card exceeds {ABI['FP_CAPACITY']} modules")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(blob)
        if args.card_out:
            build_card(args.upstream, args.card_out, args.dependency + [args.out], debug=args.debug)
        print(json.dumps({'module': str(args.out), **info,
                          'load_order': ids, 'card': str(args.card_out) if args.card_out else None}, indent=2))
        print('Built only. No camera/card writes. Run the relevant ARM verifier before deployment.')
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
