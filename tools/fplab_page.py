#!/usr/bin/env python3
"""Inspect Record Settings and exercise native object construction, offline.

B2_5 is Record Settings. The former B1_2_5 graft targeted ISO/shutter limits,
removed navigation/animation records, and never proved a working native menu.
That installable candidate has been withdrawn.

This experiment emits DECLARATIONS ONLY. It proves native object IDs, parent
attachment and table sizing, not components, rendering, focus or actions. Its
output must not be installed in a camera.
"""
from __future__ import annotations

import argparse
import collections
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct

import menu_resources as res
import nbu_components as nc
import nbu_scene as ns

ROOT = Path(__file__).resolve().parent.parent
SCENE = 'B2_5'
MENU = 89
DONOR_ROW = 4433
PRIVATE_ID = 0xF000
OUT = ROOT / 'builds/fplab-page'
EXPECTED_ROWS = (
    ('B2_5_1', '0301', 'Format'),
    ('B2_5_2', '0304', 'Bit Depth'),
    ('B2_5_3', '0309', 'Compression'),
    ('B2_5_4', '0312', 'Resolution'),
    ('B2_5_5', '0315', 'Frame Rate'),
)
# The sixth row. The five stock rows are 81 tall inside a `Menu` 486 high, so
# six slots fit exactly; rows 4 and 5 sit at y=162 and y=243, which is where the
# pitch comes from. Rows 2 and 3 both read 81, so the position property is not
# the whole story -- the menu also carries a layout item index per row -- and
# 324 is the prediction a camera has to confirm.
ROW_SLOT = 324.0
# The label. `src/strhook.S` answers this offset out of its own blob, so the
# row is named without touching a stock string or a localization key.
PRIVATE_BASE = 0x00100000
LABEL_OFFSET = PRIVATE_BASE          # "FP LAB", the first string in the blob
# This candidate still omits the donor's animation records. Header budget
# ordering is not a demonstrated reason to remove them; see the allocation
# experiment in docs/menu/gui-resources.md. Drawing and focus remain unverified.
DROP_COMPONENTS = ('controlAnimation',)
# The native interpreter consumes this record and returns 1 to end the loop.
TERMINATOR = 0xFFFFFFFF


def inspect_scene(image: bytes) -> dict:
    """Identify the target by localized row bindings, not scene-name guesses."""
    scene = ns.scene(image, SCENE)
    nodes = res.scene_nodes(image, SCENE)
    english = res.localized_text(image, next(
        e for e in res.resources(image) if e.name == '../Common/Strings/English.nloc'))
    menu = next((node for node in nodes if node[1:4] == (MENU, 1, 'Menu')), None)
    if menu is None:
        raise ValueError('Record Settings Menu container does not match')
    rows = []
    for address, obj, parent, name, key in nodes:
        if parent != MENU or not name.startswith(SCENE + '_'):
            continue
        records, _ = ns.subtree(image, scene, obj)
        addresses = {res.LOAD + at for at, tag, size in records if tag == ns.DECLARATION}
        labels = [(key, english[key][1]) for at, _, _, child_name, key in nodes
                  if at in addresses and child_name == 'Name' and key in english]
        if len(labels) != 1:
            raise ValueError(f'{name}: expected one localized Name binding, got {labels}')
        key, label = labels[0]
        rows.append(dict(name=name, object=obj, address=hex(address), key=key, label=label))
    actual = tuple((row['name'], row['key'], row['label']) for row in rows)
    if actual != EXPECTED_ROWS:
        raise ValueError(f'Record Settings label contract changed: {actual!r}')
    if rows[-1]['object'] != DONOR_ROW:
        raise ValueError('Frame Rate donor identity changed')
    return dict(scene=SCENE, scene_at=hex(scene.start + res.LOAD),
                objects=scene.header.objects, menu_declaration=hex(menu[0]),
                menu_children=ns.child_capacity(image, menu[0] - res.LOAD),
                rows=rows, installable=False,
                scope='Offline declarations only; no native FP LAB UI or actions')


