#!/usr/bin/env python3
"""Relabel and retarget a native menu row on a live SIGMA fp 5.02.

The camera rebuilds a menu page when it is entered, and the page is described by
NBU scene data in writable DRAM. So a row's label and its jump destination are
each one word, and both are reversible. Confirmed on hardware 2026-09-12: the
Shoot page 5 Zebra row displayed "False Color" after its label word was changed.

    ./menu_row.py show MainB5
    ./menu_row.py set MainB5 B5_8 --label 2154 --target B5_9
    ./menu_row.py restore MainB5 B5_8

No new row can be added this way: NBU chunks are byte-packed with no slack.
This repurposes a row that already exists. Every write is read back, and the
stock words are journalled to analysis/native_row_backup.json before any change.
A battery pull also restores stock, since nothing is written to flash.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import sys

import menu_resources as res
import menu_text

# Resolved through the module at call time, never bound at import: a test double
# that patches the transport must be able to intercept every write.
def mem_read(address: int, count: int) -> bytes:
    return menu_text.mem_read(address, count)


def shl(*args: str) -> str:
    return menu_text.shl(*args)

ROOT = pathlib.Path(__file__).resolve().parent
JOURNAL = ROOT / "analysis" / "native_row_backup.json"
DRAW_TEXT_LOCALIZED = 0x40000011


def _pool(image: bytes) -> tuple[int, int]:
    kind, span = struct.unpack_from(">II", image, res.NBU_BASE + 12)
    if kind != 1:
        raise ValueError("unexpected NBU name chunk")
    return res.NBU_BASE + 20, res.NBU_BASE + 12 + span


def pool_offset(image: bytes, name: str) -> int:
    """Offset of an existing pool string. Never invents one: the pool is packed."""
    start, end = _pool(image)
    raw = name.encode() + b"\x00"
    hits = [m.start() for m in re.finditer(re.escape(raw), image[start:end])
            if m.start() == 0 or image[start + m.start() - 1] == 0]
    if len(hits) != 1:
        raise ValueError(f"{name!r} is not a unique NBU pool string ({len(hits)} found)")
    return hits[0]


def row_fields(image: bytes, scene: str, row: str) -> dict[str, list[tuple[int, int, str]]]:
    """Locate every label and jump-target word of a row -> [(address, stock, meaning)].

    A row reaches its page from more than one key: Shoot page 5's Zebra row has
    a `Right` record and an OK record, and patching only the first left OK still
    opening the stock page (observed on hardware).

    Addresses are of the containing aligned word; NBU fields are byte-packed and
    frequently unaligned, so the caller must splice rather than overwrite.
    """
    start, end = _pool(image)

    def text(offset: int) -> str:
        """Pool string, or '' for a sentinel/dynamic offset such as 0xFFFFFFFF."""
        at = start + offset
        if not start <= at < end:
            return ""
        stop = image.find(b"\x00", at, end)
        return image[at:stop].decode("utf-8") if stop > at else ""

    nodes = res.scene_nodes(image, scene)
    container = next((n for n in nodes if n[3] == row), None)
    if container is None:
        raise ValueError(f"scene {scene!r} has no row {row!r}")
    scope = [n for n in nodes if n[2] == container[1]]
    if not scope:
        raise ValueError(f"row {row!r} has no child objects")
    item = scope[0][1]
    names = {n[1] for n in nodes if n[2] == item and n[3] == "Name"}
    lo = container[0] - res.LOAD
    # The last row has no following sibling, so fall back to the scene's end,
    # never the name pool's end, which sits far below every scene.
    hi = min((n[0] - res.LOAD for n in nodes if n[0] - res.LOAD > lo
              and n[2] == container[2]), default=res.scene_bounds(image, scene)[1])
    found: dict[str, list[tuple[int, int, str]]] = {"label": [], "target": []}
    pos = lo
    while pos < hi:
        kind, size = struct.unpack_from(">II", image, pos)
        if size < 8 or pos + size > len(image):
            raise ValueError("invalid NBU chunk while scanning row")
        method = text(struct.unpack_from(">I", image, pos + 8)[0]) if size >= 12 else ""
        field = None
        if (kind == 0x10005 and method == "drawText" and size >= 36
                and struct.unpack_from(">I", image, pos + 28)[0] == DRAW_TEXT_LOCALIZED
                and struct.unpack_from(">I", image, pos + 20)[0] in names):
            field, which = pos + 32, "label"
        elif kind == 0x10009 and method == "controlAppState" and size >= 40:
            field, which = pos + 36, "target"
        if field is not None:
            found[which].append((field, struct.unpack_from(">I", image, field)[0],
                                 text(struct.unpack_from(">I", image, field)[0])))
        pos += size
    # A row also carries a Menu/back record whose target is not its own page.
    # Retarget only the records that name the destination, never the way out.
    destination = next((name for _, _, name in found["target"] if name), None)
    found["target"] = [site for site in found["target"] if site[2] == destination]
    return {kind: sites for kind, sites in found.items() if sites}



# The item dispatcher at 0xC00B81E8 indexes this table by menu item id and calls
# a small accessor that returns the address of that item's value byte:
#   bl 0xC008ACF0 ; bl 0xC00897E8 ; movw sb,#<offset> ; add r2,r2,sb ; return r2
# 0xC008ACF0 returns 0xC31AE3B0 and 0xC00897E8 adds 0x4F0C while the flag byte
# at 0xC31B954C is zero, which is the address `menu dump` also reports.
ITEM_TABLE = 0xC07461DC
ITEM_COUNT = 0x127
STORE = 0xC31B32BC


def item_slots(image: bytes) -> dict[int, int]:
    """Menu item id -> byte offset into the settings store, decoded per accessor.

    Two encodings appear at the same instruction slot: `movw sb,#imm16` for
    large offsets and `add r2,r2,#imm` for small ones (Slideshow Repeat is
    `+0x28`). Decoding only the first silently loses the small-offset items.
    """
    slots = {}
    for item in range(ITEM_COUNT):
        function = struct.unpack_from("<I", image, ITEM_TABLE + item * 4 - res.LOAD)[0]
        if not res.LOAD <= function < res.LOAD + len(image) - 0x20:
            continue
        instruction = struct.unpack_from("<I", image, function + 0x18 - res.LOAD)[0]
        if instruction & 0x0FF00000 == 0x03000000:              # movw sb, #imm16
            slots[item] = ((instruction >> 16) & 0xF) << 12 | instruction & 0xFFF
        elif instruction & 0x0FEF0000 == 0x02820000:            # add r2, r2, #imm
            immediate, rotate = instruction & 0xFF, ((instruction >> 8) & 0xF) * 2
            slots[item] = ((immediate >> rotate) | (immediate << (32 - rotate))) & 0xFFFFFFFF \
                if rotate else immediate
    return slots


def rows_targeting(image: bytes, scene: str, destination: str) -> list[str]:
    """Other rows on the page that already open `destination`.

    Hardware: pointing the Zebra row at the False Color page, which its own row
    still opened, left the Menu/back key dead inside that page. The return
    context records are byte-identical between rows, so the collision is the
    only difference; until that is understood, a duplicate destination is
    refused rather than shipped.
    """
    out = []
    for _, _, _, name, _ in res.scene_nodes(image, scene):
        if not re.fullmatch(r"[BYR]\d+(_\w+)?", name):
            continue
        sites = row_fields(image, scene, name).get("target", [])
        if any(target == destination for _, _, target in sites):
            out.append(name)
    return out


def splice(address: int, offset: int) -> None:
    """Write a big-endian pool offset at a possibly unaligned address, verified."""
    lo, hi = address & ~3, (address + 4 + 3) & ~3
    current = bytearray(mem_read(lo, hi - lo))
    wanted = bytearray(current)
    wanted[address - lo:address - lo + 4] = struct.pack(">I", offset)
    for i in range(0, hi - lo, 4):
        if wanted[i:i + 4] != current[i:i + 4]:
            shl("mem", "set", f"{lo+i:#x}",
                f"{struct.unpack_from('<I', wanted, i)[0]:#010x}")
    if mem_read(lo, hi - lo) != bytes(wanted):
        raise RuntimeError(f"write did not stick at {address:#x}; camera may be mid-change")


def journal() -> dict:
    """Load the stock-word journal, accepting the earlier single-site format."""
    if not JOURNAL.exists():
        return {}
    saved = json.loads(JOURNAL.read_text())
    return {row: {kind: entries if isinstance(entries, list) else [entries]
                  for kind, entries in fields.items()}
            for row, fields in saved.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=pathlib.Path, default=res.IMAGE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show").add_argument("scene")
    change = commands.add_parser("set")
    change.add_argument("scene")
    change.add_argument("row")
    change.add_argument("--allow-duplicate-target", action="store_true",
                        help="permit a destination another row already opens")
    change.add_argument("--label", help="localization key, e.g. 2154")
    change.add_argument("--target", help="scene/state name the row opens")
    slots = commands.add_parser("slots")
    slots.add_argument("--only", choices=("toggles", "all"), default="all")
    undo = commands.add_parser("restore")
    undo.add_argument("scene")
    undo.add_argument("row")
    args = parser.parse_args()
    image = args.image.read_bytes()

    if args.command == "show":
        for address, obj, parent, name, key in res.scene_nodes(image, args.scene):
            if not re.fullmatch(r"[BYR]\d+(_\w+)?", name):
                continue
            fields = row_fields(image, args.scene, name)
            parts = []
            for kind in ("label", "target"):
                sites = fields.get(kind, [])
                shown = sorted({site[2] for site in sites})
                if shown:
                    parts.append("/".join(shown) + (f" x{len(sites)}" if len(sites) > 1 else ""))
            print(f"{name:12} id={obj:04x} " + "  ".join(parts))
        return 0

    if args.command == "slots":
        decoded = item_slots(image)
        offsets = sorted(decoded.values())
        block = mem_read(STORE, offsets[-1] + 1)
        for item, offset in sorted(decoded.items()):
            value = block[offset]
            if args.only == "toggles" and value not in (0, 1):
                continue
            print(f"item {item:#05x}  store+{offset:#06x}  {STORE+offset:#010x}  = {value}")
        return 0

    fields = row_fields(image, args.scene, args.row)
    key = f"{args.scene}/{args.row}"
    saved = journal()
    if args.command == "restore":
        if key not in saved:
            raise SystemExit(f"no journalled stock words for {key}")
        for kind, entries in saved[key].items():
            for entry in entries:
                splice(res.LOAD + entry["field"], entry["stock"])
            print(f"{kind}: restored {entries[0]['stock_name']} ({len(entries)} site(s))")
        saved.pop(key)
        JOURNAL.write_text(json.dumps(saved, indent=2) + "\n")
        return 0

    wanted = {"label": args.label, "target": args.target}
    if not any(wanted.values()):
        raise SystemExit("nothing to do: pass --label and/or --target")
    if args.target and not args.allow_duplicate_target:
        clash = [row for row in rows_targeting(image, args.scene, args.target)
                 if row != args.row]
        if clash:
            raise SystemExit(
                f"{', '.join(clash)} already opens {args.target}; two rows sharing a "
                "destination broke the Menu/back key on hardware. "
                "Pass --allow-duplicate-target to do it anyway.")
    entry = saved.setdefault(key, {})
    for kind, value in wanted.items():
        if value is None:
            continue
        if kind not in fields:
            raise SystemExit(f"row {args.row} has no {kind} field")
        sites = fields[kind]
        for field, stock, _ in sites:
            live = mem_read(res.LOAD + field, 4)
            if live != struct.pack(">I", stock) and kind not in entry:
                raise SystemExit(f"{kind} at {res.LOAD+field:#x} is already modified; restore first")
        entry.setdefault(kind, [{"field": field, "stock": stock, "stock_name": name}
                                for field, stock, name in sites])
        JOURNAL.parent.mkdir(exist_ok=True)
        JOURNAL.write_text(json.dumps(saved, indent=2) + "\n")
        offset = pool_offset(image, value)
        for field, _, stock_name in sites:
            splice(res.LOAD + field, offset)
            print(f"{kind}: {stock_name} -> {value}  at {res.LOAD+field:#x}")
    print("reopen the menu page to see the change; ./menu_row.py restore puts it back")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error))
