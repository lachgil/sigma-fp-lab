#!/usr/bin/env python3
"""Enable/disable open gate live over the fpshd shell (proven on hardware).

The whole feature is a RAM patch-set. This flips it without a reboot; after
toggling, switch the camera mode away and back to re-latch.

  toggle_opengate.py on      # enable open gate (gated hook, r5==175)
  toggle_opengate.py off     # revert to stock (clean normal recording)
  toggle_opengate.py status  # read current patch state

Requires: fpshd running, camera on the shell interface (class ff).
"""
import re
import subprocess
import sys

# addr: (stock_value, opengate_value, label)
PATCHES = {
    0xC0BE5888: (0x6A, 0x75, "picker slot7 mode (106<->117)"),
    0xC0BE5A28: (0x6A, 0x75, "picker table2"),
    0xC0BE5BC8: (0x6A, 0x75, "picker table3"),
    0xC0B59A28: (0x40888, 0x41C70, "mode117 VMAX (99.9<->29.97)"),
    0xC0BD9A34: (0x640, 0x400, "RWZM live H"),
    0xC0BE1684: (0x640, 0x400, "RWZM rec H"),
    0xC0BD9EFC: (0x640, 0x400, "RWZM live V"),
    0xC0BE1B4C: (0x640, 0x400, "RWZM rec V"),
    0xC072FA10: (0x0, 0x1, "ARMED flag"),
}
HOOK = 0xC043A19C
HOOK_STOCK = 0xE1A00004      # mov r0, r4
HOOK_ARM = 0xEB0BD597        # BL cave hook 0xC072F800

# EXPERIMENTAL darkness compensation (profile-122 digital-gain float at
# 0xC0BD852C+122*4). Stock 0x40000000 (2.0f); proposed 0x409C4000 (4.883f =
# 2.0*1.5625^2) to recover the RWZM summing gain lost by open-gate unity.
# NEEDS a frame-level measurement to confirm factor/direction before trusting.
DARK_ADDR = 0xC0BD8714
DARK_STOCK = 0x40000000
DARK_COMP = 0x409C4000


def fpsh(*args):
    p = subprocess.run(["./host/fpsh", *args], capture_output=True, text=True)
    return ((p.stdout or "") + (p.stderr or "")).strip()


def rd(addr):
    m = re.search(r"D:0x([0-9A-Fa-f]+)", fpsh("mem", "get", f"0x{addr:08X},,4"))
    return int(m.group(1), 16) if m else None


def wr(addr, val):
    for _ in range(8):
        fpsh("mem", "set", f"0x{addr:08X}", f"0x{val:08X}")
        if rd(addr) == val:
            return True
    return False


def cmd_status():
    for addr, (stock, og, label) in PATCHES.items():
        v = rd(addr)
        state = "ON" if v == og else ("off" if v == stock else "?")
        print(f"  {addr:#010x} = {v:#x} [{state}] {label}")
    hv = rd(HOOK)
    print(f"  {HOOK:#010x} = {hv:#x} [{'ARMED' if hv == HOOK_ARM else 'stock' if hv == HOOK_STOCK else '?'}] geometry hook")


def cmd_set(on):
    # data/flags first, hook LAST (arming) or FIRST (disarming) for safety
    if not on:
        wr(HOOK, HOOK_STOCK)
    for addr, (stock, og, _) in PATCHES.items():
        wr(addr, og if on else stock)
    if on:
        wr(HOOK, HOOK_ARM)
    print(f"open gate {'ENABLED' if on else 'DISABLED'}. Switch camera mode away/back to re-latch.")
    cmd_status()


def cmd_dark(on):
    wr(DARK_ADDR, DARK_COMP if on else DARK_STOCK)
    v = rd(DARK_ADDR)
    print(f"darkness comp {'ON' if on else 'off'}: {DARK_ADDR:#x} = {v:#x} "
          f"({'4.883f exp' if v == DARK_COMP else '2.0f stock' if v == DARK_STOCK else '?'}). "
          "EXPERIMENTAL — verify recorded brightness vs stock; switch mode away/back to re-latch.")


def main():
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    if op == "on":
        cmd_set(True)
    elif op == "off":
        cmd_set(False)
    elif op == "status":
        cmd_status()
    elif op == "dark-on":
        cmd_dark(True)
    elif op == "dark-off":
        cmd_dark(False)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
