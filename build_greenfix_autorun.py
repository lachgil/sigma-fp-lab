#!/usr/bin/env python3
"""Build the open-gate GREEN-FIX AutoRun fragment (display-side, code hook).

Open gate greens the LCD/HDMI because the sensor goes 3:2 (M117 3032x2012) but
the live-view display accessor keeps returning the stock 16:9 preview geometry
(1620x911) -> YuvResize writes a region that mismatches the preview buffer =
unwritten Y = green. This installs a CODE hook on the display geometry accessor
0xC0437E98 that, while open gate is ARMED (0xC072FA10, the same flag the record
hook uses), returns a 3:2 (1620x1080) descriptor for the LCD container objects
0xC375EB68 / 0xC375ED3C -- so the preview buffer matches the 3:2 frame.

Mechanism validated against FP3K 0.3.5 (which hooks the same accessor/objects);
the E934 fingerprint on the reference camera == 0xAED11366 (match), and the
handler logic was emulation-checked (unicorn) against FP__V502's real accessor
plus the live object dump: armed+EB68/idx0 -> 3:2 descriptor; every other case
(unarmed, other object, idx!=0) passes through to the stock accessor untouched.

RAM-only; reverts on power-off. APPEND the emitted fragment AFTER your open-gate
patch (it must set 0xC072FA10=1). Cave 0xC072E400/0xC072E430 is in the free
worker region and does NOT overlap the open-gate record cave at 0xC072F800.

ISOLATION NOTE: gated on ARMED + object + fingerprint (like FP3K). It does not
read the live sensor selector (the display path doesn't receive r5==175 the way
the record hook does), so while the open-gate card is loaded the preview is
forced 3:2 for these objects. Power-cycle reverts. If a 3:2 preview in non-open
presets is unwanted, add a live-mode gate after on-camera confirmation of a
reliable liveview mode register.
"""
import hashlib
import struct
from pathlib import Path

BASE = 0xC0000000
MAIN = Path("analysis/MAIN_c0000000.bin")
MAIN_SHA = "92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4"

DESC = 0xC072E400          # 3:2 descriptor (11 words)
CODE = 0xC072E430          # handler entry (hook target)
HOOK = 0xC0437E98          # display geom accessor; stock = push {r4,r5,lr}
HOOK_STOCK = 0xE92D4030
ARMED = 0xC072FA10

# 3:2 preview descriptor: stock E934 with heights 911 -> 1080 (see GREEN-HOOK.md)
DESC_WORDS = [1620, 1080, 0, 0, 1620, 1080, 0, 4, 0x003FC000, 1, 1]

# Handler, assembled at CODE (keystone ARM); mirrors FP3K's proven geom handler
# but reads ARMED (0xC072FA10) instead of FP3K's menu flag. See repo history /
# build log for the source asm; these are the emulation-validated words.
CODE_WORDS = [
    0xE92D4070, 0xE1A04000, 0xE3510000, 0x1A00002A, 0xE30FCA10, 0xE34CC072,
    0xE59CC000, 0xE35C0001, 0x1A000025, 0xE30ECB68, 0xE34CC375, 0xE154000C,
    0x0A00000B, 0xE30ECD3C, 0xE34CC375, 0xE154000C, 0x1A00001D, 0xE5945000,
    0xE301C494, 0xE34CC2F2, 0xE155000C, 0x1A000018, 0xE3056ED6, 0xE34E6025,
    0xEA000006, 0xE5945000, 0xE30EC934, 0xE34CC375, 0xE155000C, 0x1A000010,
    0xE3016366, 0xE34A6ED1, 0xE3092DC5, 0xE348211C, 0xE3003193, 0xE3403100,
    0xE3A0002C, 0xE4D5C001, 0xE022200C, 0xE0020293, 0xE2500001, 0x1AFFFFFA,
    0xE1520006, 0x1A000002, 0xE30E0400, 0xE34C0072, 0xE8BD8070, 0xE1A00004,
    0xE8BD4070, 0xE92D4030, 0xE307CE9C, 0xE34CC043, 0xE12FFF1C,
]


def word(img, addr):
    return struct.unpack_from("<I", img, addr - BASE)[0]


def build(out="builds/greenfix/greenfix_fragment.txt"):
    img = MAIN.read_bytes()
    if hashlib.sha256(img).hexdigest() != MAIN_SHA:
        raise SystemExit("MAIN hash mismatch")
    if word(img, HOOK) != HOOK_STOCK:
        raise SystemExit(f"hook site {HOOK:#x} = {word(img, HOOK):#x} (expected {HOOK_STOCK:#x})")
    # cave must be zero in the reference image (free region)
    for a in range(CODE, CODE + len(CODE_WORDS) * 4, 4):
        if word(img, a) != 0:
            raise SystemExit(f"cave {a:#x} not zero in reference image")
    hook_branch = 0xEA000000 | (((CODE - HOOK - 8) >> 2) & 0xFFFFFF)

    lines = [
        "# ============================================================",
        "# fpOG green-fix (SIGMA fp Ver.5.02 only; RAM-only, reverts on power-off)",
        "# Display accessor 0xC0437E98 -> 3:2 (1620x1080) preview for objects",
        "# 0xC375EB68 / 0xC375ED3C while open gate is ARMED (0xC072FA10).",
        "# APPEND AFTER your open-gate patch. Cave 0xC072E400/0xC072E430.",
        "# ============================================================",
        f"# --- 3:2 descriptor @ 0x{DESC:08X} ---",
    ]
    for i, w in enumerate(DESC_WORDS):
        lines.append(f"mem set 0x{DESC + i*4:08X} 0x{w:08X}")
    lines.append(f"# --- handler @ 0x{CODE:08X} ({len(CODE_WORDS)} words) ---")
    for i, w in enumerate(CODE_WORDS):
        lines.append(f"mem set 0x{CODE + i*4:08X} 0x{w:08X}")
    lines.append("# --- ARM the hook LAST (stock 0xC0437E98 = 0xE92D4030) ---")
    lines.append(f"mem set 0x{HOOK:08X} 0x{hook_branch:08X}")
    text = "\n".join(lines) + "\n"

    o = Path(out)
    o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(text)
    print(f"built {out} ({len(text)} bytes); hook branch 0x{hook_branch:08X} -> 0x{CODE:08X}")
    return text


if __name__ == "__main__":
    build()
