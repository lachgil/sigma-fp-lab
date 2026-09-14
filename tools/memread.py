#!/usr/bin/env python3
"""Read a memory range over fpsh and annotate words that look like dimensions."""
import re
import subprocess
import sys

DIMS = {1920: "1920", 1080: "1080", 1280: "1280", 720: "720", 2016: "2016",
        1344: "1344", 3032: "3032", 2012: "2012", 1708: "1708", 1936: "1936",
        1090: "1090", 3840: "3840", 2160: "2160", 3008: "3008", 2000: "2000",
        6064: "6064", 4042: "4042", 2088: "2088", 1174: "1174"}


def word(addr):
    out = subprocess.run(["./host/fpsh", "mem", "get", f"0x{addr:08X},,4"],
                         capture_output=True, text=True).stdout
    m = re.search(r"D:0x([0-9A-Fa-f]+)", out)
    return int(m.group(1), 16) if m else None


def main():
    start = int(sys.argv[1], 16)
    count = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x40
    for off in range(0, count, 4):
        v = word(start + off)
        if v is None:
            print(f"  {start+off:08X}: read fail"); continue
        tags = []
        if v in DIMS:
            tags.append(f"<-{DIMS[v]}")
        if 0xC0000000 <= v < 0xC4000000:
            tags.append("ptr")
        print(f"  {start+off:08X}: {v:#010x} ({v}) {' '.join(tags)}")


if __name__ == "__main__":
    main()
