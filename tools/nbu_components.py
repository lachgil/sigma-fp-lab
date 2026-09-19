#!/usr/bin/env python3
"""Decode the serialized properties of NBU component records, from the firmware.

A component record is a presence bitmask over an ordered property table that
the firmware itself carries: each component's constructor registers
`(count, table)` with `0xC05D5E30`, and the table holds 12-byte entries
`{type, name pointer, _}`. The record then stores only the present properties,
back to back, in table order. So a field's offset depends on which earlier
properties are present -- which is why a copied record cannot be rewritten
without this table, and why the earlier row work could only move object ids it
had guessed the offsets of.

Type sizes are taken from the interpreter's own property reader at
`0xC05E7B78` (its jump table at `0xC05E7BC0` dispatches 15 types):

    0 int32   1 float   2 bool   3 colour   9 enum   10 keyset   11 string-ref
    12 ref    13 resource-ref   14 OBJECT ID   6 float2   7 float3
    5 float4  4 int4    8 int4x4

Type 14 is the one that matters for a graft: it names another object in the
same scene, so every type-14 field has to be renumbered with the copy.

`python nbu_components.py verify` decodes every component record of all 221
scenes and checks each one consumes exactly its own length. `refs` lists the
object references of one object's subtree.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import struct
import sys

import menu_resources as res
import nbu_scene as ns

ROOT = Path(__file__).resolve().parent.parent
# Property sizes, by the interpreter's own reader types.
SIZES = {0: 4, 1: 4, 2: 4, 3: 4, 4: 16, 5: 16, 6: 8, 7: 12, 8: 16,
         9: 4, 10: 4, 11: 5, 12: 4, 13: 4, 14: 4}
OBJECT_ID = 14
# Where each record form keeps its presence bitmask. The event and action forms
# name their event first, so their mask sits one pair of words further in.
MASK_AT = {0x10004: 28, 0x10005: 28, 0x1000E: 28,
           0x10006: 32, 0x10009: 32, 0x1000C: 32, 0x1000D: 32}
# Drawing records carry two more properties after the component's own table: a
# float4 and a bool, which the interpreter reads at 0xC05E7B60 like any other.
# Their firmware names are not established, so they are not invented here.
DRAW_EXTRA = ((5, '(rect)'), (2, '(flag)'))
# Record forms this module does not model. `fill`, `controlLayout`,
# `animationEvent` and `languageEvent` have no locatable property table;
# layout records (0x10007) carry a fixed geometry block before their mask.
# None of them appear in the Record Settings rows, and a record whose form is
# not modelled is reported rather than half-decoded.
UNMODELLED_TAGS = (0x10007,)
REGISTER = 0xC05D5E30       # component property-table registration


def _text(image: bytes, at: int) -> str:
    return image[at - res.LOAD:image.index(b'\x00', at - res.LOAD)].decode()


def _pool_end(image: bytes) -> int:
    return res.NBU_BASE + 12 + struct.unpack_from('>I', image, res.NBU_BASE + 16)[0]


def pool_text(image: bytes, offset: int) -> str:
    at = res.NBU_BASE + 20 + offset
    return image[at:image.index(b'\x00', at, _pool_end(image))].decode()


def scene_names(image: bytes) -> list[str]:
    table = res.NBU_BASE + 12 + struct.unpack_from('>II', image, res.NBU_BASE + 12)[1]
    count = struct.unpack_from('>4I', image, table)[3]
    return [pool_text(image, struct.unpack_from('>8I', image, table + 16 + i * 32)[1])
            for i in range(count)]


def schema(image: bytes, name: str, _cache: dict = {}) -> list[tuple[int, str]] | None:
    """The component's ordered property table, located in the firmware.

    The component's name string is returned by a two-instruction getter in its
    vtable; that vtable's first slot is the constructor, which registers the
    property table. Anything ambiguous returns None rather than a guess.
    """
    if name in _cache:
        return _cache[name]
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    md = _cache.setdefault('*md', Cs(CS_ARCH_ARM, CS_MODE_THUMB))
    found = image.find(name.encode() + b'\x00', 0x5C0000, 0x6A0000)
    _cache[name] = None
    if found < 0:
        return None
    target = res.LOAD + found
    vtables = []
    for at in range(target - 8192, target, 2):
        code = list(md.disasm(image[at - res.LOAD:at - res.LOAD + 8], at))
        if (len(code) < 2 or code[0].mnemonic not in ('addw', 'adr')
                or not code[0].op_str.startswith('r0, pc, #') or code[1].mnemonic != 'bx'):
            continue
        if ((at + 4) & ~3) + int(code[0].op_str.split('#')[1], 0) != target:
            continue
        reference = image.find(struct.pack('<I', at | 1), res.NBU_BASE)
        if reference >= 0:
            vtables.append(res.LOAD + reference - 12)     # slot 3 of the vtable
    if len(vtables) != 1:
        return None
    constructor = struct.unpack_from('<I', image, vtables[0] - res.LOAD)[0]
    registers: dict[str, int] = {}
    for code in md.disasm(image[(constructor & ~1) - res.LOAD:
                                (constructor & ~1) - res.LOAD + 400], constructor & ~1):
        operands = code.op_str.split(', ')
        if code.mnemonic in ('movs', 'movw', 'movt') and len(operands) == 2 \
                and operands[1].startswith('#'):
            value = int(operands[1][1:], 0)
            registers[operands[0]] = ((registers.get(operands[0], 0) & 0xFFFF)
                                      | (value << 16)) if code.mnemonic == 'movt' else value
        elif code.mnemonic in ('addw', 'adr') and len(operands) == 3 and operands[1] == 'pc':
            registers[operands[0]] = ((code.address + 4) & ~3) + int(operands[2][1:], 0)
        elif code.mnemonic == 'bl' and code.op_str == f'#{REGISTER:#x}':
            count, table = registers.get('r1'), registers.get('r2')
            if not count or not table:
                return None
            properties = []
            for index in range(count):
                kind, pointer, _ = struct.unpack_from('<3I', image,
                                                      table - res.LOAD + index * 12)
                if kind not in SIZES:
                    return None
                properties.append((kind, _text(image, pointer)))
            _cache[name] = properties
            return properties
    return None


def fields(image: bytes, at: int, tag: int, size: int,
           names: bytes | None = None) -> list[tuple[str, int, int]]:
    """(name, type, offset of the value) of every property present in a record.

    `names` is where component-name pool offsets are looked up, and defaults to
    `image`. Pass the firmware when decoding a record out of a standalone
    buffer, which is how an emitted graft is re-read before it is installed.

    Raises for a record whose form is not modelled, or whose properties do not
    consume exactly its length -- a silent partial decode would be worse than
    no decode, because the offsets after the first wrong one are all wrong.
    """
    if tag not in MASK_AT:
        raise ValueError(f'record form {tag:#x} at {at + res.LOAD:#x} is not modelled')
    firmware = names if names is not None else image
    name = pool_text(firmware, ns.component_of(image, at, tag))
    properties = schema(firmware, name)
    if properties is None:
        raise ValueError(f'no property table for component {name!r}')
    if tag == 0x10005:
        properties = list(properties) + list(DRAW_EXTRA)
    start = MASK_AT[tag]
    mask = struct.unpack_from('>I', image, at + start)[0]
    position = start + 4
    present = []
    for index, (kind, field) in enumerate(properties):
        if mask >> index & 1:
            present.append((field, kind, at + position))
            position += SIZES[kind]
    if position != size:
        raise ValueError(f'{name} at {at + res.LOAD:#x}: properties consume '
                         f'{position} bytes of {size}')
    return present


def references(image: bytes, scene: ns.Scene, root: int) -> list[dict]:
    """Every object-id property in `root`'s subtree, with where it is stored."""
    picked, _ = ns.subtree(image, scene, root)
    out = []
    for at, tag, size in picked:
        if tag not in MASK_AT:
            continue
        name = pool_text(image, ns.component_of(image, at, tag))
        for field, kind, position in fields(image, at, tag, size):
            if kind == OBJECT_ID:
                out.append(dict(record=hex(at + res.LOAD), component=name,
                                property=field, at=hex(position - at),
                                object=struct.unpack_from('>I', image, position)[0]))
    return out


