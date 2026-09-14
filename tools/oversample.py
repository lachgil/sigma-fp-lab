#!/usr/bin/env python3
"""Oversampled FHD: read the full 6064x3412 sensor and scale it down to 1936x1090.

Stock FHD comes from a binned 3032x1708 mode, which is where the line-skipping
aliasing comes from. The camera also has the full-width 16:9 modes that stock
UHD uses, and the per-profile `raw_zoom` scaler can take one of those down to
FHD instead:

    picker cells  binned mode  ->  6064x3412 mode for that framerate
    raw_zoom      0x640 (1600) ->  0x0C80 (3200) = 3.125x

6064 / 3.125 = 1940 and 3412 / 3.125 = 1092, so the output canvas is unchanged
and nothing downstream has to move: no geometry hook, no canvas patch, and the
card sees ordinary FHD throughput. Credit for the idea goes to the fpSup
community; the scaler it turns on is the one decoded in GREEN-HOOK.md.

    ./oversample.py status
    ./oversample.py on 23.976
    ./oversample.py off

RAM only. A battery pull restores stock regardless of what this wrote.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import struct

from menu_text import mem_read, shl

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "analysis" / "oversample_state.json"

# Full-width 16:9 readouts, 12-bit, one per framerate. These are the modes stock
# UHD uses; hvbin reads 1/1 in them, which is what removes the line skipping.
# 6064x3412 is 1.777 against the 1936x1090 canvas's 1.776, so it fills the frame
# exactly -- 4176x2174 (1.921) and 3968x2640 (1.503) do not, which is what put
# a shrunken picture in the corner every earlier time.
FULL_WIDTH = {"23.976": 0x65, "25": 0x7B, "29.97": 0x07}

# The binned mode each framerate ships with, used to find that rate's cells.
STOCK_MODE = {"23.976": 0x6D, "25": 0x7D, "29.97": 0x6A}

# Per-profile raw_zoom arrays; profile = probed selector - 53, stride 4.
ZOOM_BASES = (0xC0BD9A34, 0xC0BD9EFC, 0xC0BE1684, 0xC0BE1B4C)

# Measured on hardware from the hook's probe. 24.000 is deliberately absent:
# no selector has ever been read for it, and guessing one would write another
# framerate's raw_zoom cells, which is exactly how three earlier hardware runs
# were wasted.
SELECTOR = {"29.97": 175, "23.976": 176, "25": 180}
PICKER = range(0xC0BE5700, 0xC0BE5D00, 4)
UNITY, STOCK_ZOOM, OVERSAMPLE = 0x400, 0x640, 0xC80


def word(address: int) -> int:
    return struct.unpack("<I", mem_read(address, 4))[0]


def write(address: int, value: int) -> None:
    shl("mem", "set", f"{address:#x}", f"{value:#010x}")
    if word(address) != value:
        raise SystemExit(f"write did not stick at {address:#x}")


def zoom_cells(rate: str) -> list[int]:
    profile = SELECTOR[rate] - 53
    return [base + (profile - 122) * 4 for base in ZOOM_BASES]


def picker_cells(holding: int) -> list[int]:
    """Cells currently holding `holding`, read in bulk.

    One `mem get` per word is ~400 round trips per framerate, which is slow
    enough to look like a hang and long enough to collide with the camera
    doing something else.
    """
    block = mem_read(PICKER.start, PICKER.stop - PICKER.start)
    return [PICKER.start + offset for offset in range(0, len(block), 4)
            if struct.unpack_from("<I", block, offset)[0] == holding]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    enable = commands.add_parser("on")
    enable.add_argument("rate", choices=sorted(FULL_WIDTH))
    commands.add_parser("off")
    args = parser.parse_args()
    saved = json.loads(JOURNAL.read_text()) if JOURNAL.exists() else {}

    if args.command == "status":
        for rate, mode in sorted(FULL_WIDTH.items()):
            cells = picker_cells(mode)
            zooms = [word(address) for address in zoom_cells(rate)]
            state = "OVERSAMPLED" if cells and set(zooms) == {OVERSAMPLE} else "stock"
            print(f"  FHD {rate:<7} {state:12} cells={len(cells)} zoom={[hex(z) for z in zooms]}")
        return 0

    if args.command == "off":
        if not saved:
            raise SystemExit("nothing recorded as changed")
        for address, value in saved.items():
            write(int(address, 0), int(value, 0) if isinstance(value, str) else value)
        JOURNAL.unlink()
        print(f"restored {len(saved)} cells to stock")
        return 0

    rate = args.rate
    cells = picker_cells(STOCK_MODE[rate])
    if not cells:
        raise SystemExit(f"no picker cell holds the stock mode for {rate}; "
                         "it may already be oversampled, or another option owns it")
    zooms = zoom_cells(rate)
    if {word(address) for address in zooms} != {STOCK_ZOOM}:
        raise SystemExit("raw_zoom is not at its stock 0x640; refusing to guess")
    # Journal per rate, so several framerates can be on at once and `off` still
    # restores all of them.
    saved.update({hex(address): word(address) for address in cells + zooms})
    JOURNAL.parent.mkdir(exist_ok=True)
    JOURNAL.write_text(json.dumps(saved, indent=2) + "\n")
    for address in cells:
        write(address, FULL_WIDTH[rate])
    for address in zooms:
        write(address, OVERSAMPLE)
    print(f"FHD {rate}: {len(cells)} picker cells -> mode {FULL_WIDTH[rate]:#x}, "
          f"raw_zoom -> {OVERSAMPLE:#x}")
    print("switch the preset away and back to re-latch, then record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
