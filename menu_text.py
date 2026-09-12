#!/usr/bin/env python3
"""Read and rewrite the camera's live UI text pool.

The localised UI strings are readable in the loaded firmware's RAM, and label
bytes have been changed and read back on SIGMA fp 5.02. Whether the native LCD
uses a particular patched copy still requires visual confirmation.

    ./menu_text.py find "Zebra Pattern"          # locate every copy
    ./menu_text.py set  "Zebra Pattern" "fpLAB"  # rename all copies
    ./menu_text.py get  0xC0DA3271 13            # read bytes back
    ./menu_text.py restore "Zebra Pattern" "fpLAB"  # undo that replacement

A label may only be replaced by something no longer than itself: the pool is
packed NUL-separated, so growing a string would eat its neighbour. Shorter is
padded with NULs. Everything is RAM only; a battery pull restores stock text.

Requires the USB shell daemon (see host/fpsh).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
IMAGE = ROOT / "analysis" / "MAIN_c0000000.bin"
LOAD = 0xC0000000


def shl(*args: str) -> str:
    r = subprocess.run([str(ROOT / "host" / "fpsh"), *args],
                       cwd=ROOT, capture_output=True, text=True, timeout=80)
    # fpsh prints decoded OK responses to stdout, but raw OKX to stderr with
    # exit status 1. A transport failure must never masquerade as an empty read.
    out = (r.stdout + r.stderr).strip()
    if out.startswith("OKX "):
        return bytes.fromhex(out[4:].strip()).decode(errors="replace")
    if r.returncode or out.startswith(("ERR", "NG")):
        raise RuntimeError(out or "empty shell response")
    return out


def image() -> bytes:
    if not IMAGE.exists():
        raise SystemExit(f"need the extracted firmware image at {IMAGE}")
    return IMAGE.read_bytes()


def find(text: str) -> list[tuple[int, int]]:
    """Find NUL-terminated label occurrences; capacity includes one terminator.

    A label can be the suffix of a longer display string. Preserve its prefix.
    Never treat following zero bytes as spare capacity: they may be fields.
    """
    raw = text.encode()
    if not raw or b"\x00" in raw:
        raise ValueError("label must be nonempty and contain no NUL")
    fw = image()
    out = []
    for m in re.finditer(re.escape(raw) + rb"\x00", fw):
        # Restrict mutations to the observed English resource region, not
        # matching class/debug strings or instruction bytes elsewhere in MAIN.
        if 0xD90000 <= m.start() < 0xDB0000:
            out.append((LOAD + m.start(), len(raw) + 1))
    return out


def mem_read(addr: int, n: int) -> bytes:
    """read n bytes at any alignment; `mem get` only accepts aligned words"""
    if addr < 0 or n < 0 or addr + n > 1 << 32:
        raise ValueError("invalid 32-bit address range")
    if n == 0:
        return b""
    lo, hi = addr & ~3, (addr + n + 3) & ~3
    out = bytearray()
    # Keep shell replies small; a truncated read is an error, never data.
    for start in range(lo, hi, 64):
        size = min(64, hi - start)
        reply = shl("mem", "get", f"{start:#x},,{size}")
        words = re.findall(r"A:0x([0-9a-fA-F]+), D:0x([0-9a-fA-F]+)", reply)
        if [int(a, 16) for a, _ in words] != list(range(start, start + size, 4)):
            raise RuntimeError(f"incomplete or unordered read at {start:#x}: {reply}")
        out.extend(b"".join(struct.pack("<I", int(w, 16)) for _, w in words))
    return bytes(out[addr - lo:addr - lo + n])


def mem_write_string(addr: int, new: str, capacity: int) -> bytes:
    """splice a NUL-terminated string into RAM through 32-bit pokes"""
    raw = new.encode()
    if b"\x00" in raw or len(raw) >= capacity:
        raise ValueError(f"{new!r} needs {len(raw)+1} bytes, only {capacity} available")
    lo, hi = addr & ~3, (addr + capacity + 3) & ~3
    cur = bytearray(mem_read(lo, hi - lo))
    before = bytes(cur)
    cur[addr - lo:addr - lo + capacity] = raw + b"\x00" * (capacity - len(raw))
    for i in range(0, hi - lo, 4):
        if cur[i:i+4] != before[i:i+4]:
            shl("mem", "set", f"{lo+i:#x}", f"{struct.unpack_from('<I', cur, i)[0]:#010x}")
            if mem_read(lo + i, 4) != cur[i:i+4]:
                raise RuntimeError(f"write did not stick at {lo+i:#x}; stopped, earlier words may be changed")
    actual = mem_read(lo, hi - lo)
    if actual != cur:
        raise RuntimeError(f"read-back mismatch at {lo:#x}; camera memory may be partially changed")
    return actual[addr-lo:addr-lo+capacity]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("find").add_argument("label")
    get = commands.add_parser("get")
    get.add_argument("address", type=lambda s: int(s, 0))
    get.add_argument("length", type=lambda s: int(s, 0))
    for command in ("set", "restore"):
        change = commands.add_parser(command)
        change.add_argument("label")
        change.add_argument("replacement")
    args = parser.parse_args()
    if args.command == "find":
        for addr, cap in find(args.label):
            print(f"{addr:#x}  capacity {cap} (including NUL)")
    elif args.command == "get":
        print(mem_read(args.address, args.length))
    else:
        sites = find(args.label)
        if not sites:
            raise ValueError(f"{args.label!r} is not in the supported English resource region")
        raw = args.replacement.encode()
        capacity = len(args.label.encode()) + 1
        if b"\x00" in raw or len(raw) >= capacity:
            raise ValueError("replacement exceeds the original label or contains NUL")
        original = args.label.encode() + b"\x00"
        replacement = raw.ljust(capacity, b"\x00")
        expected, desired = ((original, replacement) if args.command == "set"
                             else (replacement, original))
        # Preflight every copy before changing any. Do not overwrite unknown
        # live changes or a different firmware image's layout.
        for addr, cap in sites:
            if mem_read(addr, cap) not in (expected, desired):
                raise RuntimeError(f"unexpected live bytes at {addr:#x}; nothing written")
        new = args.replacement if args.command == "set" else args.label
        for addr, cap in sites:
            print(f"{addr:#x} -> {mem_write_string(addr, new, cap)!r}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Also redirect pending buffered stdout so interpreter shutdown cannot
        # emit a second BrokenPipeError.
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        raise SystemExit(0)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error))
