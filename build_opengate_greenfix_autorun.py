#!/usr/bin/env python3
"""Build the combined OPEN-GATE + GREEN-FIX boot-and-shoot AutoRun (no USB shell).

One card, one boot: engages mode-gated open gate (M117 3032x2012 @29.97 CinemaDNG)
AND the display-side green fix, so the LCD/HDMI preview is a clean 3:2 with no
green. Everything is RAM-only; remove AutoRun.txt and power-cycle (battery out)
to return to stock.

Pieces (all firmware-verified against FP__V502 / analysis MAIN, all emulated):
  * open-gate data patches   -- upstream fpSup set (picker slot7 M106->M117 x3,
                                mode117 VMAX ->29.97, four profile-122 RWZM cells
                                -> unity). Identical to toggle_opengate.py.
  * record-geometry hook cave @ 0xC072F800 (src/rowpatch_gated.S) gated on
                                ARMED + row 1936x1090 + selector r5==175, writes
                                the 3032x2012 canvas. BL from 0xC043A19C.
  * green-fix cave @ 0xC072E400/0xC072E430: hooks the display geom accessor
                                0xC0437E98; while ARMED, returns a 3:2 (1620x1080)
                                descriptor for the LCD objects 0xC375EB68/ED3C
                                (see build_greenfix_autorun.py / GREEN-HOOK.md).

Ordering: data first, caves next, ARMED flag, hook words LAST (safe arm order).
No OSD/display commands are issued (they would touch the very path being fixed).

The two cave payloads are the emulation-validated words; regenerate them from
src/rowpatch_gated.S (keystone/clang) and build_greenfix_autorun.py if changed.
"""
import hashlib
import struct
from pathlib import Path

BASE = 0xC0000000
MAIN = Path("analysis/MAIN_c0000000.bin")
MAIN_SHA = "92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4"

LOG, ARMED = 0xC072FA00, 0xC072FA10

DATA_PATCHES = (
    (0xC0B59A28, 0x00040888, 0x00041C70, "mode117 VMAX 2184 -> 7280 (29.97)"),
    (0xC0BE5888, 0x6A, 0x75, "picker table1 idx7: M106 -> M117"),
    (0xC0BE5A28, 0x6A, 0x75, "picker table2 idx7: M106 -> M117"),
    (0xC0BE5BC8, 0x6A, 0x75, "picker table3 idx7: M106 -> M117"),
    (0xC0BD9A34, 0x640, 0x400, "profile122 live RWZM H -> unity"),
    (0xC0BE1684, 0x640, 0x400, "profile122 rec  RWZM H -> unity"),
    (0xC0BD9EFC, 0x640, 0x400, "profile122 live RWZM V -> unity"),
    (0xC0BE1B4C, 0x640, 0x400, "profile122 rec  RWZM V -> unity"),
)

REC_HOOK, REC_STOCK, REC_CODE = 0xC043A19C, 0xE1A00004, 0xC072F800
REC_WORDS = [
    0xE92D500F, 0xE284105C, 0xE30FCA00, 0xE34CC072, 0xE30F0A10, 0xE34C0072,
    0xE5900000, 0xE3500000, 0x0A000035, 0xE5913000, 0xE3000790, 0xE1530000,
    0x1A000031, 0xE5913004, 0xE3000442, 0xE1530000, 0x1A00002D, 0xE30000AF,
    0xE1550000, 0x1A00002A, 0xE59C0000, 0xE3500000, 0x1A000006, 0xE58C5004,
    0xE30000D8, 0xE7913000, 0xE58C3008, 0xE30000E0, 0xE7913000, 0xE58C300C,
    0xE3003BD8, 0xE3000000, 0xE7813000, 0xE30037DC, 0xE3000004, 0xE7813000,
    0xE3003BC0, 0xE3000024, 0xE7813000, 0xE30037D0, 0xE300002C, 0xE7813000,
    0xE3003BD8, 0xE30000D8, 0xE7813000, 0xE30037DC, 0xE30000E0, 0xE7813000,
    0xE3003BD8, 0xE30000DC, 0xE7813000, 0xE30037DC, 0xE30000E4, 0xE7813000,
    0xE3003BD8, 0xE30000F4, 0xE7813000, 0xE30037DC, 0xE30000F8, 0xE7813000,
    0xE59C3000, 0xE2833001, 0xE58C3000, 0xE8BD500F, 0xE1A00004, 0xE12FFF1E,
]

