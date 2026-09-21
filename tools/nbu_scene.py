#!/usr/bin/env python3
"""Read, extend and re-emit fp 5.02 NBU scene streams.

A scene is an allocation header (chunk 0x10002) followed by byte-packed
records. The header is what makes a scene growable: the native interpreter
sizes every array from it before a single object is built, so adding records
without adding their header contribution corrupts memory rather than failing.

Header layout, decoded here and checked against all 221 scenes in the image:

    tag 0x10002, size, object count, deferred-object count,
    component-kind count, then {pool offset, count} per kind,
    group count, then {a, b, clips} per animation-group record (0x1000B),
    clip count,  then track count per animation clip,
    track count, then key count per track,
    list count,  then item count per list component.

Proven relations (all 221 scenes, `python nbu_scene.py verify`):

  * object count            == number of 0x10003 records
  * per-kind counts         == records naming that component, except
    animationClip, whose clips are created by the group records
  * sum of group clip counts == animationClip count
  * clip array length       == animationClip count
  * sum of clip track counts == track array length
  * list array length       == list component count

Grafting copies a subtree of records out of a donor scene and appends both the
records and their exact header contribution. Records that consume the clip,
track, group or list arrays are refused, because their header entries are
positional and this module does not yet resolve which entry belongs to which
record. Refusing is deliberate: an over- or under-count there is silent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import argparse
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import menu_resources as res

HEADER_TAG = 0x10002
DECLARATION = 0x10003
GROUP = 0x1000B
CLIP = 0x1000A
LIST = 0x10008
# Records whose payload begins with a component-name pool offset.
NAMED = (0x10004, 0x10005, 0x10006, 0x10007, LIST, 0x10009, CLIP,
         0x1000C, 0x1000D, 0x1000E)
ARRAY_RECORDS = (GROUP, CLIP, LIST)


def _u32(raw: bytes, at: int) -> int:
    return struct.unpack_from('>I', raw, at)[0]


@dataclass
class Header:
    objects: int
    deferred: int
    kinds: list[tuple[int, int]]           # (pool offset, count), source order
    groups: list[tuple[int, int, int]]     # per animation-group record
    clip_tracks: list[int]                 # per animation clip
    track_keys: list[int]                  # per animation track
    list_items: list[int]                  # per list component

    def kind(self, offset: int) -> int:
        return next((count for key, count in self.kinds if key == offset), 0)


@dataclass
class Scene:
    name: str
    start: int                             # image offset of the header
    end: int
    header: Header
    records: list[tuple[int, int, int]] = field(default_factory=list)  # at,tag,size


def decode_header(raw: bytes) -> Header:
    if len(raw) < 20 or _u32(raw, 0) != HEADER_TAG:
        raise ValueError('not an NBU scene allocation header')
    size = _u32(raw, 4)
    if size != len(raw) or size % 4:
        raise ValueError('header size does not match its own chunk')
    words = struct.unpack('>%dI' % (size // 4), raw)
    at = 4
    kinds = []
    count = words[at]
    at += 1
    for _ in range(count):
        kinds.append((words[at], words[at + 1]))
        at += 2
    groups = []
    count = words[at]
    at += 1
    for _ in range(count):
        groups.append(tuple(words[at:at + 3]))
        at += 3
    scalars = []
    for _ in range(3):
        count = words[at]
        at += 1
        scalars.append(list(words[at:at + count]))
        at += count
    if at != len(words):
        raise ValueError('trailing data in allocation header')
    return Header(words[2], words[3], kinds, groups, *scalars)


def encode_header(header: Header) -> bytes:
    body = [header.objects, header.deferred, len(header.kinds)]
    for key, count in header.kinds:
        body += [key, count]
    body.append(len(header.groups))
    for triple in header.groups:
        body += list(triple)
    for scalars in (header.clip_tracks, header.track_keys, header.list_items):
        body.append(len(scalars))
        body += scalars
    for value in body:
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError('allocation header value out of range')
    size = 8 + len(body) * 4
    return struct.pack('>II', HEADER_TAG, size) + struct.pack('>%dI' % len(body), *body)


def scene(image: bytes, name: str) -> Scene:
    start, end = res.scene_bounds(image, name)
    size = _u32(image, start + 4)
    parsed = Scene(name, start, end, decode_header(image[start:start + size]))
    at = start + size
    while at < end:
        tag, chunk = struct.unpack_from('>II', image, at)
        if chunk < 8 or at + chunk > end:
            raise ValueError(f'invalid record in scene {name!r} at {at:#x}')
        parsed.records.append((at, tag, chunk))
        at += chunk
    return parsed


def component_of(image: bytes, at: int, tag: int) -> int | None:
    """Pool offset of the component a record instantiates, or None."""
    return _u32(image, at + 8) if tag in NAMED else None


def subtree(image: bytes, source: Scene, root: int) -> tuple[list[tuple[int, int, int]], dict[int, int]]:
    """Records of the object `root` and its descendants, in stream order.

    Object records own every following record until the next declaration, so a
    subtree is a contiguous span only when its objects are declared together.
    That is checked rather than assumed.
    """
    owners: dict[int, int] = {}
    spans: list[tuple[int, int, int, int]] = []       # object, at, tag, size
    current = None
    for at, tag, size in source.records:
        if tag == DECLARATION:
            obj, parent = struct.unpack_from('>2I', image, at + 20)
            owners[obj] = parent
            current = obj
        spans.append((current, at, tag, size))
    wanted = {root}
    for obj, parent in owners.items():
        walk, seen = parent, set()
        while walk in owners and walk not in seen:
            seen.add(walk)
            if walk == root:
                wanted.add(obj)
                break
            walk = owners[walk]
    picked = [(at, tag, size) for obj, at, tag, size in spans if obj in wanted]
    first = next(i for i, (obj, *_rest) in enumerate(spans) if obj in wanted)
    last = max(i for i, (obj, *_rest) in enumerate(spans) if obj in wanted)
    if last - first + 1 != len(picked):
        raise ValueError('subtree records are not contiguous in the stream')
    return picked, owners


def _array_slice(image: bytes, donor: Scene, picked: list[tuple[int, int, int]]
                 ) -> tuple[list[tuple[int, int, int]], list[int], list[int], list[int]]:
    """The donor's own header entries for the records being copied.

    Clips are created by the group records, `groups[i][2]` of them each, in
    stream order; tracks belong to clips the same way, and list items to list
    components. So each array is a contiguous run, located by counting the
    records of that kind that precede the copied span.
    """
    header = donor.header
    group_order = [at for at, tag, _s in donor.records if tag == GROUP]
    list_order = [at for at, tag, _s in donor.records if tag == LIST]
    group_positions = [group_order.index(at) for at, tag, _s in picked if tag == GROUP]
    list_positions = [list_order.index(at) for at, tag, _s in picked if tag == LIST]
    clip_records = sum(1 for _at, tag, _s in picked if tag == CLIP)
    if not group_positions and not clip_records and not list_positions:
        return [], [], [], []
    # The header reserves clips per animation GROUP (groups[i][2]); clip records
    # bind to their owner object by id (C05E6FAA), so records may be FEWER than
    # the reserved slots -- the stock scene itself reserves 214 for 190 records.
    # The reservation is only sliceable if the copied groups are a contiguous
    # run in group order, because clip_tracks is laid out in that order.
    def contiguous(positions: list[int], what: str) -> None:
        if positions and positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError(f'copied {what} are not contiguous; the slice would be wrong')
    contiguous(group_positions, 'animation groups')
    contiguous(list_positions, 'list records')
    picked_groups = [header.groups[i] for i in group_positions]
    wanted_clips = sum(g[2] for g in picked_groups)
    if clip_records > wanted_clips:
        raise ValueError('more clip records (%d) than reserved slots (%d)'
                         % (clip_records, wanted_clips))
    clip_index = sum(g[2] for g in header.groups[:group_positions[0]]) if group_positions else 0
    track_index = sum(header.clip_tracks[:clip_index])
    list_index = list_positions[0] if list_positions else 0
    tracks = header.clip_tracks[clip_index:clip_index + wanted_clips] if wanted_clips else []
    keys = header.track_keys[track_index:track_index + sum(tracks)] if tracks else []
    items = header.list_items[list_index:list_index + len(list_positions)] if list_positions else []
    if len(tracks) != wanted_clips or len(keys) != sum(tracks) or len(items) != len(list_positions):
        raise ValueError('donor header arrays are shorter than the copied span')
    return picked_groups, tracks, keys, items


def graft(image: bytes, target: Scene, donor: Scene, root: int,
          parent: int, identifiers: dict[int, int],
          rewrites: dict[int, bytes] | None = None) -> tuple[bytes, bytes]:
    """Return (new header, appended records) adding donor's `root` to `target`.

    `identifiers` maps every donor object id to a private id. `parent` is the
    target object the copied root attaches to; its declaration must already
    have room for another child, which `child_capacity` reports.
    """
    picked, owners = subtree(image, donor, root)
    rewrites = rewrites or {}
    groups, tracks, keys, items = _array_slice(image, donor, picked)
    missing = set()
    body = bytearray()
    objects = 0
    kinds: dict[int, int] = {}
    for at, tag, size in picked:
        raw = bytearray(rewrites.get(at, image[at:at + size]))
        if len(raw) != size:
            raise ValueError('a rewritten record changed length')
        if tag == DECLARATION:
            obj, owner = struct.unpack_from('>2I', raw, 20)
            if obj not in identifiers:
                missing.add(obj)
            struct.pack_into('>I', raw, 20, identifiers.get(obj, obj))
            struct.pack_into('>I', raw, 24,
                             parent if obj == root else identifiers.get(owner, owner))
            objects += 1
        else:
            # Records carry the object they belong to at +0x14, except the
            # event and animation-group forms, which name the event first.
            field = 24 if tag in (0x10006, GROUP) else 20
            owner = _u32(raw, field)
            if owner not in identifiers:
                missing.add(owner)
            struct.pack_into('>I', raw, field, identifiers.get(owner, owner))
            component = component_of(image, at, tag)
            # Clips are counted from the group records that create them; the
            # clip records themselves are not one component each.
            if component is not None and tag != CLIP:
                kinds[component] = kinds.get(component, 0) + 1
        body += raw
    if missing:
        raise ValueError('no private id for donor objects: '
                         + ', '.join(f'{obj:#x}' for obj in sorted(missing)))
    header = Header(target.header.objects + objects, target.header.deferred,
                    list(target.header.kinds), target.header.groups + groups,
                    target.header.clip_tracks + tracks,
                    target.header.track_keys + keys,
                    target.header.list_items + items)
    if groups:
        clip_key = next(key for key, _ in donor.header.kinds
                        if image[res.NBU_BASE + 20 + key:
                                 res.NBU_BASE + 20 + key + 14] == b'animationClip\x00')
        kinds[clip_key] = kinds.get(clip_key, 0) + sum(g[2] for g in groups)
    for component, count in kinds.items():
        for index, (key, have) in enumerate(header.kinds):
            if key == component:
                header.kinds[index] = (key, have + count)
                break
        else:
            header.kinds.append((component, count))
            header.kinds.sort(key=lambda entry: entry[0])
    return encode_header(header), bytes(body)


def child_capacity(image: bytes, at: int) -> int:
    """Declared child slots of the declaration record at image offset `at`."""
    if _u32(image, at) != DECLARATION:
        raise ValueError('not an object declaration')
    return _u32(image, at + 16)


def set_child_capacity(raw: bytes, slots: int) -> bytes:
    record = bytearray(raw)
    struct.pack_into('>I', record, 16, slots)
    return bytes(record)


def verify(image: bytes) -> dict:
    """Check every header relation this module relies on, across all scenes."""
    base, end = res.NBU_BASE, res.NBU_END
    table = base + 12 + _u32(image, base + 16)
    count = _u32(image, table + 12)
    names = []
    for index in range(count):
        offset = _u32(image, table + 20 + index * 32)
        at = base + 20 + offset
        names.append(image[at:image.index(b'\x00', at, table)].decode())
    clip_key = None
    list_key = None
    checked = 0
    for name in names:
        parsed = scene(image, name)
        header = parsed.header
        if clip_key is None:
            pool = base + 20
            for key, _ in header.kinds:
                text = image[pool + key:image.index(b'\x00', pool + key, table)]
                if text == b'animationClip':
                    clip_key = key
                elif text == b'list':
                    list_key = key
        counts: dict[int, int] = {}
        objects = 0
        groups = []
        for at, tag, size in parsed.records:
            if tag == DECLARATION:
                objects += 1
            if tag == GROUP:
                groups.append(struct.unpack_from('>3I', image, at + 8))
            component = component_of(image, at, tag)
            if component is not None:
                counts[component] = counts.get(component, 0) + 1
        assert objects == header.objects, name
        assert len(header.groups) == len(groups), name
        assert [g[:2] for g in header.groups] == [g[:2] for g in groups], name
        assert all(h[2] >= c[2] for h, c in zip(header.groups, groups)), name
        assert sum(h[2] for h in header.groups) == header.kind(clip_key), name
        assert len(header.clip_tracks) == header.kind(clip_key), name
        assert sum(header.clip_tracks) == len(header.track_keys), name
        assert len(header.list_items) == header.kind(list_key), name
        for key, value in counts.items():
            if key != clip_key:
                assert header.kind(key) == value, (name, key)
        assert encode_header(header) == image[parsed.start:parsed.start
                                              + _u32(image, parsed.start + 4)], name
        checked += 1
    return dict(scenes=checked, animation_clip_key=clip_key, list_key=list_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, default=res.IMAGE)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify')
    show = commands.add_parser('show')
    show.add_argument('scene')
    args = parser.parse_args()
    image = args.image.read_bytes()
    if args.command == 'verify':
        report = verify(image)
        print(f"header layout and every derived relation hold for "
              f"{report['scenes']} scenes; round-trip is byte-exact")
        return
    parsed = scene(image, args.scene)
    header = parsed.header
    print(f'{args.scene}: {parsed.start + res.LOAD:#x}..{parsed.end + res.LOAD:#x} '
          f'{len(parsed.records)} records')
    print(f'objects {header.objects} deferred {header.deferred} '
          f'kinds {len(header.kinds)} groups {len(header.groups)} '
          f'clips {len(header.clip_tracks)} tracks {len(header.track_keys)} '
          f'lists {len(header.list_items)}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error) as error:
        raise SystemExit(str(error))