def value(image: bytes, kind: int, position: int) -> str:
    """A property's value, rendered by its type rather than as raw hex."""
    if kind == 1:
        return f'{struct.unpack_from(">f", image, position)[0]:g}'
    if kind in (5, 6, 7):
        count = {6: 2, 7: 3, 5: 4}[kind]
        return '(' + ', '.join(f'{v:g}' for v in
                               struct.unpack_from(f'>{count}f', image, position)) + ')'
    raw = struct.unpack_from('>I', image, position)[0]
    if kind == 11:
        try:
            return repr(pool_text(image, raw))
        except ValueError:
            return hex(raw)
    return hex(raw)


def dump(image: bytes, scene: ns.Scene, root: int) -> list[str]:
    """Every record of `root` and its descendants, decoded property by property."""
    picked, _ = ns.subtree(image, scene, root)
    owner, out = None, []
    for at, tag, size in picked:
        if tag == ns.DECLARATION:
            obj, parent = struct.unpack_from('>2I', image, at + 20)
            owner = obj
            name = pool_text(image, struct.unpack_from('>I', image, at + 28)[0])
            out.append(f'{at + res.LOAD:#x}  object {obj} ({name}), parent {parent}')
            continue
        component = (pool_text(image, ns.component_of(image, at, tag))
                     if tag in ns.NAMED else f'{tag:#x}')
        if tag not in MASK_AT:
            out.append(f'{at + res.LOAD:#x}    {component}  [not decoded]')
            continue
        out.append(f'{at + res.LOAD:#x}    {component}')
        for field, kind, position in fields(image, at, tag, size):
            out.append(f'{" ":12}      {field} = {value(image, kind, position)}')
    return out


