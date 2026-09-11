#!/usr/bin/env python3
"""Live geometry probe for the open-gate RECORD-MONITOR green (fp Ver.5.02).

The record-monitor green is a stale FHD 16:9 pair inside the open-gate 3:2 node.
This tool follows the manager pointer DYNAMICALLY (nodes move per state) and only
acts when the node selector is 175 (open gate). It is read-only by default.

  greenprobe.py chain            # dump manager -> node -> sub-object geometry
  greenprobe.py watch [n]        # poll the stale pair n times (default 20)
  greenprobe.py set <w> <h>      # EXPERIMENTAL poke of the stale pair (reverts)
  greenprobe.py restore          # write the stock 1936x1090 pair back

Requires: fpshd running (sudo), camera on the class-ff shell, open gate ARMED and
its preset selected. Run only while IDLE unless you are deliberately watching a
take. Nothing is flashed; a battery-out cold boot restores everything.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANAGER = 0xC375D840          # geometry manager singleton
LIVE_PTR = MANAGER + 0x0C     # [M+0x0c] -> live/record-monitor node (dynamic)
SEL_OFF = 0x00               # node+0x00 = selector (175 == open gate)
SUB_OFF = 0x5C               # node+0x5C = geometry sub-object (dev live finding)
W_OFF = SUB_OFF + 0x0C       # stale width  (open gate: stuck 1936)
H_OFF = SUB_OFF + 0x10       # stale height (open gate: stuck 1090)
OPEN_GATE_SELECTOR = 175
STOCK_W, STOCK_H = 1936, 1090
OPEN_W, OPEN_H = 3032, 2012


def fpsh(*args):
    p = subprocess.run([str(HERE / "host" / "fpsh"), *args],
                       capture_output=True, text=True, check=True, timeout=5)
    return p.stdout.strip()


def rd(addr):
    reply = fpsh("mem", "get", f"0x{addr:08X},,4")
    values = re.findall(r"D:0x([0-9A-Fa-f]{8})\b", reply)
    if len(values) != 1:
        raise RuntimeError(f"Bad memory reply at {addr:#010x}: {reply!r}")
    return int(values[0], 16)


def wr(addr, val):
    fpsh("mem", "set", f"0x{addr:08X}", f"0x{val:08X}")
    if rd(addr) != val:
        raise RuntimeError(f"Write not held at {addr:#010x} (wanted {val:#x})")


def resolve():
    """Return (node, selector) following the live pointer; never cache the node."""
    node = rd(LIVE_PTR)
    if not (0xC0000000 <= node < 0xC4000000):
        raise RuntimeError(f"[M+0x0c] = {node:#x} is not a RAM node; is the camera up?")
    return node, rd(node + SEL_OFF)


def require_open_gate():
    node, sel = resolve()
    if sel != OPEN_GATE_SELECTOR:
        raise RuntimeError(f"node {node:#x} selector = {sel} (need {OPEN_GATE_SELECTOR}); "
                           "select the open-gate preset and re-latch first.")
    return node


def cmd_chain():
    node, sel = resolve()
    print(f"manager   {MANAGER:#010x}")
    print(f"[M+0x0c]  -> node {node:#010x}   selector(+0x00) = {sel}"
          f"  {'[OPEN GATE]' if sel == OPEN_GATE_SELECTOR else '[not open gate]'}")
    sub = node + SUB_OFF
    print(f"sub-object {sub:#010x} (node+0x5c):")
    for off in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14):
        v = rd(sub + off)
        tag = ""
        if off == 0x0C:
            tag = f"  <- width  (stale 1936 = green)" if v == STOCK_W else "  <- width"
        if off == 0x10:
            tag = f"  <- height (stale 1090 = green)" if v == STOCK_H else "  <- height"
        print(f"  +0x{off:02x} = {v:#010x} ({v}){tag}")
    print(f"rec-obj [M+0x10] = {rd(MANAGER + 0x10):#010x}   "
          f"alloc slots +0xe8={rd(MANAGER + 0xE8):#010x} +0xf8={rd(MANAGER + 0xF8):#010x}")


def cmd_watch(n):
    print(f"watching stale pair for {n} reads (Ctrl-C to stop):")
    last = None
    for _ in range(n):
        node = rd(LIVE_PTR)
        w, h = rd(node + W_OFF), rd(node + H_OFF)
        cur = (node, w, h)
        if cur != last:
            print(f"  node {node:#010x}  {w}x{h}")
            last = cur


def cmd_set(w, h):
    node = require_open_gate()
    print(f"EXPERIMENTAL: node {node:#010x} sub+0x0c/+0x10 -> {w}x{h}")
    print("Note: the record-monitor buffer is allocated 16:9 at record-start, so "
          "this typically reverts per frame and will not resize a running take. "
          "Set it in STANDBY, then start recording, to test record-start pickup.")
    wr(node + W_OFF, w)
    wr(node + H_OFF, h)
    cmd_chain()


def cmd_restore():
    node = require_open_gate()
    wr(node + W_OFF, STOCK_W)
    wr(node + H_OFF, STOCK_H)
    print(f"restored node {node:#010x} sub pair to {STOCK_W}x{STOCK_H}")


def main():
    a = sys.argv[1:]
    op = a[0] if a else ""
    try:
        if op == "chain":
            cmd_chain()
        elif op == "watch":
            cmd_watch(int(a[1]) if len(a) > 1 else 20)
        elif op == "set" and len(a) == 3:
            cmd_set(int(a[1]), int(a[2]))
        elif op == "restore":
            cmd_restore()
        else:
            print(__doc__)
            return 1
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
