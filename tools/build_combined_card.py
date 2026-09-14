#!/usr/bin/env python3
"""Build the fp 5.02 RAM-only menu card, using the verified gyro release code."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / 'reference/fpSup/fp_usb_shell'
GYRO = ROOT / 'reference/fpSup/gyro'
sys.path[:0] = [str(SHELL), str(GYRO)]
from armasm import assemble, symbols
import build_base_card as gyro

MENU_OFFSET = 0x50000
# The drawn panel: its code in the pool above the menu, its state in the cave
# above the menu's. It used to be pushed over USB by tools/overlay_deploy.py;
# the card carries it now, which is the only way it exists without a cable.
PANEL_OFFSET = 0x52000
PANEL_STATE = 0xC072FC00
# Above the shell's worker (0xC072F050..0xC072F698) and its state block at
# 0xC072F000, so the debug and release cards share one address map.
BOOT = 0xC072F700
ROW = 0xC072F800
STATE = 0xC072FB00
GYRO_DIGEST = 'a34faf9bec9dcb0531a4de816e53f6b51c94817042ff16dc28a6723022a1265d'
FIRMWARE_DIGEST = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'


def parse_vbin(raw):
    magic, count, entry, length = struct.unpack_from('<4sIII', raw)
    if magic != b'VBIN' or not 0 < count < 128:
        raise ValueError('invalid VBIN header')
    offset = 16 + count * 8
    if offset + length > len(raw):
        raise ValueError('truncated VBIN')
    sections = []
    for i in range(count):
        at, size = struct.unpack_from('<II', raw, 16 + i * 8)
        if size % 4 or offset + size > len(raw):
            raise ValueError('invalid section size')
        sections.append((at, raw[offset:offset + size]))
        offset += size
    if offset != 16 + count * 8 + length:
        raise ValueError('VBIN body length mismatch')
    return entry, sections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/combined-menu')
    parser.add_argument('--debug', action='store_true',
                        help='keep the USB shell in, so the camera can be asked '
                             'what it is actually doing. Same payload either '
                             'way; the SSD cannot be used while USB is the host')
    parser.add_argument('--og60-sel', type=lambda s: int(s, 0), default=173,
                        help='FieldAngle selector for FHD/59.94 CinemaDNG. '
                             'Default 173, measured on hardware 2026-09-11: the '
                             'probe read 175 at FHD/29.97 and 173 at FHD/59.94. '
                             'Pass 0 to leave the M98 60P option refusing.')
    args = parser.parse_args()
    if not 0 <= args.og60_sel <= 0xFFFF:
        raise SystemExit('--og60-sel must be a 16-bit selector value')
    if args.og60_sel == 175:
        raise SystemExit('175 is the measured FHD/29.97 selector, not 59.94')
    args.out.mkdir(parents=True, exist_ok=True)
    firmware = (ROOT / 'analysis/MAIN_c0000000.bin').read_bytes()
    if hashlib.sha256(firmware).hexdigest() != FIRMWARE_DIGEST:
        raise SystemExit('requires the verified Sigma fp 5.02 MAIN image')
    sections = gyro.sections('gcsv')
    gyro.check(sections)
    digest = hashlib.sha256(b''.join(struct.pack('<II', a, len(b)) + b
                                   for a, b, _ in sections)).hexdigest()
    if digest != GYRO_DIGEST:
        raise SystemExit('gyro sections differ from verified gyro_og_test example')
    menu_source = ROOT / 'src/menu.S'
    # The panel is assembled first: the menu has to be told where menu_core
    # lands in the pool, and baking that in from the real symbol is what stops
    # the two drifting apart into a branch to nowhere.
    panel_source = ROOT / 'src/menu_overlay.S'
    panel_defines = (f'STATE_ADDR={PANEL_STATE:#x}',)
    panel = assemble(panel_source, panel_defines)
    panel_syms = symbols(panel_source, panel_defines)
    panel_core = PANEL_OFFSET + panel_syms['menu_core']
    panel_spawn = PANEL_OFFSET + panel_syms['menu_spawn']
    panel_body = PANEL_OFFSET + panel_syms['menu_body']
    # The SEL row's two hex counts are a working tool for whoever is editing
    # the geometry patches. On the public card the label stands alone.
    defines = (f'OG60_SEL={args.og60_sel}',
               f'SHOW_SEL={1 if args.debug else 0}',
               f'PANEL_OFF={panel_core:#x}',
               f'PANEL_SPAWN_OFF={panel_spawn:#x}',
               f'PANEL_BODY_OFF={panel_body:#x}')
    menu = assemble(menu_source, defines)
    syms = symbols(menu_source, defines)
    guards = list(struct.iter_unpack('<II', menu[syms['guard_table']:syms['labels']]))
    for address, expected in guards:
        actual = struct.unpack_from('<I', firmware, address - 0xC0000000)[0]
        if actual != expected:
            raise SystemExit(f'firmware guard mismatch at {address:#x}')
    if args.debug and args.out == ROOT / 'builds/combined-menu':
        raise SystemExit('--debug needs its own --out so it cannot be mistaken '
                         'for the release card')
    if any(firmware[BOOT - 0xC0000000:STATE - 0xC0000000 + 0x100]):
        raise SystemExit('combined cave region is not empty in stock firmware')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        trampoline = tmp / 'boot.S'
        trampoline.write_text(f'''.syntax unified
.arm
.text
.global entry
entry:
    movw r0, #0x7a7c
    movt r0, #0xc375
    ldr r0, [r0]
    cmp r0, #0
    bxeq lr
    add r0, r0, #0x50000
    movw r1, #{syms['boot']}
    add r0, r0, r1
    bx r0
''')
        sections.extend([(MENU_OFFSET, menu, 'menu'),
                         (PANEL_OFFSET, panel, 'drawn panel'),
                         (BOOT, assemble(trampoline), 'combined boot'),
                         (ROW, assemble(ROOT / 'src/rowpatch_gated.S'), 'gated geometry')])
        spans = [(a, a + len(b), why) for a, b, why in sections]
        # Include runtime state and loader/file/job reservations, not just code.
        spans += [(STATE, STATE + 0x100, 'menu state'),
                  (PANEL_STATE, PANEL_STATE + 0x180, 'panel state'),
                  (0xC072FA00, 0xC072FAC0, 'geometry state, selectors, probe, canvases, keep list'),
                  (0x7000, 0x28000, 'loader read window'),
                  (0x42000, 0x43000, 'gyro file object'),
                  (0x43000, 0x43414, 'gyro jobs')]
        for i, (lo, hi, why) in enumerate(spans):
            if lo < 0x40000000 and hi > 0x100000:
                raise SystemExit(f'{why} exceeds allocated pool')
            for lo2, hi2, why2 in spans[i + 1:]:
                if lo < hi2 and lo2 < hi:
                    raise SystemExit(f'{why} overlaps {why2}')
        cmd = [sys.executable, str(SHELL / 'build_autorun.py'), '--loader',
               '--vshl-entry', hex(BOOT), '--out', str(args.out / 'AutoRun.txt'),
               '--banner', 'fpLAB DEBUG' if args.debug else 'fpLAB MENU']
        # The endpoint patches exist for hook-push, which a card never does; the
        # interface patch travels with the shell and is what stops the host PTP
        # stack taking interface 0.
        cmd += ['--no-ep-patches'] if args.debug else ['--no-shell']
        for index, (address, blob, _) in enumerate(sections):
            path = tmp / f'{index}.bin'
            path.write_bytes(blob)
            cmd += ['--also-bin', f'{address:#x}:{path}']
        subprocess.run(cmd, check=True)
    raw = (args.out / 'VSHL.BIN').read_bytes()
    entry, packed = parse_vbin(raw)
    if entry != BOOT:
        raise SystemExit(f'packaged entry {entry:#x} is not the trampoline')
    # A debug build also carries the shell's worker, so check ours are present
    # and byte-identical rather than that the list matches exactly.
    for address, blob, why in sections:
        if (address, blob) not in packed:
            raise SystemExit(f'{why} at {address:#x} is not in the binary as built')
    manifest = {
        'target': 'SIGMA fp 5.02, not fp L',
        'hardware_status': 'combined cold boot and recordings not yet tested',
        'usb_shell': args.debug,
        'entry': hex(entry), 'menu_pool_offset': hex(MENU_OFFSET),
        'og60_selector': args.og60_sel or 'unmeasured: M98 60P option refuses',
        'menu_symbols': syms, 'gyro_sections_sha256': digest,
        'sections': [{'address': hex(a), 'bytes': len(b), 'name': why,
                      'sha256': hashlib.sha256(b).hexdigest()} for a, b, why in sections],
        'files': {name: hashlib.sha256((args.out / name).read_bytes()).hexdigest()
                  for name in ('AutoRun.txt', 'VSHL.BIN')},
    }
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Combined card: {args.out}\nGyro sections match working example; '
          f'{len(guards)} firmware guards; code and runtime reservations do not '
          f'overlap.\nM98 60P: ' + (f'selector {args.og60_sel} baked in.'
          if args.og60_sel else 'no selector yet, option refuses and shows the '
          'probe value; record FHD/59.94 CinemaDNG once to read it.'))


if __name__ == '__main__':
    main()
