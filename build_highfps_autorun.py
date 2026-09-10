#!/usr/bin/env python3
"""Build a persistent high-fps CinemaDNG RAW AutoRun by repurposing a CinemaDNG
picker slot to a high-frame-rate sensor mode (data-only picker patch).

The fp exposes 100/120fps only in MOV; the sensor modes exist for RAW too but
are not offered in the CinemaDNG menu. This repoints a menu-reachable slot's
sensor-mode id to a high-fps mode with a MATCHING raster, so it records clean
FHD/2K at high fps (no geometry hook, no green/darkness). Data-only (picker
cells) -> no code/cache concerns; RAM-only, reverts on power-off.

PROVISIONAL: user recorded M58 (slot 4) with no green, but the recorded
frame-rate/continuity is unverified (check the .DNG FrameRate tag + frame count).
3K@120 downscales to 1936x1090 = ~389 MB/s -> needs a fast SSD.

  build_highfps_autorun.py            # default: slot4 (FHD59.94) -> M58 FHD120
"""
import hashlib
import struct
from pathlib import Path

BASE = 0xC0000000
MAIN = Path("analysis/MAIN_c0000000.bin")
MAIN_SHA = "92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4"

# picker tables A/B/C; slot mode-id cell = table + slot*0x10 + 8
TABLES = (0xC0BE5810, 0xC0BE59B0, 0xC0BE5B50)

# (slot, stock_mode, new_mode, label)
PROFILES = {
    "fhd120": (4, 27, 58, "FHD 59.94 slot -> M58 3032x1708@119.88 (->1080, ~389MB/s SSD)"),
    "2k120":  (17, 88, 103, "2K 59.94 slot -> M103 2088x1174@119.88 (~441MB/s SSD)"),
}


def word(img, addr):
    return struct.unpack_from("<I", img, addr - BASE)[0]


def build(profile="fhd120", out="builds/highfps/AutoRun.txt"):
    img = MAIN.read_bytes()
    if hashlib.sha256(img).hexdigest() != MAIN_SHA:
        raise SystemExit("MAIN hash mismatch")
    slot, stock, new, label = PROFILES[profile]
    cells = [t + slot * 0x10 + 8 for t in TABLES]
    for c in cells:
        v = word(img, c)
        if v != stock:
            raise SystemExit(f"slot {slot} cell {c:#x} = {v} (expected stock {stock})")
    lines = [
        "# ============================================================",
        f"# fpSup-highfps: {label}",
        "# fp Ver.5.02 only. RAM-only (data picker patch); power-cycle to revert.",
        "# Select the repurposed preset in the CinemaDNG menu; switch away/back",
        "# to re-latch, then record to a FAST SSD.",
        "# ============================================================",
        "display monitor 0 1",
        "mem set 0xC0BB1208 0xFFFFF8B2",
        "display osd 1 0x00000000",
        "display text fpHFRtest",
        "display osd 1",
        "",
        f"# picker slot {slot}: mode {stock} -> {new} (3 tables)",
    ]
    for c in cells:
        lines.append(f"mem set 0x{c:08X} 0x{new:08X}")
    lines += [
        "",
        "display osd 1 0x00000000",
        "display text fpHFR!",
        "display osd 1",
        "",
    ]
    text = "\n".join(lines)
    o = Path(out)
    o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(text)
    print(f"built {out} ({len(text)} bytes)")
    print(f"profile={profile}: {label}")
    print(f"picker cells patched (stock {stock}->{new}): " + ", ".join(f"0x{c:08X}" for c in cells))
    return text


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "fhd120")