def verify(image: bytes) -> dict:
    decoded = 0
    unmodelled: collections.Counter = collections.Counter()
    failures = []
    for name in scene_names(image):
        for at, tag, size in ns.scene(image, name).records:
            if tag in UNMODELLED_TAGS:
                unmodelled[f'{tag:#x}'] += 1
                continue
            if tag not in MASK_AT:
                continue
            try:
                fields(image, at, tag, size)
            except ValueError as error:
                component = pool_text(image, ns.component_of(image, at, tag))
                if 'no property table' in str(error):
                    unmodelled[component] += 1
                else:
                    failures.append(str(error))
            else:
                decoded += 1
    return dict(decoded=decoded, unmodelled=dict(unmodelled), failures=failures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=res.IMAGE)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify')
    table = commands.add_parser('schema')
    table.add_argument('component')
    refs = commands.add_parser('refs')
    refs.add_argument('scene')
    refs.add_argument('object', type=lambda s: int(s, 0))
    show = commands.add_parser('dump')
    show.add_argument('scene')
    show.add_argument('object', type=lambda s: int(s, 0))
    args = parser.parse_args()
    image = args.image.read_bytes()
    if args.command == 'schema':
        properties = schema(image, args.component)
        if properties is None:
            raise SystemExit(f'no property table found for {args.component!r}')
        for index, (kind, name) in enumerate(properties):
            print(f'{index:2}  type {kind:2}  {SIZES[kind]:2}B  {name}')
        return
    if args.command == 'dump':
        for line in dump(image, ns.scene(image, args.scene), args.object):
            print(line)
        return
    if args.command == 'refs':
        found = references(image, ns.scene(image, args.scene), args.object)
        print(json.dumps(found, indent=2))
        print(f'{len(found)} object references', file=sys.stderr)
        return
    report = verify(image)
    for name, count in sorted(report['unmodelled'].items()):
        print(f'not modelled: {name} ({count} records)')
    for failure in report['failures']:
        print(f'FAIL  {failure}')
    print(f"{report['decoded']} component records decode to exactly their own length")
    if report['failures']:
        raise SystemExit('property decoding failed')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error) as error:
        raise SystemExit(str(error))