def reference_audit(image: bytes) -> dict:
    """What a component-carrying graft of this row would have to renumber.

    Decoded with tools/nbu_components.py, so these are real field offsets from
    the firmware's own property tables, not guessed ones. References that leave
    the row are the interesting half: each one names a stock object the copy
    would still have to reach, or would have to be repointed at a private one.
    """
    scene = ns.scene(image, SCENE)
    picked, _ = ns.subtree(image, scene, DONOR_ROW)
    inside = {struct.unpack_from('>I', image, at + 20)[0]
              for at, tag, size in picked if tag == ns.DECLARATION}
    internal, external = [], []
    for entry in nc.references(image, scene, DONOR_ROW):
        (internal if entry['object'] in inside else external).append(entry)
    families = collections.Counter(entry['component'] for entry in internal + external)
    return dict(object_references=len(internal) + len(external),
                inside_the_row=len(internal), leaving_the_row=len(external),
                by_component=dict(sorted(families.items())),
                external_targets=sorted({entry['object'] for entry in external}),
                undecoded_records=sorted({
                    nc.pool_text(image, ns.component_of(image, at, tag))
                    for at, tag, size in picked
                    if tag not in nc.MASK_AT and tag in ns.NAMED}))


def label_record(image: bytes, scene: ns.Scene) -> tuple[int, int]:
    """The row's own label field: (record offset, image offset of its text).

    Found through the `Name` object under the donor row, not by position, so a
    different donor or a different firmware fails loudly instead of renaming
    whatever happens to sit there.
    """
    picked, _ = ns.subtree(image, scene, DONOR_ROW)
    addresses = {at for at, tag, size in picked if tag == ns.DECLARATION}
    names = [struct.unpack_from('>I', image, at + 20)[0] for at in addresses
             if nc.pool_text(image, struct.unpack_from('>I', image, at + 28)[0]) == 'Name']
    if len(names) != 1:
        raise ValueError(f'expected one Name object under the row, found {names}')
    found = []
    for at, tag, size in picked:
        if tag != 0x10005 or struct.unpack_from('>I', image, at + 20)[0] != names[0]:
            continue
        if nc.pool_text(image, ns.component_of(image, at, tag)) != 'drawText':
            continue
        for field, kind, position in nc.fields(image, at, tag, size):
            if field == 'text':
                found.append((at, position))
    if len(found) != 1:
        raise ValueError(f'expected one label field on the row, found {len(found)}')
    return found[0]


