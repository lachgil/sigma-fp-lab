#!/usr/bin/env python3
"""Inspect a saved fp 5.02 preview-manager capture without camera access.

Provide the documented load address of a single contiguous RAM dump. This
follows actual accessor offsets, not nearby dimension-looking words. A dump
is not necessarily atomic; successful traversal does not prove live coherence.
"""
import argparse
import json
from pathlib import Path
import struct

MANAGER = 0xC375D840


def inspect(data, base):
    missing = []

    def word(address):
        off = address - base
        if off < 0 or off + 4 > len(data):
            missing.append(f"{address:#010x}..{address + 4:#010x}")
            return None
        if address % 4:
            raise ValueError(f"Unaligned pointer-derived read at {address:#010x}")
        return struct.unpack_from("<I", data, off)[0]

    def hexed(value):
        return None if value is None else hex(value)

    result = {"capture_base": hex(base), "capture_bytes": len(data),
              "manager": hex(MANAGER),
              "coherence": "host dump is not atomic; confirm live on the unit"}
    result["manager_pointer_slots"] = {
        "+0x0c live_object": hexed(word(MANAGER + 0x0C)),
        "+0xe8 alloc_slot_not_geometry": hexed(word(MANAGER + 0xE8)),
        "+0xf8 alloc_slot_not_geometry": hexed(word(MANAGER + 0xF8)),
    }
    live = word(MANAGER + 0x0C)
    if live is not None:
        result["live_object"] = hex(live)
        # Record-monitor node: gate on the selector, never a fixed node address.
        result["node_selector_+0x00"] = hexed(word(live + 0x00))
        result["node_selector_is_open_gate_175"] = word(live + 0x00) == 175
        # Dev live finding: sub-object at O+0x5C carries the stale 1936x1090 pair.
        sub = live + 0x5C
        result["record_monitor_subobject"] = hex(sub)
        result["record_monitor_fields"] = {
            "+0x00": hexed(word(sub + 0x00)),
            "+0x0c stale_width": hexed(word(sub + 0x0C)),
            "+0x10 stale_height": hexed(word(sub + 0x10)),
        }
        # Standby descriptor path (accessor 0xC0437E98 objects use their own head).
        descriptor = word(live + 4)
        if descriptor is not None:
            result["descriptor"] = hex(descriptor)
            reads = {off: word(descriptor + off)
                     for off in (0x0C, 0x10, 0x30, 0x80, 0x88, 0x8C, 0xD8, 0xE0)}
            result["descriptor_words"] = {hex(k): hexed(v) for k, v in reads.items()}
            if reads[0x30] is not None:
                result["descriptor_0x30_float"] = repr(
                    struct.unpack("<f", struct.pack("<I", reads[0x30]))[0])
            if None not in (reads[0x0C], reads[0x80], reads[0xD8]):
                width = reads[0xD8] or reads[0x0C]
                align = 4 if reads[0x80] in (0xA, 0xB, 0x1000A) else 8
                result["aligned_horizontal_extent"] = (width + align - 1) & ~(align - 1) & 0xFFFFFFFF
            if None not in (reads[0x10], reads[0xE0], reads[0x88], reads[0x8C]):
                result["vertical_extent"] = (
                    (reads[0xE0] or reads[0x10]) + reads[0x88] + reads[0x8C]) & 0xFFFFFFFF
            result["extent_units"] = "unverified; not automatically byte stride or LCD dimensions"
    if missing:
        result["complete"] = False
        result["missing_ranges"] = missing
    else:
        result["complete"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--base", type=lambda value: int(value, 0), required=True,
                        help="documented dump start address, e.g. 0xC375D7C0")
    args = parser.parse_args()
    try:
        data = args.capture.read_bytes()
        if args.base < 0 or args.base % 4 or args.base + len(data) > 0x100000000:
            raise ValueError("Capture must be aligned and fit the 32-bit address space")
        result = inspect(data, args.base)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
