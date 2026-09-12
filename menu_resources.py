#!/usr/bin/env python3
"""Inspect the embedded NBR resources in the extracted SIGMA fp 5.02 MAIN image.

Offline and read-only. Examples:
  python menu_resources.py list MainMenu
  python menu_resources.py show ../MainMenu/data/ListMenu/B1_4.cvm

  python menu_resources.py text 0711
NBR chunk 1 is the name pool. Chunk 2 contains a sorted FNV-1a directory
and resource payloads. Offsets here are image offsets, not file paths to open.
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
import struct
import sys

LOAD = 0xC0000000
BASE = 0xD22400
NBU_BASE = 0x18C0460
NBU_END = 0x2D8CEF8
IMAGE = Path(__file__).resolve().parent / 'analysis' / 'MAIN_c0000000.bin'


@dataclass(frozen=True)
class Resource:
    name: str
    key: int
    offset: int
    size: int


def fnv1a(raw: bytes) -> int:
    value = 2166136261
    for byte in raw:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


def resources(image: bytes) -> list[Resource]:
    if image[BASE:BASE + 4] != b'NBR\x00':
        raise ValueError('expected SIGMA fp 5.02 NBR resource at 0xC0D22400')
    names_chunk = BASE + 12
    kind, names_size = struct.unpack_from('>II', image, names_chunk)
    directory = names_chunk + names_size
    names_start = names_chunk + 8
    if kind != 1 or names_size < 8 or directory > len(image) - 12:
        raise ValueError('invalid name pool chunk')
    kind, chunk_size, count = struct.unpack_from('>III', image, directory)
    end = directory + chunk_size
    entries_start = directory + 12
    data_start = entries_start + count * 16
    if kind != 2 or not data_start <= end <= len(image):
        raise ValueError('invalid resource directory chunk')
    entries = []
    previous_key = -1
    for index in range(count):
        key, name_offset, data_offset, size = struct.unpack_from(
            '>IIII', image, entries_start + index * 16)
        name_start = names_start + name_offset
        if not names_start <= name_start < directory:
            raise ValueError(f'name outside pool in entry {index}')
        name_end = image.index(b'\x00', name_start, directory)
        raw_name = image[name_start:name_end]
        offset = BASE + data_offset
        if key < previous_key or fnv1a(raw_name) != key:
            raise ValueError(f'invalid path hash/order in entry {index}')
        if not data_start <= offset <= end - size:
            raise ValueError(f'payload outside chunk in entry {index}')
        entries.append(Resource(raw_name.decode('utf-8'), key, offset, size))
        previous_key = key
    if len({entry.name for entry in entries}) != count:
        raise ValueError('duplicate resource names')
    return entries


def localized_text(image: bytes, entry: Resource) -> dict[str, tuple[int, str]]:
    """Return localization keys mapped to (image byte offset, UTF-8 text)."""
    data = image[entry.offset:entry.offset + entry.size]
    if data[:8] != b'NDB\x00\x01\x00\x00\x00':
        raise ValueError('unsupported NDB localization header')
    count = struct.unpack_from('<I', data, 8)[0]
    table_end = 12 + count * 8
    if table_end > len(data):
        raise ValueError('localization table outside resource')
    result = {}
    previous_hash = -1
    for index in range(count):
        key_hash, packed = struct.unpack_from('<II', data, 12 + index * 8)
        record = packed >> 8
        if packed & 255 or not table_end <= record <= len(data) - 8:
            raise ValueError(f'invalid localization record {index}')
        key_offset, value_offset = struct.unpack_from('<II', data, record)
        key_start, value_start = record + key_offset, record + value_offset
        if not record + 8 <= key_start < value_start < len(data):
            raise ValueError(f'invalid localization strings in record {index}')
        key_end = data.index(b'\x00', key_start, value_start)
        value_end = data.index(b'\x00', value_start)
        raw_key = data[key_start:key_end]
        key = raw_key.decode('utf-8')
        if key_hash < previous_hash or fnv1a(raw_key) != key_hash or key in result:
            raise ValueError(f'invalid localization key in record {index}')
        result[key] = (entry.offset + value_start,
                       data[value_start:value_end].decode('utf-8'))
        previous_hash = key_hash
    return result

def scene_bounds(image: bytes, name: str) -> tuple[int, int]:
    """Half-open image-offset span of one scene's chunk stream."""
    base, end = NBU_BASE, NBU_END
    if image[base:base + 4] != b'NBU\x00' or end > len(image):
        raise ValueError('expected fp 5.02 NBU image')
    kind, span = struct.unpack_from('>II', image, base + 12)
    table = base + 12 + span
    if kind != 1 or not base + 20 <= table <= end - 16:
        raise ValueError('invalid NBU name chunk')
    kind, span, _, count = struct.unpack_from('>IIII', image, table)
    if kind != 0x10001 or span != 16 + count * 32 or table + span > end:
        raise ValueError('invalid NBU scene directory')
    starts, wanted = [], None
    for index in range(count):
        key, offset, _, data, *_ = struct.unpack_from('>8I', image, table + 16 + index * 32)
        at = base + 20 + offset
        scene_name = image[at:image.index(b'\x00', at, table)].decode('utf-8')
        start = base + data
        if fnv1a(scene_name.encode()) != key:
            raise ValueError('invalid NBU scene name/hash')
        if not table + span <= start <= end - 8 or struct.unpack_from('>I', image, start)[0] != 0x10002:
            raise ValueError('invalid NBU scene payload')
        starts.append(start)
        if scene_name == name:
            wanted = start
    if wanted is None:
        raise ValueError(f'unknown scene {name!r}')
    return wanted, min((s for s in starts if s > wanted), default=end)



