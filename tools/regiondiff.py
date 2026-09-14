#!/usr/bin/env python3
"""Dump a RAM region over fpsh and diff two dumps to find geometry that changed
between camera modes (open gate vs a normal mode).

  regiondiff.py dump 0xC37CD800 0x2000 captures/opengate.bin
  regiondiff.py diff captures/opengate.bin captures/normal.bin 0xC37CD800
"""
import binascii
import re
import struct
import subprocess
import sys

CHUNK = 0x1000  # 4 KiB (1024 words) per mem get; under the shell ceiling

DIMS = {1920, 1080, 1280, 720, 2016, 1344, 3032, 2012, 1708, 1936, 1090,
        3840, 2160, 3008, 2000, 2088, 1174, 640, 480, 960, 540, 4176, 2174}


def read_region(addr, size):
    out = bytearray()
    off = 0
    while off < size:
        n = min(CHUNK, size - off)
        p = subprocess.run(["./host/fpsh", "mem", "get", f"0x{addr+off:08X},,0x{n:X}"],
                           capture_output=True, text=True)
        r = ((p.stdout or "") + (p.stderr or "")).strip()
        r = r.replace("OKX ", "").replace("\n", "").strip()
        try:
            text = binascii.unhexlify(r).decode("latin1")
        except Exception:
            text = r
        words = re.findall(r"D:0x([0-9A-Fa-f]{8})", text)
        if not words:
            break
        for w in words:
            out += struct.pack("<I", int(w, 16))
        off += len(words) * 4
    return bytes(out[:size])


def cmd_dump(addr, size, path):
    data = read_region(addr, size)
    open(path, "wb").write(data)
    print(f"dumped {len(data)} bytes from {addr:#x} -> {path}")


def cmd_diff(path_a, path_b, base):
    a = open(path_a, "rb").read()
    b = open(path_b, "rb").read()
    n = min(len(a), len(b))
    print(f"diffing {path_a}(A) vs {path_b}(B), {n} bytes from {base:#x}")
    hits = 0
    for off in range(0, n - 3, 4):
        va = struct.unpack_from("<I", a, off)[0]
        vb = struct.unpack_from("<I", b, off)[0]
        if va != vb:
            ta = " A=DIM" if va in DIMS else ""
            tb = " B=DIM" if vb in DIMS else ""
            print(f"  {base+off:08X}: A={va:#010x}({va}){ta}  B={vb:#010x}({vb}){tb}")
            hits += 1
    print(f"{hits} differing words")


def main():
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    if op == "dump":
        cmd_dump(int(sys.argv[2], 16), int(sys.argv[3], 16), sys.argv[4])
    elif op == "diff":
        cmd_diff(sys.argv[2], sys.argv[3], int(sys.argv[4], 16))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