def build_row(image: bytes) -> dict:
    """Build the sixth row, animations intact.

    Three kinds of rewrite happen here, and only the first was ever safe to do
    by hand:

      * the declaration's own object and parent ids (fixed fields);
      * the owner id every component record carries (fixed field, `ns.graft`);
      * every OBJECT ID *property*, whose offset depends on which earlier
        properties that record happens to carry. Those come from
        `nbu_components`, which reads the firmware's own property tables. This
        is the part that made the earlier attempt drop the row's navigation
        instead of renumbering it.

    References that leave the row (the scene root, and `Footer`) are left
    pointing at the stock objects on purpose: they are the page's, not ours.
    """
    identity = inspect_scene(image)
    scene = ns.scene(image, SCENE)
    picked, _ = ns.subtree(image, scene, DONOR_ROW)
    # Keep every record. Animation groups and clips are carried with their exact
    # header contribution by `ns.graft`; clips bind to their owner object by id
    # (firmware C05E6FAA), so nothing here is positional. See
    # docs/menu/gui-resources.md.
    kept, dropped = list(picked), collections.Counter()
    inside = [struct.unpack_from('>I', image, at + 20)[0]
              for at, tag, size in kept if tag == ns.DECLARATION]
    identifiers = {obj: PRIVATE_ID + index for index, obj in enumerate(inside)}
    stock_ids = {struct.unpack_from('>I', image, at + 20)[0]
                 for at, tag, size in scene.records if tag == ns.DECLARATION}
    if stock_ids & set(identifiers.values()):
        raise ValueError('private object IDs collide with stock IDs')

    label_at, label_field = label_record(image, scene)
    rewrites: dict[int, bytearray] = {}
    remapped, external = 0, collections.Counter()

    def record(at: int, size: int) -> bytearray:
        return rewrites.setdefault(at, bytearray(image[at:at + size]))

    for at, tag, size in kept:
        if tag not in nc.MASK_AT:
            continue
        for field, kind, position in nc.fields(image, at, tag, size):
            if kind != nc.OBJECT_ID:
                continue
            target = struct.unpack_from('>I', image, position)[0]
            if target in identifiers:
                struct.pack_into('>I', record(at, size), position - at,
                                 identifiers[target])
                remapped += 1
            else:
                external[target] += 1
        if at == label_at:
            struct.pack_into('>I', record(at, size), label_field - at, LABEL_OFFSET)
        if tag == 0x10004 and struct.unpack_from('>I', image, at + 20)[0] == DONOR_ROW:
            place = {field: position
                     for field, _kind, position in nc.fields(image, at, tag, size)}
            if 'position' not in place:
                raise ValueError('the donor row carries no position property')
            struct.pack_into('>2f', record(at, size), place['position'] - at,
                             0.0, ROW_SLOT)

    # The donor is the whole scene: `graft` recomputes the subtree, and
    # `_array_slice` needs the full group order to index the header's reservations.
    header, body = ns.graft(image, scene, scene, DONOR_ROW, MENU, identifiers,
                            {at: bytes(raw) for at, raw in rewrites.items()})
    menu_at = int(identity['menu_declaration'], 16) - res.LOAD
    stock_menu = image[menu_at:menu_at + 36]
    capacity = identity['menu_children']
    # Preserve stock declaration/component order by appending at the terminator.
    # C05E8348 calls the interpreter until its status is nonzero. The old
    # injector overwrote success with STATE, stopping this loop at the header;
    # zero injected records did not establish an undispatched terminator.
    tail_at, tail_tag, tail_size = scene.records[-1]
    if tail_tag != TERMINATOR:
        raise ValueError('the scene does not end with a terminator')
    # A second candidate anchor: the record immediately after the last row's
    # subtree (the Footer block). Injecting here constructs our row within the
    # row block, BEFORE the scene's focus/footer controllers -- the fix for the
    # camera focus-enrollment bug, where a row built at the terminator (after
    # those controllers) draws but is never enrolled as focusable. Safe for
    # accounting: objects store by sorted id and clips bind by owner id, neither
    # depends on stream position.
    last_row_end = max(at for at, _t, _s in ns.subtree(image, scene, DONOR_ROW)[0])
    after = next((r for r in scene.records if r[0] > last_row_end), None)
    if after is None or after[1] == TERMINATOR:
        raise ValueError('no record follows the last row before the terminator')
    after_at, _after_tag, after_size = after
    grown = ns.decode_header(header)
    return dict(identity=identity, scene=SCENE, scene_at=scene.start + res.LOAD,
                header_at=scene.start + res.LOAD,
                header_stock_size=struct.unpack_from('>I', image, scene.start + 4)[0],
                header=header, menu_at=menu_at + res.LOAD, menu_stock=stock_menu,
                menu=ns.set_child_capacity(stock_menu, capacity + 1),
                tail_at=tail_at + res.LOAD, tail_size=tail_size, body=body,
                identifiers=identifiers, records=len(kept),
                dropped=dict(dropped), references_remapped=remapped,
                references_left_stock=dict(external),
                label=dict(record=hex(label_at + res.LOAD),
                           offset=hex(LABEL_OFFSET), text='FP LAB'),
                after_rows_at=after_at + res.LOAD, after_rows_size=after_size,
                capacity=(capacity, capacity + 1), installable=False)



