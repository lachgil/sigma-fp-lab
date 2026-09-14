#!/usr/bin/env python3
"""Control an already cold-boot-installed fp 5.02 gated open-gate payload.

Run only while the camera is idle, never during recording. No recording-state
interlock is known. This tool checks the installed payload and patch words, not
an entire live firmware image. It does not install code or flush instruction cache.

  toggle_opengate.py on|off|status
  toggle_opengate.py dark-on|dark-off  # unverified brightness experiment

Requires the matching gated AutoRun, fpshd and host/fpsh. After changing state,
switch the camera mode away and back to re-latch. On failure, stop testing and
cold-boot without AutoRun to recover; a partial table update is not a usable mode.
"""
import hashlib
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent.parent
PATCHES = {
    0xC0BE5888: (0x6A, 0x75, "picker slot7 mode (106<->117)"),
    0xC0BE5A28: (0x6A, 0x75, "picker table2"),
    0xC0BE5BC8: (0x6A, 0x75, "picker table3"),
    0xC0B59A28: (0x40888, 0x41C70, "mode117 VMAX (99.9<->29.97)"),
    0xC0BD9A34: (0x640, 0x400, "RWZM profile122 first H"),
    0xC0BE1684: (0x640, 0x400, "RWZM profile122 first V"),
    0xC0BD9EFC: (0x640, 0x400, "RWZM profile122 second H"),
    0xC0BE1B4C: (0x640, 0x400, "RWZM profile122 second V"),
}
ARMED = 0xC072FA10
HOOK = 0xC043A19C
HOOK_STOCK = 0xE1A00004
HOOK_ARM = 0xEB0BD597
CODE = 0xC072F800
CODE_BYTES = 264
CODE_SHA256 = "b9f02198efc3b5091c1dbc102ef6a1ea88852c7d5e5276396b89426b904d069a"

# Only the first of two profile floats is tested by this experiment. Neither
# its brightness effect nor the proposed compensation factor is hardware-proven.
DARK_ADDR = 0xC0BD8714
DARK_STOCK = 0x40000000
DARK_COMP = 0x409C4000


def fpsh(*args):
    p = subprocess.run([str(HERE / "host" / "fpsh"), *args],
                       capture_output=True, text=True, check=True, timeout=5)
    return p.stdout.strip()


def rd(addr):
    reply = fpsh("mem", "get", f"0x{addr:08X},,4")
    values = re.findall(r"D:0x([0-9A-Fa-f]{8})\b", reply)
    if len(values) != 1:
        raise RuntimeError(f"Invalid memory reply at {addr:#010x}: {reply!r}")
    return int(values[0], 16)


def wr(addr, val):
    fpsh("mem", "set", f"0x{addr:08X}", f"0x{val:08X}")
    actual = rd(addr)
    if actual != val:
        raise RuntimeError(f"Write failed at {addr:#010x}: wanted {val:#x}, read {actual:#x}")


def preflight(on):
    hook = rd(HOOK)
    if hook not in (HOOK_STOCK, HOOK_ARM) or (on and hook != HOOK_ARM):
        raise RuntimeError("Matching hook is not installed. Cold-boot the gated AutoRun first.")
    payload = b"".join(struct.pack("<I", rd(CODE + off)) for off in range(0, CODE_BYTES, 4))
    if hashlib.sha256(payload).hexdigest() != CODE_SHA256:
        raise RuntimeError("Unknown or missing gated payload; refusing all writes.")
    for addr, (stock, enabled, _) in PATCHES.items():
        if rd(addr) not in (stock, enabled):
            raise RuntimeError(f"Unknown patch value at {addr:#010x}; refusing all writes.")
    if rd(ARMED) not in (0, 1) or rd(DARK_ADDR) not in (DARK_STOCK, DARK_COMP):
        raise RuntimeError("Unknown armed/gain state; refusing all writes.")


def cmd_status():
    for addr, (stock, enabled, label) in PATCHES.items():
        value = rd(addr)
        state = "ON" if value == enabled else "off" if value == stock else "UNKNOWN"
        print(f"  {addr:#010x} = {value:#x} [{state}] {label}")
    hook = rd(HOOK)
    state = "installed" if hook == HOOK_ARM else "stock" if hook == HOOK_STOCK else "UNKNOWN"
    print(f"  {HOOK:#010x} = {hook:#x} [{state}] geometry hook")
    print(f"  ARMED={rd(ARMED):#x}; gain={rd(DARK_ADDR):#x}")


def cmd_set(on):
    preflight(on)
    # Keep the cold-boot code hook installed: change data only. Disarm before
    # touching tables, arm only after every write has been read back correctly.
    try:
        wr(ARMED, 0)
        for addr, (stock, enabled, _) in PATCHES.items():
            wr(addr, enabled if on else stock)
        if not on:
            wr(DARK_ADDR, DARK_STOCK)
        if on:
            wr(ARMED, 1)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        try:
            wr(ARMED, 0)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            print("WARNING: disarm could not be verified.", file=sys.stderr)
        raise
    print(f"open gate {'ENABLED' if on else 'DISABLED'}. Switch camera mode away/back to re-latch.")
    cmd_status()


def cmd_dark(on):
    preflight(on)
    if on and (rd(ARMED) != 1 or any(rd(a) != v[1] for a, v in PATCHES.items())):
        raise RuntimeError("Enable open gate completely before the gain experiment.")
    wr(DARK_ADDR, DARK_COMP if on else DARK_STOCK)
    print(f"EXPERIMENTAL gain {'ON' if on else 'off'}: {DARK_ADDR:#x} = {rd(DARK_ADDR):#x}. "
          "Brightness effect unverified; switch mode away/back to re-latch.")


def main():
    op = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        if op in ("on", "off"):
            cmd_set(op == "on")
        elif op == "status":
            cmd_status()
        elif op in ("dark-on", "dark-off"):
            cmd_dark(op == "dark-on")
        else:
            print(__doc__)
            return 1
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}\nState is not certified safe for recording. "
              "Stop and cold-boot without AutoRun before further use.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
