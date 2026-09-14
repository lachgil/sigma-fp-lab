#!/usr/bin/env python3
"""Build the FP LAB row for Record Settings, and prove it against the firmware.

Record Settings is NBU scene `B1_2_5`. Its four rows are objects under `Menu`
(id 0x59); each row carries its own value page. This builds a fifth row by
copying row `B1_2_5_1` wholesale, renumbering it into a private id range and
attaching it to `Menu`, then emitting the enlarged allocation header the native
interpreter needs before it builds anything.

What is copied and what is not:

  * copied: object declarations and every component whose header contribution
    is exactly known -- objectBase, drawText, drawRect, drawImage, keyEvent,
    focusEvent, controlFocus, controlValue, controlAppState, controlAppVariable,
    changePropertyByControl, screenEvent, scrollEvent, toggleVisible, list.
  * dropped: animationClip and animation-group records, and the controlAnimation
    actions that drive them. Their header entries are positional per group and
    that mapping is not proven yet, so the row is built without the focus
    highlight animation rather than with a guessed allocation.

`python fplab_page.py build` writes the injection payload and a report;
`python fplab_page.py verify` runs the firmware's own interpreter over the
result. Neither touches a camera.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import menu_resources as res
import nbu_scene as ns

SCENE = 'B1_2_5'
DONOR_ROW = 4601          # B1_2_5_4, the page's last row, with its value page
MENU = 0x59               # the page's row container
PRIVATE_ID = 0xF000       # private object ids; no stock scene reaches this
DROP_COMPONENTS = ('controlAnimation',)
# Row containers carry one objectBase property: rows 2, 3 and 4 hold 81.0,
# 162.0 and 162.0, and row 1 has no property at all. The first three are one
# row apart, so 81 is the pitch; row 4 repeating 162 is not understood, and
# whether a layout component overrides these is not established either. The new
# row asks for the next slot down and the hardware trial reports where it lands.
ROW_PITCH = 81.0
ROW_SLOT = 243.0
OUT = ROOT / 'builds/fplab-page'


def component(image: bytes, at: int, tag: int) -> str | None:
    offset = ns.component_of(image, at, tag)
    if offset is None:
        return None
    pool = res.NBU_BASE + 20 + offset
    return image[pool:image.index(b'\x00', pool)].decode()


def activation_records(image: bytes, picked: list[tuple[int, int, int]]) -> set[int]:
    """The Right and OK key records of the row, and the actions they fire.

    A key record names its key at +0x14 and its event index at +0x1C; the
    actions that run for it carry the same object and index at +0x14/+0x18.
    """
    pool = res.NBU_BASE + 20

    def text(offset: int) -> str:
        return image[pool + offset:image.index(b'\x00', pool + offset)].decode()

    events, dropped = set(), set()
    for at, tag, _size in picked:
        if tag != 0x10006 or component(image, at, tag) != 'keyEvent':
            continue
        key = text(struct.unpack_from('>I', image, at + 20)[0])
        if key in ('Right', 'OK'):
            obj, index = struct.unpack_from('>2I', image, at + 24)
            events.add((obj, index))
            dropped.add(at)
    for at, tag, _size in picked:
        if tag in (0x10003, 0x10006) or tag not in ns.NAMED:
            continue
        obj, index = struct.unpack_from('>2I', image, at + 20)
        if (obj, index) in events:
            dropped.add(at)
    return dropped


def named_child(image: bytes, scene, root: int, wanted: str) -> int:
    """The object called `wanted` somewhere under `root`."""
    pool = res.NBU_BASE + 20
    picked, _ = ns.subtree(image, scene, root)
    for at, tag, _size in picked:
        if tag != ns.DECLARATION:
            continue
        offset = struct.unpack_from('>I', image, at + 28)[0]
        if image[pool + offset:image.index(b'\x00', pool + offset)].decode() == wanted:
            return struct.unpack_from('>I', image, at + 20)[0]
    raise SystemExit(f'no {wanted!r} object under {root:#x}')


def place_row(image: bytes, scene, root: int) -> dict[int, bytes]:
    """Move the copied row down one slot, through its objectBase property."""
    at = next(a for a, tag, _size in scene.records
              if tag == 0x10004 and struct.unpack_from('>I', image, a + 20)[0] == root)
    size = struct.unpack_from('>I', image, at + 4)[0]
    record = bytearray(image[at:at + size])
    if struct.unpack_from('>I', record, 28)[0] != 1:
        raise SystemExit('the donor row does not carry exactly one placement property')
    struct.pack_into('>f', record, size - 4, ROW_SLOT)
    return {at: bytes(record)}

# The row's own label. Localization keys are referenced by NBU pool offset, and
# changing that reference is the one label mechanism proven on hardware
# (2026-09-12: the Shoot page 5 Zebra row displayed "False Color" after its
# label word was changed). Every four-digit key in the pool is referenced by at
# least one record, so there is no unused one to borrow; `1636` is referenced
# exactly once, by the HDMI output-format page, for a format this body cannot
# output. Its English slot is 16 characters, so "FP LAB" fits with room to
# spare, and the patch is RAM only.
#
# Whether a patched .nloc value reaches the LCD has never been confirmed
# visually. If it does not, the row will read "DCI 4K 4096x2160", which is
# still unambiguous evidence that the row is ours -- and an answer to that
# second question.
LABEL_KEY = '1636'
LABEL_STOCK = 'DCI 4K 4096x2160'
LABEL_TEXT = 'FP LAB'
LOCALE = '../Common/Strings/English.nloc'


def label_row(image: bytes, scene, root: int) -> dict[int, bytes]:
    """Point the row's Name label at our key instead of the donor's."""
    pool = res.NBU_BASE + 20
    picked, _ = ns.subtree(image, scene, root)
    name = None
    for at, tag, _size in picked:
        if tag != ns.DECLARATION:
            continue
        offset = struct.unpack_from('>I', image, at + 28)[0]
        if image[pool + offset:image.index(b'\x00', pool + offset)] == b'Name':
            name = struct.unpack_from('>I', image, at + 20)[0]
    key = key_offset(image, LABEL_KEY)
    out = {}
    for at, tag, size in picked:
        if (tag == 0x10005 and struct.unpack_from('>I', image, at + 20)[0] == name
                and struct.unpack_from('>I', image, at + 28)[0] == 0x40000011):
            record = bytearray(image[at:at + size])
            struct.pack_into('>I', record, 32, key)
            out[at] = bytes(record)
    if len(out) != 1:
        raise SystemExit(f'expected one label field on the row, found {len(out)}')
    return out


def key_offset(image: bytes, key: str) -> int:
    """NBU pool offset of a localization key, which must already be there."""
    pool = res.NBU_BASE + 20
    end = res.NBU_BASE + 12 + struct.unpack_from('>I', image, res.NBU_BASE + 16)[0]
    wanted = key.encode() + b'\x00'
    found = [at for at in range(pool, end - len(wanted))
             if image[at:at + len(wanted)] == wanted and image[at - 1] == 0]
    if len(found) != 1:
        raise SystemExit(f'{key!r} is not a unique NBU pool string ({len(found)})')
    return found[0] - pool


def label_patch(image: bytes) -> dict:
    """The word-aligned English.nloc slice that renames our key's text."""
    entry = next(e for e in res.resources(image) if e.name == LOCALE)
    at, stock = res.localized_text(image, entry)[LABEL_KEY]
    if stock != LABEL_STOCK:
        raise SystemExit(f'key {LABEL_KEY} reads {stock!r}, not {LABEL_STOCK!r}')
    if len(LABEL_TEXT) > len(stock):
        raise SystemExit('the replacement label is longer than its slot')
    low = at & ~3
    high = (at + len(stock) + 1 + 3) & ~3
    slice_ = bytearray(image[low:high])
    slice_[at - low:at - low + len(stock) + 1] = (LABEL_TEXT.encode()
                                                 + bytes(len(stock) + 1 - len(LABEL_TEXT)))
    return dict(patch_at=low + res.LOAD, patch=bytes(slice_))