def build(image: bytes) -> dict:
    """Build a declaration-only experiment, never an installable scene graft."""
    identity = inspect_scene(image)
    scene = ns.scene(image, SCENE)
    picked, _ = ns.subtree(image, scene, DONOR_ROW)
    declarations = [(at, size) for at, tag, size in picked if tag == ns.DECLARATION]
    identifiers = {struct.unpack_from('>I', image, at + 20)[0]: PRIVATE_ID + index
                   for index, (at, size) in enumerate(declarations)}
    stock_ids = {struct.unpack_from('>I', image, at + 20)[0]
                 for at, tag, size in scene.records if tag == ns.DECLARATION}
    if stock_ids & set(identifiers.values()):
        raise ValueError('private object IDs collide with stock IDs')
    body = bytearray()
    for at, size in declarations:
        record = bytearray(image[at:at + size])
        obj, parent = struct.unpack_from('>2I', record, 20)
        struct.pack_into('>2I', record, 20, identifiers[obj],
                         MENU if obj == DONOR_ROW else identifiers[parent])
        body.extend(record)
    header = ns.encode_header(replace(scene.header,
                                     objects=scene.header.objects + len(declarations)))
    menu_at = int(identity['menu_declaration'], 16) - res.LOAD
    stock_menu = image[menu_at:menu_at + 36]
    capacity = identity['menu_children']
    tail_at, _, tail_size = scene.records[-1]
    return dict(identity=identity, scene=SCENE, scene_at=scene.start + res.LOAD,
                header_at=scene.start + res.LOAD,
                header_stock_size=struct.unpack_from('>I', image, scene.start + 4)[0],
                header=header, menu_at=menu_at + res.LOAD, menu_stock=stock_menu,
                menu=ns.set_child_capacity(stock_menu, capacity + 1),
                tail_at=tail_at + res.LOAD, tail_size=tail_size, body=bytes(body),
                identifiers=identifiers, records=len(declarations),
                objects=(scene.header.objects, scene.header.objects + len(declarations)),
                capacity=(capacity, capacity + 1), installable=False)

