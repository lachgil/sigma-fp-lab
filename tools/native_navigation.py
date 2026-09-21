#!/usr/bin/env python3
"""Decode a menu row's page-entry and return contract, from the firmware.

A row that opens a page is not just a jump. It is a `keyEvent` (Right/OK) whose
`controlAppState` names a destination application state, plus the
`controlAppVariable` records in the same key group that record where the opened
page returns to (`MENU_ReturnScreen`) and any context it carries.

The application state named by `sync-request` is resolved against a runtime
state registry, NOT the NBU scene directory. The two are different layers:

  scene directory   C18EB48C   name -> serialized scene bytes (the page frame)
  state registry    per GUI context, installed by C055F8F0(contexts,count,tbl)

So registering a private scene name in the directory does not by itself make a
selectable page. `controlAppState` resolves the name through C05ED3B8 (an FNV
name lookup over the context's state table at object+0x198+0x34), reached from
the action handler C05F1398 -> request path C05DC8E0 / C05DC9A0. A private page
therefore needs EITHER a registered private application state with that name, or
a row that reuses an existing stock state name.

This tool reports, for a row, every destination it names and whether that name
is also a known scene, so an integration can see what still has to be bound.

Firmware anchors (fp 5.02, base 0xC0000000), all confirmed by disassembly:

  C05F1398  controlAppState action handler (reads 8 properties, dispatches)
  C05F1440  controlAppState constructor (registers the 8-property schema)
  C05DC8E0  state request, no app-sync-request
  C05DC9A0  state request, with app-sync-request/to-uic
  C05DC178  state-machine core: walks the context's registered state table
  C05ED3B8  name -> state entry (FNV lookup); C05ED248 inserts an entry
  C055F8F0  install a state table into every GUI context (20-byte entries)
  C05DA3D0/C05DA51E  per-context state subsystem construct/teardown at +0x198

Read-only. No firmware patch, card build, or camera action.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import menu_resources as res
import nbu_components as nc
import nbu_scene as ns

KEY_NAMES = {0x0C: 'Right', 0x0D: 'Right(rel)', 0x14: 'Up', 0x18: 'Down',
             0x1C: 'OK', 0x1B: 'Menu/back', 0x26: 'Right(menu)', 0x28: 'Down(menu)',
             0x0D: 'Enter'}
JUMP_KEYS = {0x0C, 0x1C, 0x26, 0x0D}


def scene_names(image: bytes) -> set[str]:
    table = res.NBU_BASE + 12 + struct.unpack_from('>II', image, res.NBU_BASE + 12)[1]
    count = struct.unpack_from('>I', image, table + 12)[0]
    out = set()
    for i in range(count):
        offset = struct.unpack_from('>I', image, table + 16 + i * 32 + 4)[0]
        at = res.NBU_BASE + 20 + offset
        out.add(image[at:image.index(b'\x00', at)].decode())
    return out


def key_groups(image: bytes, scene: ns.Scene, root: int) -> list[dict]:
    """Group a row's records by the keyEvent that owns them, decoding actions."""
    picked, _ = ns.subtree(image, scene, root)
    groups: list[dict] = []
    current: dict | None = None
    for at, tag, size in picked:
        if tag not in nc.MASK_AT or tag not in ns.NAMED:
            continue
        name = nc.pool_text(image, ns.component_of(image, at, tag))
        fields = {n: nc.value(image, k, p).strip("'")
                  for n, k, p in nc.fields(image, at, tag, size)}
        if name == 'keyEvent':
            key = int(fields.get('args', '0x0'), 0)
            current = dict(key=key, label=KEY_NAMES.get(key, hex(key)),
                           at=hex(at + res.LOAD), states=[], variables=[])
            groups.append(current)
        elif name == 'controlAppState' and current is not None:
            current['states'].append(fields.get('sync-request'))
        elif name == 'controlAppVariable' and current is not None:
            dest = fields.get('destination-variable')
            if dest:
                current['variables'].append((dest, fields.get('value')))
    return groups


def inspect(image: bytes, scene_name: str, row: str) -> dict:
    scene = ns.scene(image, scene_name)
    roots = [struct.unpack_from('>I', image, at + 20)[0]
             for at, tag, _ in scene.records
             if tag == 0x10003
             and nc.pool_text(image, struct.unpack_from('>I', image, at + 28)[0]) == row]
    if len(roots) != 1:
        raise ValueError(f'{row!r} resolved to {len(roots)} objects in {scene_name}')
    known = scene_names(image)
    groups = key_groups(image, scene, roots[0])
    jumps, missing = [], []
    for group in groups:
        if group['key'] not in JUMP_KEYS:
            continue
        targets = [t for t in group['states'] if t and not t.startswith('0x')]
        returns = [d for d, _ in group['variables'] if d == 'MENU_ReturnScreen']
        for target in targets:
            jumps.append(dict(key=group['label'], target=target,
                              is_known_scene=target in known,
                              has_return_context=bool(returns)))
            if target not in known:
                missing.append(target)
    return dict(scene=scene_name, row=row, object=roots[0], groups=groups,
                jumps=jumps, undeclared_targets=sorted(set(missing)),
                note=('sync-request targets resolve against the per-context state '
                      'registry (C055F8F0/C05ED3B8), not the scene directory; '
                      'is_known_scene only reports whether a frame of that name '
                      'exists, not that the state is registered.'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('scene', help="scene name, e.g. MainB5")
    parser.add_argument('row', help="row object name, e.g. B5_8")
    parser.add_argument('--image', type=Path,
                        default=ROOT / 'analysis' / 'MAIN_c0000000.bin')
    args = parser.parse_args()
    report = inspect(args.image.read_bytes(), args.scene, args.row)
    print(f"{report['scene']}/{report['row']} (object {report['object']})")
    for jump in report['jumps']:
        frame = 'frame present' if jump['is_known_scene'] else 'NO frame'
        ret = 'return set' if jump['has_return_context'] else 'NO return context'
        print(f"  {jump['key']:>12} -> {jump['target']:<24} {frame}, {ret}")
    if report['undeclared_targets']:
        print(f"  targets without a scene frame: {report['undeclared_targets']}")
    print(f"  {report['note']}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(str(error))
