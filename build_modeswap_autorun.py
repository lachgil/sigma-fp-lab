#!/usr/bin/env python3
"""Repoint a CinemaDNG picker slot to ANY IMX410 sensor mode (mode-unlock tool).

The CinemaDNG menu exposes only 26 picker slots, but the sensor has 70 modes
(analysis/imx410 CSVs). This swaps a slot's mode id to any target mode so it can
be recorded/tested. RAM-only; power-cycle reverts.

READOUT QUALITY is the whole point (see MODE-MAP.md):
  * FULL (sampling 1,1,1,1) = every photosite -> sharpest, "beats UHD" when
    downscaled. 23 modes: the 6K/5.7K/4.2K/4K-3:2 families.
  * 2x2 (sampling 2,2,2,2) = discards 3 of 4 photosites -> soft. This is M117
    OpenGate AND every 3K/2K CinemaDNG preset.
  * 3x2 / 3x3 = heavier subsample (the 2016-wide high-fps modes).

CLEAN swap (target raster == slot's current raster): just the picker id changes,
no geometry hook. e.g. slot 13 (M102, 4176x2174 12-bit) -> M6 (same raster,
14-bit). GEOM-HOOK swap (raster differs): also needs the record-geometry hook
(build_gated_autorun style) + VMAX, or the recorder crops/greens like OpenGate.

  build_modeswap_autorun.py <slot> <target_mode_id>

CAVEAT: 14-bit modes (M0/6/95/100/122/129/141/144) are full-readout but the
CinemaDNG recorder is only proven at 12-bit (MODES.txt) -- test, don't trust.
"""
import csv
import hashlib
import struct
import sys
from pathlib import Path

BASE = 0xC0000000
MAIN = Path("analysis/MAIN_c0000000.bin")
MAIN_SHA = "92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4"
GEO = Path("reference/fpSup/gyro/analysis_imx410/imx410_mode_geometry.csv")

# picker: three parallel tables; slot cell = table + slot*0x10 + 8 holds the mode id
TABLES = (0xC0BE5810, 0xC0BE59B0, 0xC0BE5B50)


def word(img, a):
    return struct.unpack_from("<I", img, a - BASE)[0]


def load_modes():
    m = {}
    for r in csv.DictReader(open(GEO)):
        mid = int(r["mode_id"])
        samp = (r["field_0x44"], r["field_0x48"], r["field_0x4c"], r["field_0x50"])
        m[mid] = dict(W=int(r["field_0x04"]), H=int(r["field_0x08"]),
                      fps=int(r["field_0x40"]),
                      samp="FULL" if samp == ("1", "1", "1", "1")
                      else "2x2" if samp == ("2", "2", "2", "2") else ",".join(samp))
    return m


def build(slot, target, out=None):
    img = MAIN.read_bytes()
    if hashlib.sha256(img).hexdigest() != MAIN_SHA:
        raise SystemExit("MAIN hash mismatch")
    modes = load_modes()
    if target not in modes:
        raise SystemExit(f"unknown target mode {target}; valid: {sorted(modes)}")
    cells = [t + slot * 0x10 + 8 for t in TABLES]
    cur = word(img, cells[0])
    for c in cells[1:]:
        if word(img, c) != cur:
            raise SystemExit(f"slot {slot} picker tables disagree ({c:#x})")
    if cur not in modes:
        raise SystemExit(f"slot {slot} current mode {cur} not in table")
    src, dst = modes[cur], modes[target]
    clean = (src["W"], src["H"]) == (dst["W"], dst["H"])
    out = out or f"builds/modeswap/AutoRun.txt"

    lines = [
        "# ============================================================",
        f"# mode-swap: slot {slot}  M{cur} ({src['W']}x{src['H']} {src['samp']})"
        f" -> M{target} ({dst['W']}x{dst['H']} {dst['samp']} @{dst['fps']})",
        "# SIGMA fp Ver.5.02; RAM-only, power-cycle reverts. Select the slot's",
        "# CinemaDNG preset, toggle mode away/back to re-latch, record to SSD.",
    ]
    if clean:
        lines.append("# CLEAN swap: raster matches slot -> no geometry hook needed.")
    else:
        lines.append(f"# WARNING: raster {dst['W']}x{dst['H']} != slot {src['W']}x{src['H']}"
                      " -> needs the record-geometry hook (build_gated style) or it")
        lines.append("# will crop/green like OpenGate. This file only swaps the picker id.")
    lines.append("# ============================================================")
    for c in cells:
        lines.append(f"mem set 0x{c:08X} 0x{target:08X}")
    text = "\n".join(lines) + "\n"
    o = Path(out)
    o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(text)
    print(f"built {out}: slot {slot} M{cur} -> M{target} "
          f"({dst['W']}x{dst['H']} {dst['samp']} @{dst['fps']}fps) "
          f"[{'CLEAN' if clean else 'NEEDS GEOM HOOK'}]")
    print("picker cells: " + ", ".join(f"0x{c:08X}" for c in cells))
    return text


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_modeswap_autorun.py <slot> <target_mode_id>")
    build(int(sys.argv[1]), int(sys.argv[2]))