def verify_row(image: bytes, plan: dict) -> dict:
    """Re-read the emitted row the way the firmware will, and run its objects.

    Two independent passes. First the records are decoded again from the bytes
    that will actually be injected -- not from the plan that produced them --
    and every reference is resolved against the scene the row lands in. Then the
    declarations go through the firmware's own interpreter.
    """
    from native_scene_vm import SceneVM

    scene = ns.scene(image, SCENE)
    body = plan['body']
    stock_ids = {struct.unpack_from('>I', image, at + 20)[0]
                 for at, tag, size in scene.records if tag == ns.DECLARATION}
    private = set(plan['identifiers'].values())

    declared, owners, refs, lengths = set(), [], [], True
    components = collections.Counter()
    at = 0
    while at < len(body):
        tag, size = struct.unpack_from('>II', body, at)
        if size < 8 or at + size > len(body):
            raise ValueError(f'emitted record at {at} is malformed')
        if tag == ns.DECLARATION:
            declared.add(struct.unpack_from('>I', body, at + 20)[0])
        at += size
    at = 0
    labels = []
    while at < len(body):
        tag, size = struct.unpack_from('>II', body, at)
        if tag in nc.MASK_AT:
            component = nc.pool_text(image, struct.unpack_from('>I', body, at + 8)[0])
            components[component] += 1
            try:
                fields = nc.fields(body, at, tag, size, names=image)
            except ValueError:
                lengths = False
            else:
                for field, kind, position in fields:
                    if kind == nc.OBJECT_ID:
                        refs.append(struct.unpack_from('>I', body, position)[0])
                    elif (component == 'drawText' and field == 'text'
                          and struct.unpack_from('>I', body, position)[0] >= PRIVATE_BASE):
                        labels.append(struct.unpack_from('>I', body, position)[0])
        if tag != ns.DECLARATION and tag in ns.NAMED:
            owners.append(struct.unpack_from('>I', body,
                                             at + (24 if tag in (0x10006, ns.GROUP) else 20))[0])
        at += size

    external = set(plan['references_left_stock'])
    grown = ns.decode_header(plan['header'])
    # Every component kind the grown header reserves must match the record census
    # of the grafted scene (stock records + the row body), so the firmware sizes
    # the arena for exactly what arrives. animationClip is reserved per group, so
    # its count is the stock reservation plus the row's 30, not a record count.
    clip_key = next(key for key, _ in scene.header.kinds
                    if nc.pool_text(image, key) == 'animationClip')
    census = collections.Counter(
        struct.unpack_from('>I', image, at + 8)[0]
        for at, tag, _s in scene.records if tag in ns.NAMED and tag != ns.CLIP)
    pos = 0
    while pos < len(body):
        tag, size = struct.unpack_from('>II', body, pos)
        if tag in ns.NAMED and tag != ns.CLIP:
            census[struct.unpack_from('>I', body, pos + 8)[0]] += 1
        pos += size
    census[clip_key] = dict(scene.header.kinds)[clip_key] + 30
    checks = {
        'every emitted record decodes to its own length': lengths,
        'the row declares exactly the private ids': declared == private,
        'no emitted record still owns a stock object':
            all(owner in private for owner in owners),
        'every reference resolves to a declared object':
            all(ref in private or ref in stock_ids for ref in refs),
        'references that leave the row are only the page\'s own':
            external <= {1, 37} and all(ref in stock_ids for ref in external),
        'no reference is left stale (all resolve to a declared object)':
            not (set(refs) - private - stock_ids),
        'exactly one label, and it is our private offset':
            labels == [LABEL_OFFSET],
        'the header grew by exactly the objects added':
            grown.objects == scene.header.objects + len(private),
        'the header still round-trips': ns.encode_header(grown) == plan['header'],
        'the list component brought its header entry':
            len(grown.list_items) == len(scene.header.list_items) + 1,
        'the animation groups and their clips came across':
            grown.groups[:len(scene.header.groups)] == scene.header.groups
            and len(grown.groups) == len(scene.header.groups) + 6
            and len(grown.clip_tracks) == len(scene.header.clip_tracks) + 30
            and len(grown.track_keys) == len(scene.header.track_keys) + 95,
        'the header reserves exactly the grafted component census':
            dict(grown.kinds) == dict(census),
    }

    vm = SceneVM(image)
    vm.record(plan['header'])
    menu_at = plan['menu_at'] - res.LOAD
    for at, tag, size in scene.records:
        if tag == ns.DECLARATION:
            vm.record(plan['menu'] if at == menu_at else image[at:at + size])
    at = 0
    while at < len(body):
        tag, size = struct.unpack_from('>II', body, at)
        if tag == ns.DECLARATION:
            vm.record(body[at:at + size])
        at += size
    checks['the interpreter builds every object'] = vm.created() == grown.objects
    checks['the interpreter resolves every private id'] = all(
        vm.lookup(obj) for obj in private)
    checks['Menu owns the new row'] = vm.parent(PRIVATE_ID) == MENU
    checks['Menu now has six rows plus Blind'] = (
        vm.children(MENU) == plan['capacity'][1])
    return dict(checks=checks, components=dict(sorted(components.items())),
                scope='Records decoded and objects built offline. Component '
                      'instantiation, drawing, focus and key handling are not '
                      'exercised: the interpreter\'s component registry is '
                      'modelled as zero-sized here. Needs a camera.')