GREEN_HOOK, GREEN_STOCK = 0xC0437E98, 0xE92D4030
DESC, CODE = 0xC072E400, 0xC072E430
DESC_WORDS = [1620, 1080, 0, 0, 1620, 1080, 0, 4, 0x003FC000, 1, 1]
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


def word(img, a):
    return struct.unpack_from("<I", img, a - BASE)[0]


def verify(img):
    if hashlib.sha256(img).hexdigest() != MAIN_SHA:
        raise SystemExit("MAIN hash mismatch")
    for a, stock, _, _ in DATA_PATCHES:
        if word(img, a) != stock:
            raise SystemExit(f"data stock mismatch at {a:#x}")
    if word(img, REC_HOOK) != REC_STOCK:
        raise SystemExit("record hook site not stock")
    if word(img, GREEN_HOOK) != GREEN_STOCK:
        raise SystemExit("green hook site not stock")
    for lo, hi in ((DESC, CODE + len(CODE_WORDS) * 4),
                   (REC_CODE, REC_CODE + len(REC_WORDS) * 4), (LOG, LOG + 0x10)):
        if any(img[lo - BASE:hi - BASE]):
            raise SystemExit(f"cave {lo:#x}..{hi:#x} not zero in reference image")


def build(out="builds/opengate-greenfix/AutoRun.txt"):
    img = MAIN.read_bytes()
    verify(img)
    rec_branch = 0xEB000000 | (((REC_CODE - REC_HOOK - 8) >> 2) & 0xFFFFFF)
    grn_branch = 0xEA000000 | (((CODE - GREEN_HOOK - 8) >> 2) & 0xFFFFFF)

    L = ["# ================================================================",
         "# fpOG-openGate + GREEN-FIX  (SIGMA fp Ver.5.02 ONLY)",
         "# Cold-boot RAM patches; remove AutoRun.txt + power-cycle (battery",
         "# out) to return to stock. Open gate M117 3032x2012 @29.97 CinemaDNG",
         "# (record hook gated on selector==175) + display 3:2 hook = NO GREEN.",
         "# Data first, caves, ARM, hooks LAST. Record to a FAST SSD.",
         "# ================================================================",
         "# --- open-gate data patches ---"]
    for a, stock, val, c in DATA_PATCHES:
        L.append(f"# {c}; stock 0x{stock:08X}")
        L.append(f"mem set 0x{a:08X} 0x{val:08X}")
    L += ["", "# --- clear telemetry log ---"]
    for o in range(0, 0x10, 4):
        L.append(f"mem set 0x{LOG + o:08X} 0x00000000")
    L += ["", f"# --- record-geometry hook cave @ 0x{REC_CODE:08X} ({len(REC_WORDS)} words) ---"]
    for i, w in enumerate(REC_WORDS):
        L.append(f"mem set 0x{REC_CODE + i*4:08X} 0x{w:08X}")
    L += ["", f"# --- green-fix 3:2 descriptor @ 0x{DESC:08X} ---"]
    for i, w in enumerate(DESC_WORDS):
        L.append(f"mem set 0x{DESC + i*4:08X} 0x{w:08X}")
    L += ["", f"# --- green-fix handler @ 0x{CODE:08X} ({len(CODE_WORDS)} words) ---"]
    for i, w in enumerate(CODE_WORDS):
        L.append(f"mem set 0x{CODE + i*4:08X} 0x{w:08X}")
    L += ["", "# --- arm the feature ---", f"mem set 0x{ARMED:08X} 0x00000001"]
    L += ["", "# --- hooks LAST: record (BL) then green (B) ---",
          "# record: stock 0xC043A19C = 0xE1A00004 (mov r0,r4)",
          f"mem set 0x{REC_HOOK:08X} 0x{rec_branch:08X}",
          "# green:  stock 0xC0437E98 = 0xE92D4030 (push)",
          f"mem set 0x{GREEN_HOOK:08X} 0x{grn_branch:08X}"]
    text = "\n".join(L) + "\n"
    o = Path(out)
    o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(text)
    print(f"built {out} ({len(text)} bytes); "
          f"record BL 0x{rec_branch:08X}, green B 0x{grn_branch:08X}")
    return text


if __name__ == "__main__":
    build()
