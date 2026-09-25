#!/usr/bin/env python3
"""Build an isolated, marker-only fp 5.02 warm-start investigation card.

No deployment, settings storage, retention-timer patch, registry or early loader.
The unchanged upstream --boot-bin loader executes the installer from temporary
staging. Only its explicitly checked marker image survives in the cave arena.
Use --mode remove for a card that restores owned calls without clearing markers.
Optional --debug adds the upstream USB shell and its separate heap allocation;
the retained marker probe itself never relies on that shell or its allocation.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / 'analysis/MAIN_c0000000.bin'
MAIN_SHA = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
BASE = 0xC0000000
CAVE_BEGIN, CAVE_END = 0xC072EC60, 0xC072EFB4
STATE, STATE_SIZE = 0xC072ED60, 16
INIT_SITE, INIT_ORIG = 0xC0020B78, 0xC03DA440
READ_SITE, READ_ORIG = 0xC03DA420, 0xC03DA758
LOADER = 0xC072DE64


def branch(site: int, target: int) -> int:
    offset = target - site - 8
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise ValueError('target is outside aligned ARM BL range')
    return 0xEB000000 | ((offset >> 2) & 0xFFFFFF)


def firmware_bytes(path: Path) -> bytes:
    image = path.read_bytes()
    if hashlib.sha256(image).hexdigest() != MAIN_SHA:
        raise ValueError('requires the verified original SIGMA fp Ver.5.02 MAIN image')
    for site, target in ((INIT_SITE, INIT_ORIG), (READ_SITE, READ_ORIG)):
        actual = struct.unpack_from('<I', image, site - BASE)[0]
        if actual != branch(site, target):
            raise ValueError(f'expected original BL at {site:#x}, got {actual:#x}')
    if any(image[CAVE_BEGIN - BASE:CAVE_END - BASE]):
        raise ValueError('probe arena is not zero in original firmware')
    return image


def container_sections(raw: bytes) -> tuple[int, list[tuple[int, bytes]]]:
    if len(raw) < 16:
        raise ValueError('truncated upstream VBIN header')
    magic, count, entry, length = struct.unpack_from('<4sIII', raw)
    if magic != b'VBIN' or not 0 < count < 128:
        raise ValueError('invalid upstream VBIN header')
    cursor = 16 + count * 8
    end = cursor + length
    if end > len(raw):
        raise ValueError('truncated upstream VBIN body')
    sections = []
    for index in range(count):
        address, size = struct.unpack_from('<II', raw, 16 + index * 8)
        if not size or size % 4 or cursor + size > end:
            raise ValueError('invalid upstream section extent')
        sections.append((address, raw[cursor:cursor + size]))
        cursor += size
    if cursor != end:
        raise ValueError('upstream section lengths do not cover its body')
    return entry, sections


def build_card(upstream: Path, out: Path, *, mode: str = 'install',
               firmware: Path = MAIN, debug: bool = False) -> Path:
    """Build only local artifacts; never import a live-camera helper."""
    if mode not in ('install', 'remove'):
        raise ValueError('mode must be install or remove')
    firmware_bytes(firmware)
    upstream, out = upstream.resolve(), out.resolve()
    spec = importlib.util.spec_from_file_location(
        'boot_probe_armasm', upstream / 'fp_usb_shell/armasm.py')
    if spec is None or spec.loader is None:
        raise ImportError('cannot load the supplied upstream ARM assembler')
    assembler = importlib.util.module_from_spec(spec)
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(assembler)
    finally:
        sys.dont_write_bytecode = previous_bytecode

    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'src/boot_probe.S'
    defines = ('PROBE_REMOVE=1',) if mode == 'remove' else ()
    with (out / 'build.log').open('w', encoding='utf-8') as log:
        with redirect_stdout(log), redirect_stderr(log):
            payload = assembler.assemble(source, defines)
            marks = assembler.symbols(source, defines)
        start, end = marks['probe_image'], marks['probe_image_end']
        resident = payload[start:end]
        if (marks['boot_probe_entry'] != 0 or end != len(payload)
                or len(resident) != CAVE_END - CAVE_BEGIN
                or marks['probe_state'] - start != STATE - CAVE_BEGIN
                or not 0 < marks['probe_code_end'] - start <= STATE - CAVE_BEGIN
                or any(resident[STATE - CAVE_BEGIN:])):
            raise ValueError('probe code/state do not fit the exclusive arena layout')
        callbacks = {name: CAVE_BEGIN + marks[name] - start
                     for name in ('probe_init', 'probe_reader')}
        if any(address % 4 or not CAVE_BEGIN <= address < STATE
               for address in callbacks.values()):
            raise ValueError('callback is outside the resident code extent')
        payload_path = out / 'boot_probe.bin'
        payload_path.write_bytes(payload)
        (out / 'resident.bin').write_bytes(resident)
        command = [sys.executable, '-B',
                   str(upstream / 'fp_usb_shell/build_autorun.py'),
                   '--loader', '--no-ep-patches',
                   '--boot-bin', f'{payload_path}:0',
                   '--banner', f'fp diag {mode}',
                   '--out', str(out / 'AutoRun.txt')]
        if not debug:
            command.append('--no-shell')
        log.flush()
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'upstream build failed ({result.returncode}); see {out / "build.log"}')

    # An absolute section copy would erase retained evidence before the entry
    # could inspect it. No fixed section may touch our hooks or reservation.
    entry, sections = container_sections((out / 'fpSup.BIN').read_bytes())
    if sections.count((0, payload)) != 1:
        raise ValueError('probe entry must be a unique in-place upstream boot section')
    cursor = 16 + 8 * len(sections)
    entry_in_staging = False
    for address, blob in sections:
        if not address and cursor <= entry < cursor + len(blob):
            entry_in_staging = True
        cursor += len(blob)
    if not entry_in_staging or entry % 4:
        raise ValueError('upstream entry is not aligned inside an in-place staging section')
    if not debug and (len(sections) != 2 or sections[0][0] != 0
                      or sections[1] != (0, payload)
                      or entry != 32 + len(sections[0][1])):
        raise ValueError('marker-only card must contain just stage2 and the probe entry')
    protected = ((CAVE_BEGIN, CAVE_END), (INIT_SITE, INIT_SITE + 4),
                 (READ_SITE, READ_SITE + 4))
    for address, blob in sections:
        if 0 < address < 0x40000000:
            raise ValueError('probe card must not contain runtime-pool-relative sections')
        if address and any(address < hi and lo < address + len(blob)
                           for lo, hi in protected):
            raise ValueError('static upstream section would overwrite probe ownership')
    text = (out / 'AutoRun.txt').read_text()
    for address in re.findall(r'^mem set (0x[0-9a-fA-F]+) ', text, re.M):
        at = int(address, 16)
        if any(lo <= at < hi for lo, hi in protected):
            raise ValueError('AutoRun would overwrite probe ownership before validation')

    manifest = {
        'target': 'SIGMA fp Ver.5.02, not fp L', 'mode': mode,
        'purpose': 'Native startup marker diagnostic, not an earlier or faster loader',
        'residency': 'Retained callbacks and markers are entirely cave-resident; entry uses temporary upstream staging',
        'debug_shell_scope': ('Upstream USB shell uses separate heap memory; outside marker-only verification'
                              if debug else 'No USB shell or persistent heap allocation'),
        'firmware_sha256': MAIN_SHA, 'usb_shell': debug,
        'hardware_status': 'unmeasured; offline investigation only',
        'arena': {'begin': CAVE_BEGIN, 'end': CAVE_END, 'bytes': len(resident)},
        'state': {'address': STATE, 'bytes': STATE_SIZE,
                  'words': ['init_visits', 'boot_sequence', 'reader_visits', 'last_sequence']},
        'callbacks': callbacks, 'code_bytes': marks['probe_code_end'] - start,
        'installer_bytes': start, 'payload_bytes': len(payload), 'symbols': marks,
        'hooks': [{'site': site, 'original_target': original,
                   'original_word': branch(site, original),
                   'owned_word': branch(site, callbacks[name])}
                  for site, original, name in ((INIT_SITE, INIT_ORIG, 'probe_init'),
                                               (READ_SITE, READ_ORIG, 'probe_reader'))],
        'files': {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                  for name in ('AutoRun.txt', 'fpSup.BIN', 'boot_probe.bin', 'resident.bin')},
        'limitations': [
            'Isolated card only: no registry modules or other cave-arena users.',
            'Sequences count observer initialization visits, not measured physical boots.',
            'First installation misses the native boundaries that already ran that boot.',
            'Native readiness gate unchanged; not evidence of earlier SD-safe loading.',
            'Same-build installed images preserve all marker words without writes.',
            'Removed images remain resident; a new installation requires a genuine cold boot.',
            'Local IRQ/FIQ dispatch excluded during ownership checks, patch publication and marker updates.',
            'No cross-core lock or safety claim for arbitrary concurrent patch owners.',
            'No hardware retention, cache-coherency or power-cut behavior proved offline.',
            'Entry result is not displayed by upstream stage2; use supervised read-only inspection.',
        ],
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True,
                        help='explicit unchanged fpSup source checkout')
    parser.add_argument('--firmware', type=Path, default=MAIN)
    parser.add_argument('--out', type=Path,
                        help='default: builds/boot-probe/MODE')
    parser.add_argument('--mode', choices=('install', 'remove'), default='install')
    parser.add_argument('--debug', action='store_true',
                        help='include upstream USB shell (separate heap, outside marker-only proof), without endpoint push patches')
    args = parser.parse_args()
    try:
        out = build_card(args.upstream, args.out or ROOT / 'builds/boot-probe' / args.mode,
                         mode=args.mode, firmware=args.firmware, debug=args.debug)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Offline {args.mode} investigation card: {out}')
    print(f'Exclusive marker arena: {CAVE_BEGIN:#010x}..{CAVE_END:#010x}; state {STATE:#010x}')
    print('No deployment performed. No hardware warm-start or earlier SD readiness claim.')


if __name__ == '__main__':
    main()