def verify(image: bytes, plan: dict) -> dict:
    """Execute native declarations and an undersized-table negative control."""
    from native_scene_vm import SceneVM

    scene = ns.scene(image, SCENE)
    stock_header = image[scene.start:scene.start + plan['header_stock_size']]
    menu_at = plan['menu_at'] - res.LOAD

    def run(header: bytes, menu: bytes, extra: bytes):
        vm = SceneVM(image)
        vm.record(header)
        for at, tag, size in scene.records:
            if tag == ns.DECLARATION:
                vm.record(menu if at == menu_at else image[at:at + size])
        for offset in range(0, len(extra), 36):
            vm.record(extra[offset:offset + 36])
        return vm

    stock = run(stock_header, plan['menu_stock'], b'')
    grown = run(plan['header'], plan['menu'], plan['body'])
    original = {struct.unpack_from('>I', image, at + 20)[0]
                for at, tag, size in scene.records if tag == ns.DECLARATION}
    checks = {
        'stock objects': stock.created() == scene.header.objects,
        'extended objects': grown.created() == plan['objects'][1],
        'stock Menu children': stock.children(MENU) == plan['capacity'][0],
        'extended Menu children': grown.children(MENU) == plan['capacity'][1],
        'every stock ID still resolves': all(grown.lookup(obj) for obj in original),
        'every new ID resolves': all(grown.lookup(obj) for obj in plan['identifiers'].values()),
        'new declaration root belongs to Menu': grown.parent(PRIVATE_ID) == MENU,
    }
    control = run(stock_header, plan['menu'], b'')
    overruns = control.watch_table(plan['body'])
    checks['unchanged header overruns the object table'] = bool(overruns)
    return dict(checks=checks, overruns=overruns,
                scope='Native declarations only; components and GUI behavior are not exercised')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=res.IMAGE)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('command', choices=('inspect', 'build', 'verify', 'row'))
    args = parser.parse_args()
    image = args.image.read_bytes()
    if args.command == 'inspect':
        print(json.dumps(dict(inspect_scene(image),
                              reference_audit=reference_audit(image)), indent=2))
        return
    if args.command == 'row':
        plan = build_row(image)
        report = dict(plan['identity'], objects=plan['objects'],
                      menu_children=plan['capacity'],
                      records_copied=plan['records'], dropped=plan['dropped'],
                      references_remapped=plan['references_remapped'],
                      references_left_stock=plan['references_left_stock'],
                      label=plan['label'], row_slot=ROW_SLOT,
                      header_bytes=[plan['header_stock_size'], len(plan['header'])],
                      appended_bytes=len(plan['body']),
                      tail_record=hex(plan['tail_at']),
                      private_ids=[hex(v) for v in plan['identifiers'].values()],
                      firmware_sha256=hashlib.sha256(image).hexdigest())
        result = verify_row(image, plan)
        report['verification'] = result
        for name, passed in result['checks'].items():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
        if not all(result['checks'].values()):
            raise SystemExit('the FP LAB row did not verify')
        args.out.mkdir(parents=True, exist_ok=True)
        for name, key in (('row-header.bin', 'header'), ('row-records.bin', 'body'),
                          ('row-menu.bin', 'menu')):
            (args.out / name).write_bytes(plan[key])
        (args.out / 'row.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f"FP LAB row: {plan['records']} records, {len(plan['body'])} bytes, "
              f"{plan['objects'][0]} -> {plan['objects'][1]} objects, "
              f"{plan['references_remapped']} references remapped, "
              f"dropped {plan['dropped']}")
        print(f"artifacts in {args.out}")
        return
    plan = build(image)
    report = dict(plan['identity'], objects=plan['objects'],
                  menu_children=plan['capacity'], records_copied=plan['records'],
                  header_bytes=[plan['header_stock_size'], len(plan['header'])],
                  appended_bytes=len(plan['body']), tail_record=hex(plan['tail_at']),
                  private_ids=[hex(v) for v in plan['identifiers'].values()],
                  reference_audit=reference_audit(image),
                  firmware_sha256=hashlib.sha256(image).hexdigest())
    if args.command == 'verify':
        result = verify(image, plan)
        report['verification'] = result
        for name, passed in result['checks'].items():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
        if not all(result['checks'].values()):
            raise SystemExit('native declaration experiment failed')
    args.out.mkdir(parents=True, exist_ok=True)
    for name, key in (('header.bin', 'header'), ('records.bin', 'body'), ('menu.bin', 'menu')):
        (args.out / name).write_bytes(plan[key])
    (args.out / 'plan.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"DECLARATIONS ONLY, NOT INSTALLABLE: {plan['objects'][0]} -> "
          f"{plan['objects'][1]} objects; artifacts in {args.out}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error) as error:
        raise SystemExit(str(error))
