#!/usr/bin/env python3
"""Snapshot candidate mode-discriminator addresses over the fpsh shell."""
import re
import subprocess
import sys

FPSH = ["./host/fpsh"]

WORDS = {
    "0xC343B590 sel-mode(reg)": 0xC343B590,
    "0xC31B3A4C UI-res-enum": 0xC31B3A4C,
    "0xC31AC530 mode-obj[0]": 0xC31AC530,
    "0xC3075230 settings-ptr": 0xC3075230,
    "0xC072FA00 hook-hits": 0xC072FA00,
    "0xC072FA04 hook-r5": 0xC072FA04,
    "0xC072FA08 hook-+0xD8": 0xC072FA08,
    "0xC072FA0C hook-+0xE0": 0xC072FA0C,
}
GETTERS = ["SetMovRecSize", "SetMovFramerate", "SetMovCinemaDNGQuality",
           "SetCropMode"]


def word(addr):
    out = subprocess.run(FPSH + ["mem", "get", f"0x{addr:08X},,4"],
                         capture_output=True, text=True).stdout
    m = re.search(r"D:0x([0-9A-Fa-f]+)", out)
    return int(m.group(1), 16) if m else None


def getter(name):
    out = subprocess.run(FPSH + ["menu", name], capture_output=True, text=True).stdout
    return out.replace("OKX ", "").strip()


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    print(f"===== {label} =====")
    for name in GETTERS:
        print(f"  {name:26s} = {getter(name)}")
    for name, addr in WORDS.items():
        v = word(addr)
        print(f"  {name:26s} = {v:#010x} ({v})" if v is not None else f"  {name}: read failed")
    ptr = word(0xC3075230)
    if ptr and 0xC0000000 <= ptr < 0xC4000000:
        print(f"  -- settings block @ {ptr:#010x} --")
        for off in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x48):
            v = word(ptr + off)
            print(f"     +0x{off:02x} = {v:#010x} ({v})" if v is not None else f"     +0x{off:02x}: fail")


if __name__ == "__main__":
    main()