def scene_nodes(image: bytes, name: str) -> list[tuple[int, int, int, str, str | None]]:
    """Inspect NBU object declarations and direct localized drawText bindings.

    Result: (declaration address, object id, parent id, name, text key).
    Other property types remain undecoded; this is not a scene rewriter.
    """
    base, end = NBU_BASE, NBU_END
    pool = base + 20
    table = base + 12 + struct.unpack_from('>II', image, base + 12)[1]

    def string(offset: int) -> str:
        at = pool + offset
        if not pool <= at < table:
            raise ValueError('NBU string outside pool')
        return image[at:image.index(b'\x00', at, table)].decode('utf-8')

    start, stop = scene_bounds(image, name)
    declarations, texts = [], {}
    pos = start
    while pos < stop:
        kind, size = struct.unpack_from('>II', image, pos)
        if size < 8 or pos + size > stop:
            raise ValueError('invalid NBU scene chunk')
        # Chunks are packed, NOT word-aligned. Advance by their exact sizes.
        if kind == 0x10003:
            if size != 36:
                raise ValueError('unexpected object declaration')
            _, _, _, obj, parent, offset, _ = struct.unpack_from('>7I', image, pos + 8)
            declarations.append((LOAD + pos, obj, parent, string(offset)))
        elif kind == 0x10005 and size >= 36:
            method = string(struct.unpack_from('>I', image, pos + 8)[0])
            value_type = struct.unpack_from('>I', image, pos + 28)[0]
            if method == 'drawText' and value_type == 0x40000011:
                obj = struct.unpack_from('>I', image, pos + 20)[0]
                offset = struct.unpack_from('>I', image, pos + 32)[0]
                # Some dynamic fields (e.g. Y5_CreateQR) have no literal key.
                if offset != 0xFFFFFFFF:
                    texts[obj] = string(offset)
        pos += size
    return [(*node, texts.get(node[1])) for node in declarations]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, default=IMAGE)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list').add_argument('pattern', nargs='?', default='')
    commands.add_parser('show').add_argument('name')
    text = commands.add_parser('text')
    text.add_argument('key')
    text.add_argument('--locale', default='English')
    scene = commands.add_parser('scene')
    scene.add_argument('name')
    scene.add_argument('--filter', default='')
    args = parser.parse_args()
    image = args.image.read_bytes()
    entries = resources(image)
    if args.command == 'list':
        for entry in entries:
            if args.pattern.lower() in entry.name.lower():
                print(f'{LOAD + entry.offset:#010x} {entry.size:8d} {entry.key:08x} {entry.name}')
    elif args.command == 'text':
        name = f'../Common/Strings/{args.locale}.nloc'
        entry = next((entry for entry in entries if entry.name == name), None)
        if entry is None:
            raise ValueError(f'no localization resource {name!r}')
        texts = localized_text(image, entry)
        if args.key not in texts:
            raise ValueError(f'no localization key {args.key!r}')
        offset, value = texts[args.key]
        print(f'{LOAD + offset:#010x} {args.key}: {value}')
    elif args.command == 'scene':
        english = next(entry for entry in entries if entry.name == '../Common/Strings/English.nloc')
        texts = localized_text(image, english)
        for address, obj, parent, name, key in scene_nodes(image, args.name):
            value = texts.get(key, (0, ''))[1] if key else ''
            line = f'{address:#010x} id={obj:04x} parent={parent:04x} {name}'
            if key:
                line += f' text={key}: {value}'
            if args.filter.lower() in line.lower():
                print(line)
    else:
        entry = next((entry for entry in entries if entry.name == args.name), None)
        if entry is None:
            raise ValueError(f'no resource named {args.name!r}')
        if not entry.name.endswith(('.cvm', '.csv')):
            raise ValueError('show supports CSV/CVM text only; list reports binary payload locations')
        sys.stdout.write(image[entry.offset:entry.offset + entry.size].decode('utf-8-sig'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error) as error:
        raise SystemExit(str(error))