def build(image: bytes, with_page: bool = False) -> dict:
    """Build the row. With `with_page`, its stock value list comes too.

    Without the list the row is deliberately inert on Right and OK: those key
    records and their actions are dropped, so nothing can open a page that was
    not copied. The row still draws and takes focus, which is what the first
    hardware trial has to establish. The full row with its list is 11,048 bytes
    of records and does not fit the card's 32 KB image; the row alone does.
    """
    scene = ns.scene(image, SCENE)
    picked, _ = ns.subtree(image, scene, DONOR_ROW)
    inert = set()
    if not with_page:
        listed, _ = ns.subtree(image, scene, named_child(image, scene, DONOR_ROW, 'List'))
        inert = {at for at, _tag, _size in listed}
        inert |= activation_records(image, picked)
    kept, dropped = [], collections.Counter()
    for at, tag, size in picked:
        name = component(image, at, tag)
        if tag in (ns.CLIP, ns.GROUP) or name in DROP_COMPONENTS or at in inert:
            dropped[name or f'{tag:#x}'] += 1
            continue
        kept.append((at, tag, size))
    donor = ns.Scene(scene.name, scene.start, scene.end, scene.header, kept)
    objects = [struct.unpack_from('>I', image, at + 20)[0]
               for at, tag, _ in kept if tag == ns.DECLARATION]
    identifiers = {obj: PRIVATE_ID + index for index, obj in enumerate(objects)}
    declared = {struct.unpack_from('>I', image, at + 20)[0]
                for at, tag, _ in scene.records if tag == ns.DECLARATION}
    if set(identifiers.values()) & declared:
        raise SystemExit('private id range collides with the stock scene')
    rewrites = place_row(image, scene, DONOR_ROW)
    rewrites |= label_row(image, scene, DONOR_ROW)
    header, body = ns.graft(image, scene, donor, DONOR_ROW, MENU, identifiers,
                            rewrites)

    menu_at = next(at for at, tag, _ in scene.records if tag == ns.DECLARATION
                   and struct.unpack_from('>I', image, at + 20)[0] == MENU)
    capacity = ns.child_capacity(image, menu_at)
    menu_size = struct.unpack_from('>I', image, menu_at + 4)[0]
    menu = ns.set_child_capacity(image[menu_at:menu_at + menu_size], capacity + 1)

    tail_at, _tail_tag, tail_size = scene.records[-1]
    grown = ns.decode_header(header)
    return dict(
        scene=SCENE,
        scene_at=scene.start + res.LOAD,
        header_at=scene.start + res.LOAD,
        header_stock_size=struct.unpack_from('>I', image, scene.start + 4)[0],
        header=header,
        menu_at=menu_at + res.LOAD,
        menu_stock=image[menu_at:menu_at + menu_size],
        menu=menu,
        tail_at=tail_at + res.LOAD,
        tail_size=tail_size,
        body=body,
        records=len(kept),
        dropped=dict(dropped),
        identifiers=identifiers,
        objects=(scene.header.objects, grown.objects),
        capacity=(capacity, capacity + 1),
        label=dict(key=LABEL_KEY, stock=LABEL_STOCK, text=LABEL_TEXT,
                   **label_patch(image)),
    )


