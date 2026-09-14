#!/usr/bin/env python3
"""Build an experimental picker-only AutoRun targeting high-rate sensor modes.

The user reports 100/120fps exposed in MOV but not in CinemaDNG. These patches
select table-derived high-rate modes through other picker slots. M58 was read
back after a take, but neither recorded cadence nor downstream geometry has
been established. Matching sensor rasters alone do not prove clean output.

No green-preview fix, USB shell or runtime firmware guard is included.
Use only on fp 5.02. RAM-only; remove AutoRun and power-cycle to revert.
Cold-boot this card alone, not layered over an open-gate session.

  build_highfps_autorun.py [fhd120|2k120]
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
    "fhd120": (4, 27, 58, "FHD 59.94 slot -> M58 (table 3032x1708@119.88; output unverified)"),
    "2k120":  (17, 88, 103, "2K 59.94 slot -> M103 (table 2088x1174@119.88; output unverified)"),
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
        "# EXPERIMENTAL: recorded fps/geometry and sustained throughput unverified.",
        "# Boot alone, select the repurposed preset and switch away/back to re-latch.",
        "# This build contains no USB shell. Preserve original card files separately.",
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
