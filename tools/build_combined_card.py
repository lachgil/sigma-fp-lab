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
sys.path[:0] = [str(SHELL), str(GYRO), str(ROOT / 'tools')]
from armasm import assemble, symbols
import build_base_card as gyro
import fplab_page

MENU_OFFSET = 0x50000
# The drawn panel: its code in the pool above the menu, its state in the cave
# above the menu's. It used to be pushed over USB by tools/overlay_deploy.py;
# the card carries it now, which is the only way it exists without a cable.
PANEL_OFFSET = 0x52000
PANEL_STATE = 0xC072FC00
# The GUI append probe (--ui-probe): observation only, see src/uiprobe.S.
PROBE_CODE = 0xC0732100         # empty in stock firmware, verified at build time
PROBE_STATE = 0xC0732300
PROBE_SITE = 0xC05E2CA8         # FUN_c05e2ca8, the element append; Thumb
PROBE_RESUME = PROBE_SITE + 4 + 1
# A third entry in the camera's own Resolution list (--native-entry).
# Mechanism from Vitaly Li's FP3K (tools/fp3k_native_menu.S in his handoff):
# widen the widget's range word, and replace the 89-byte list CSV in place.
# The CSV fits three rows only because the Popup column is dropped, rows use
# \n rather than \r\n, and the third row's TEXT is literal text instead of a
# string id -- which is also why the label needs no artwork.
NATIVE_CSV = 0xC0F8E7EC
NATIVE_RANGE = 0xC1A709BC
NATIVE_RANGE_THREE = 0x40
NATIVE_STOCK_RANGE = 0x803F
NATIVE_CSV_LEN = 89
NATIVE_PICK = 0xC0732400        # the selection hook, empty in stock
NATIVE_SITE = 0xC057A6A0        # MV_Resolution selection callback (ARM)
NATIVE_SITE_STOCK = 0xE92D40F0  # push {r4,r5,r6,r7,lr}, the displaced word
# The FP LAB row (--fplab-page). Caves verified empty in the stock image at
# build time; the row itself is generated and checked by tools/fplab_page.py.
INJECT_CODE = 0xC0793100
INJECT_STATE = 0xC0793400
INJECT_TABLE = 0xC0793420
INJECT_HEADER = 0xC0793500
INJECT_SITE = 0xC05E6400        # the NBU record interpreter; Thumb
INJECT_MENU = 0xC0794100
INJECT_RECORDS = 0xC07A4400
# The green fix (src/greenfix.S), armed from the menu's GREEN FIX row. Caves
# checked empty at build time; the hook word at 0xC0437E98 is NOT written here,
# the menu writes it when the row is switched on and puts the stock instruction
# back when it is switched off.
GREEN_DESC = 0xC0794200
GREEN_CODE = 0xC0794240
INJECT_SITE_STOCK = 0x4FF0E92D  # push.w {r4-r11,lr} then the start of vpush


def thumb_branch(site: int, target: int) -> int:
    """A Thumb-2 B.W (T4) at `site` reaching `target`, as one little word."""
    offset = target - (site + 4)
    if not -0x800000 <= offset < 0x800000 or offset % 2:
        raise SystemExit('branch target out of B.W range')
    sign = (offset >> 24) & 1
    j1 = (~((offset >> 23) & 1) ^ sign) & 1
    j2 = (~((offset >> 22) & 1) ^ sign) & 1
    first = 0xF000 | (sign << 10) | ((offset >> 12) & 0x3FF)
    second = 0x9000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    return first | (second << 16)

# Above the shell's worker (0xC072F050..0xC072F698) and its state block at
# 0xC072F000, so the debug and release cards share one address map.
BOOT = 0xC072F700
ROW = 0xC072F800
STATE = 0xC072FB00
GYRO_DIGEST = 'a34faf9bec9dcb0531a4de816e53f6b51c94817042ff16dc28a6723022a1265d'
FIRMWARE_DIGEST = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
# The loader build pads to 32 KB and refuses more; stage2 reads up to 0x20000,
# so a bigger image is a pad decision, not a loader limit. See repack_vbin.
BIN_PAD = 65536


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