def verify(image: bytes, plan: dict) -> dict:
    """Run the firmware's interpreter over the extended scene's declarations."""
    from native_scene_vm import SceneVM        # local, emulation-only helper

    scene = ns.scene(image, SCENE)
    stock_header = image[scene.start:scene.start + plan['header_stock_size']]
    menu_at = plan['menu_at'] - res.LOAD

    def run(header: bytes, menu: bytes, extra: bytes):
        vm = SceneVM(image)
        vm.record(header)
        for at, tag, size in scene.records:
            if tag == ns.DECLARATION:
                vm.record(menu if at == menu_at else image[at:at + size])
        at = 0
        while at < len(extra):
            tag, size = struct.unpack_from('>II', extra, at)
            if tag == ns.DECLARATION:
                vm.record(extra[at:at + size])
            at += size
        return vm

    stock = run(stock_header, image[menu_at:menu_at + 36], b'')
    grown = run(plan['header'], plan['menu'], plan['body'])
    identifiers = list(plan['identifiers'].values())
    checks = {
        'stock objects': stock.created() == scene.header.objects,
        'extended objects': grown.created() == plan['objects'][1],
        'stock Menu children': stock.children(MENU) == plan['capacity'][0],
        'extended Menu children': grown.children(MENU) == plan['capacity'][0] + 1,
        'every stock id still resolves': all(grown.lookup(obj) for obj in
                                             {struct.unpack_from('>I', image, at + 20)[0]
                                              for at, tag, _ in scene.records
                                              if tag == ns.DECLARATION}),
        'every new id resolves': all(grown.lookup(obj) for obj in identifiers),
        'new row is a child of Menu': grown.parent(identifiers[0]) == MENU,
    }
    # Negative control: the same records with the stock header overrun the
    # object-pointer table the firmware sized from it.
    control = SceneVM(image)
    control.record(stock_header)
    for at, tag, size in scene.records:
        if tag == ns.DECLARATION:
            control.record(plan['menu'] if at == menu_at else image[at:at + size])
    overruns = control.watch_table(plan['body'])
    checks['unchanged header overruns the object table'] = bool(overruns)
    return dict(checks=checks, overruns=overruns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, default=res.IMAGE)
    parser.add_argument('--out', type=Path, default=OUT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build')
    commands.add_parser('verify')
    args = parser.parse_args()
    image = args.image.read_bytes()
    plan = build(image)
    report = {
        'scene': plan['scene'], 'scene_at': hex(plan['scene_at']),
        'objects': plan['objects'], 'menu_children': plan['capacity'],
        'records_copied': plan['records'], 'records_dropped': plan['dropped'],
        'header_bytes': [plan['header_stock_size'], len(plan['header'])],
        'appended_bytes': len(plan['body']),
        'menu_declaration': hex(plan['menu_at']),
        'tail_record': hex(plan['tail_at']),
        'private_ids': [hex(v) for v in plan['identifiers'].values()],
        'payload_sha256': hashlib.sha256(plan['header'] + plan['body']).hexdigest(),
    }
    if args.command == 'verify':
        result = verify(image, plan)
        report['verification'] = result['checks']
        report['negative_control'] = result['overruns']
        for name, passed in result['checks'].items():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
        if not all(result['checks'].values()):
            raise SystemExit('extended scene did not verify')
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'header.bin').write_bytes(plan['header'])
    (args.out / 'records.bin').write_bytes(plan['body'])
    (args.out / 'menu.bin').write_bytes(plan['menu'])
    (args.out / 'plan.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"FP LAB row: {plan['objects'][0]} -> {plan['objects'][1]} objects, "
          f"{len(plan['body'])} bytes of records, header "
          f"{plan['header_stock_size']} -> {len(plan['header'])} bytes")
    print(f"artifacts in {args.out}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error) as error:
        raise SystemExit(str(error))