def repack_vbin(path, extra, pad):
    """Add sections to a built VSHL.BIN, re-emitting the header and table.

    The FP LAB row's scene records are several kilobytes, and the loader build
    pads its image to 32 KB and refuses anything past it. That ceiling is the
    pad, not the loader: stage2 reads up to 0x20000 into pool+0x8000. So the
    big buffers are spliced in here rather than handed to the loader build,
    and the file is padded further.

    A card is copied with a card reader, which truncates. `putfile` over USB
    does not, so pushing a smaller image over a larger one would leave its tail
    behind -- the same reason the pad exists at all.
    """
    entry, sections = parse_vbin(path.read_bytes())
    sections = sections + [(address, blob) for address, blob in extra]
    for address, blob in sections:
        if len(blob) % 4:
            raise SystemExit(f'section at {address:#x} is not a whole number of words')
    body = b''.join(blob for _address, blob in sections)
    table = b''.join(struct.pack('<II', address, len(blob))
                     for address, blob in sections)
    blob = struct.pack('<4sIII', b'VBIN', len(sections), entry, len(body)) + table + body
    if len(blob) > pad:
        raise SystemExit(f'card image is {len(blob)} bytes, past the {pad} it pads to')
    path.write_bytes(blob + bytes(pad - len(blob)))
    return sections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'builds/combined-menu')
    parser.add_argument('--debug', action='store_true',
                        help='keep the USB shell in, so the camera can be asked '
                             'what it is actually doing. Same payload either '
                             'way; the SSD cannot be used while USB is the host')
    parser.add_argument('--native-entry', metavar='LABEL', nargs='?',
                        const='OG 3032x2012',
                        help='add LABEL as a third entry in the native '
                             'Resolution list (max 12 characters)')
    parser.add_argument('--ui-probe', action='store_true',
                        help='arm the observation-only GUI append hook at boot')
    parser.add_argument('--fplab-page', action='store_true',
                        help='add a real fifth row to Record Settings, built '
                             'from the stock Resolution row and fed to the NBU '
                             'interpreter at boot (src/nbuinject.S)')
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
               f'PANEL_BODY_OFF={panel_body:#x}',
               f'GREEN_CODE={GREEN_CODE:#x}')
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
        if args.native_entry:
            label = args.native_entry.encode('ascii')
            stock = firmware[NATIVE_CSV - 0xC0000000:
                             NATIVE_CSV - 0xC0000000 + NATIVE_CSV_LEN]
            if not stock.startswith(b'\xef\xbb\xbfNO,TEXT,Popup,IMAGE,'):
                raise SystemExit('the Resolution list CSV is not where expected')
            got = struct.unpack_from('<I', firmware, NATIVE_RANGE - 0xC0000000)[0]
            if got != NATIVE_STOCK_RANGE:
                raise SystemExit(f'range word is {got:#x}, expected '
                                 f'{NATIVE_STOCK_RANGE:#x}')
            csv = (b'\xef\xbb\xbf' + b'NO,TEXT,IMAGE,Enabled,Enabled2\r\n'
                   + b'1,0313,blank,1,2\n' + b'2,0314,blank,1,2\n'
                   + b'3,' + label + b',,1,2\n')
            if len(csv) != NATIVE_CSV_LEN:
                raise SystemExit(f'label must make the CSV exactly '
                                 f'{NATIVE_CSV_LEN} bytes; {len(label)} '
                                 f'characters gives {len(csv)}')
            # The loader copies whole words, and the table is packed with three
            # bytes of padding before the next one: carry them through unchanged.
            tail = firmware[NATIVE_CSV - 0xC0000000 + NATIVE_CSV_LEN:
                            NATIVE_CSV - 0xC0000000 + NATIVE_CSV_LEN + 3]
            # And the half that makes it do something: intercept the selection
            # so index 2 turns on open gate and dispatches FHD instead of the
            # invalid enum 4.
            got = struct.unpack_from('<I', firmware, NATIVE_SITE - 0xC0000000)[0]
            if got != NATIVE_SITE_STOCK:
                raise SystemExit(f'selection callback starts {got:#x}, '
                                 f'expected {NATIVE_SITE_STOCK:#x}')
            if any(firmware[NATIVE_PICK - 0xC0000000:
                            NATIVE_PICK - 0xC0000000 + 0x100]):
                raise SystemExit('selection hook cave is not empty in stock')
            pick = assemble(ROOT / 'src/nativepick.S',
                            (f'SEL_OG_OFF={syms["native_select_og"]:#x}',
                             f'SEL_STOCK_OFF={syms["native_select_stock"]:#x}'))
            branch = 0xEA000000 | (((NATIVE_PICK - (NATIVE_SITE + 8)) >> 2)
                                   & 0xFFFFFF)
            sections.extend([
                (NATIVE_CSV, csv + tail, 'native resolution list'),
                (NATIVE_RANGE, struct.pack('<I', NATIVE_RANGE_THREE),
                 'native resolution range'),
                (NATIVE_PICK, pick, 'native selection hook'),
                (NATIVE_SITE, struct.pack('<I', branch),
                 'native selection branch')])
        if args.ui_probe:
            # Thumb, so it cannot share the ARM assembler's defaults, and the
            # hook word is emitted as its own one-word section: the loader
            # writes every section before jumping to our boot, and boot runs
            # F_CACHE, so the site is coherent before anything executes it.
            if any(firmware[PROBE_CODE - 0xC0000000:PROBE_STATE - 0xC0000000 + 0x40]):
                raise SystemExit('probe cave is not empty in stock firmware')
            probe = assemble(ROOT / 'src/uiprobe.S',
                             (f'PROBE_STATE={PROBE_STATE:#x}',
                              f'PROBE_RESUME={PROBE_RESUME:#x}'))
            off = PROBE_CODE - (PROBE_SITE + 4)
            s = (off >> 24) & 1
            j1 = (~((off >> 23) & 1) ^ s) & 1
            j2 = (~((off >> 22) & 1) ^ s) & 1
            hw1 = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
            hw2 = 0x9000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
            sections.extend([(PROBE_CODE, probe, 'gui append probe'),
                             (PROBE_STATE, bytes(0x40), 'gui probe state'),
                             (PROBE_SITE, struct.pack('<I', hw1 | (hw2 << 16)),
                              'gui append hook')])
        if args.fplab_page:
            # A real fifth row in Record Settings. The scene's records are
            # byte-packed, so the row arrives through the interpreter itself:
            # see src/nbuinject.S and docs/menu/gui-resources.md.
            plan = fplab_page.build(firmware)
            payload = {
                'code': (INJECT_CODE, assemble(ROOT / 'src/nbuinject.S',
                                               (f'INJECT_STATE={INJECT_STATE:#x}',
                                                f'INJECT_TABLE={INJECT_TABLE:#x}',
                                                'INJECT_COUNT=3')), 'scene injector'),
                'state': (INJECT_STATE, bytes(0x10), 'scene injector state'),
                'header': (INJECT_HEADER, plan['header'], 'enlarged scene header'),
                'menu': (INJECT_MENU, plan['menu'], 'Menu declaration, one more child'),
                'records': (INJECT_RECORDS, plan['body'], 'FP LAB row records'),
            }
            table = b''
            for record, stock, buffer, mode in (
                    (plan['header_at'], firmware[plan['header_at'] - 0xC0000000:
                                                 plan['header_at'] - 0xC0000000
                                                 + plan['header_stock_size']],
                     payload['header'], 0),
                    (plan['menu_at'], plan['menu_stock'], payload['menu'], 0),
                    (plan['tail_at'], firmware[plan['tail_at'] - 0xC0000000:
                                               plan['tail_at'] - 0xC0000000
                                               + plan['tail_size']],
                     payload['records'], 1)):
                value = 0x811C9DC5
                for byte in stock:
                    value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
                table += struct.pack('<6I', record, len(stock), value, mode,
                                     buffer[0], len(buffer[1]))
            payload['table'] = (INJECT_TABLE, table, 'scene injector table')
            for address, blob, why in payload.values():
                if any(firmware[address - 0xC0000000:
                                address - 0xC0000000 + len(blob)]):
                    raise SystemExit(f'{why} cave at {address:#x} is not empty in stock')
            got = struct.unpack_from('<I', firmware, INJECT_SITE - 0xC0000000)[0]
            if got != INJECT_SITE_STOCK:
                raise SystemExit(f'the NBU interpreter starts {got:#x}, '
                                 f'expected {INJECT_SITE_STOCK:#x}')
            # The loader copies whole words. The table keeps each buffer's true
            # length, so the padding is never handed to the interpreter.
            sections.extend((address, blob + bytes(-len(blob) % 4), why)
                            for address, blob, why in payload.values())
            sections.append((INJECT_SITE, struct.pack('<I', thumb_branch(
                INJECT_SITE, INJECT_CODE)), 'scene injector hook'))
            # The row's label. Not a cave: this deliberately overwrites one
            # stock English string, the HDMI page's "DCI 4K 4096x2160", for a
            # format this body cannot output. fplab_page checks it reads that
            # before replacing it, and it is RAM only.
            sections.append((plan['label']['patch_at'], plan['label']['patch'],
                             f"row label {plan['label']['text']!r}"))

        # The green fix, placed but not armed: the menu's GREEN FIX row writes
        # the branch at 0xC0437E98 and puts the stock instruction back.
        green = assemble(ROOT / 'src/greenfix.S',
                         (f'GREEN_STATE={STATE + 76:#x}',
                          f'GREEN_DESC={GREEN_DESC:#x}'))
        green_syms = symbols(ROOT / 'src/greenfix.S',
                             (f'GREEN_STATE={STATE + 76:#x}',
                              f'GREEN_DESC={GREEN_DESC:#x}'))
        if any(firmware[GREEN_DESC - 0xC0000000:
                        GREEN_CODE - 0xC0000000 + len(green)]):
            raise SystemExit('the green fix cave is not empty in stock firmware')
        sections.extend([(GREEN_DESC, green[:green_syms['green_fix']],
                          'green fix 3:2 descriptor'),
                         (GREEN_CODE, green[green_syms['green_fix']:],
                          'green fix handler')])
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
        # Two reasons a section is spliced in after the loader build rather than
        # handed to it: buffers past a kilobyte do not fit its 32 KB image, and
        # hook words must be written LAST. The loader runs while the camera is
        # already up, so a hook armed before the buffers it reads would have a
        # window, however small, of pointing at zeros.
        def late(blob, why):
            return len(blob) > 1024 or why.endswith('hook')

        spliced = ([(address, blob) for address, blob, why in sections
                    if len(blob) > 1024 and not why.endswith('hook')]
                   + [(address, blob) for address, blob, why in sections
                      if why.endswith('hook')])
        for index, (address, blob, why) in enumerate(sections):
            if late(blob, why):
                continue
            path = tmp / f'{index}.bin'
            path.write_bytes(blob)
            cmd += ['--also-bin', f'{address:#x}:{path}']
        subprocess.run(cmd, check=True)
        if spliced:
            repack_vbin(args.out / 'VSHL.BIN', spliced, BIN_PAD)
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
        'menu_symbols': syms,
        'panel_offset': PANEL_OFFSET, 'panel_state': f'{PANEL_STATE:#x}',
        'panel_symbols': panel_syms, 'gyro_sections_sha256': digest,
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
