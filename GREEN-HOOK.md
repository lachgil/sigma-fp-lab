# Live-view green — analysis + hook design (needs hardware iteration to verify)
## CONCRETE HOOK POINT (added after accessor disasm)
- lv accessor `0xC04376E0` = `ldr r1,[r0,#0xc]; mov r0,r1; bx lr` → returns the
  **lv-geom object at manager+0xc**. rec accessor `0xC04371F0` uses manager+0x10.
- In MovSigProcess: `0xC0428B14 ldr r0,[r4]; 0xC0428B18 bl 0xC04376E0;
  0xC0428B1C mov r2,r0; 0xC0428B20 str r2,[r4,#4]` → **lv-geom obj at r4+4**,
  **rec-geom obj at r4+8** (rec is already the correct 3032x2012 in open gate,
  set by our record hook).
- **Proposed green hook:** replace `0xC0428B20 str r2,[r4,#4]` (or insert right
  after) with a BL to a cave routine that, when ARMED (0xC072FA10) and the live
  mode is open gate, copies the rec-geom size fields (obj+0x00 size_h, +0x04
  size_v, and rwzm +0x48/+0x4c) FROM the rec obj (r4+8 target) INTO the lv obj
  (r2), so the preview/HDMI buffer is built for the 3:2 frame. Replay the
  displaced `str r2,[r4,#4]` then return.
- **Unverified offline (freeze risk):** the lv-geom object's exact field offsets
  and the downstream buffer-allocation must be confirmed live (dump the lv obj at
  r4+4 in open gate). A wrong size can desync the display allocator. Bring-up:
  read the lv obj layout, write the cave routine, arm, observe. Draft only.


## Confirmed by disassembly
- Record and live-view geometry are **separate paths**. The record path is a
  single-caller linear chain: `0xC0439740` → `0xC0439CC8` → `0xC043A158`
  (selector r5 → profile table `0xC0BD05D4`) → builder `0xC0439D18` (fills the
  record FieldAngle row at obj+0x5C; our record hook `0xC043A19C` gates on r5==175).
- `0xC0439740` first calls `0xC0439C10` (a virtual-dispatched sibling via manager
  object `*(0xC2F21678)`, vtable+0xc) — the non-record geometry setup.
- **Display/live-view output geometry** is built in **MovSigProcess `0xC0428AE0`**:
  - `bl 0xC04371F0` → **rec-geom object**, stored at `r4+0x8`.
  - `bl 0xC04376E0` → **live-view-geom object**, stored at `r4+0x4`.
  - It then builds `XC_MovSigYuvOutParam` from these and publishes via XC_Subject
    to display observers (LCD `XC_DisplayLcd 0xC0B54FFC` + HDMI `XC_DisplayHdmi
    0xC0729B48`). YUV buffer filled by YuvResize task ~`0xC03CB090`.
- Geometry accessors on the singleton `0xC375D840` family: `0xC0436590`
  (get-singleton), `0xC04371F0` (rec), `0xC04376E0` (liveview), `0xC0436FE8`,
  `0xC0437668`, `0xC04375C8`, `0xC0437060`.

## Mechanism (confirmed structurally; exact stale field inferred)
Open gate changes the **sensor** to 3:2 (mode 117, 3032x2012) and forces the
**shared** RWZM cells to unity. The live-view path (`0xC04376E0` object) still
carries the stock ~16:9 preview geometry, so YuvResize writes a region that does
not match the preview/HDMI buffer stride/height → unwritten YUV (Y=0) = green.
Confirmed by hardware A/B: M58 (standard 3032x1708→1080 raster) shows NO green;
only open gate's non-standard 3:2 raster greens. The record hook can't help — it
doesn't run for the display path.

## Hook design (to implement + verify on hardware)
Two candidate gated interventions, in order of surgical-ness:
1. **Hook the live-view geom accessor result in MovSigProcess** (at/after
   `0xC0428B18 bl 0xC04376E0`, before `str r2,[r4,#4]`): when open gate is armed
   (ARMED flag or live mode==117), overwrite the returned lv-geom object's
   size_h/size_v (block +0x00/+0x04) and its rwzm (+0x48/+0x4c) to describe the
   3032x2012 3:2 frame so the preview buffer matches. Requires reading the
   lv-geom object layout live first.
2. **Force the live-view singleton block** (container liveview block) at the
   builder that fills it (the `0xC0439C10` virtual sibling) — analogous to the
   record hook but for the display block.
BLOCKER for verification: the display geometry is **recomputed every frame**
(confirmed: RAM pokes to the 1620x911 blocks reverted), so only a CODE hook (not
a RAM poke) can hold it, and it MUST be validated on the camera (a wrong preview
geometry can freeze the display). Cannot be verified offline.

## Honest status
Green is fully localized and a hook approach is identified, but implementing a
correct, freeze-safe display hook requires the camera (read lv-geom object layout
live in open gate, place the hook, observe). It is the hardest item and remains a
hardware-iteration task. Cosmetic (recorded files are unaffected).

## HARDWARE NEGATIVE RESULT (2026-09-11)
Poking the geometry-manager live-view blocks 0xC375E190 / 0xC375E308 (the 1620x911
"lv render" pairs reached via manager 0xC375D840 +0xc) to dramatic values (800x800,
held across recompute cycles) produced **NO visible change** on the LCD in open gate.
=> These RAM blocks are NOT the visible display/green source. The manager+0xc object
(0xC0428B18 accessor 0xC04376E0) is therefore not the effective target either.
The visible green is driven further downstream — likely the ISP/YUV display-scaler
config (YuvResize task ~0xC03CB090) or MMIO display-controller registers, not a plain
RAM geometry struct. NEXT: trace YuvResize + the display-controller (XC_DisplayLcd
0xC0B54FFC / XC_DisplayHdmi 0xC0729B48) register writes, offline (no camera needed).
Do NOT re-test the 0xC375Exxx geometry blocks — confirmed inert for the display.

## SOLVED TARGET — from FP3K 0.3.5 disassembly (2026-09-11)
Reversing the FP3K "preview display-fit" module (\FP3KMENU.BIN, a loaded code
blob at 0xC072DE64) resolves the effective display-geometry target. FP3K touches
NO picker / VMAX / RWZM / record-geometry (it is display-only; it does NOT hit
open gate), yet it cleanly reframes the preview to 3:2. Its one geometry hook:
- **Hook point: accessor `0xC0437E98`** (stock first instr `E92D4030`; trampoline
  continues at `0xC0437E9C`). This is a DIFFERENT accessor than the manager+0xc
  `0xC04376E0` we tried — that is why our earlier attempt was inert.
- **Effective live-view objects: `0xC375EB68` and `0xC375ED3C`** (a third,
  `0xC375E934`, is a sub-object it validates). These — NOT `0xC375E190/E308` —
  are the objects the visible display path reads.
- **Mechanism (accessor-return substitution, gated):** when ARMED and the passed
  object == `0xC375EB68`/`0xC375ED3C` and a 44-byte fingerprint of it matches,
  FP3K returns a MODULE-OWNED descriptor instead of the firmware object, then
  replays the displaced code. The descriptor it forces is `{+00 size_h=0x654=1620,
  +04 size_v=0x438=1080, +10/+14 duplicate pair, +1c=4, +20=0x003FC000,
  +24=1, +28=1}` = **1620x1080 (3:2) preview geometry**.
=> The green fix is a CODE hook on `0xC0437E98` that, gated to open gate, returns
a 3:2 descriptor for objects `0xC375EB68/ED3C`. A RAM poke cannot hold it
(recomputed per frame — consistent with our negative result); the accessor-return
hook does. 1620x1080 is the correct 3:2 preview raster for a 3032x2012 sensor
frame.

## IMPROVEMENT — true open gate WITHOUT green (combine the two halves)
FP3K = display half only (no sensor change → no green, but also not open gate).
Our M117 path = sensor half only (real 3032x2012 readout, but greens). Combine:
1. SENSOR (ours): picker slot -> M117 3032x2012 + VMAX (build_highfps-style).
2. DISPLAY (FP3K's, ported): code hook on `0xC0437E98`, gated to open gate/ARMED,
   return a 3:2 descriptor for `0xC375EB68/ED3C` (1620x1080 preview).
3. BRIGHTNESS: compensate the RWZM-unity ~-0.65 stop (FIRMWARE-DECODE +0xfc/+0x100
   per-profile float), gated the same way.
All three must be CODE (loaded blob), not pokes. Delivery = the compiled-blob
loader (FP3K proves the pattern: stub@0xC0732100 loads \*.BIN into 0xC072DE64,
sum-zero checksum, installs trampoline hooks). Hardware bring-up still required to
confirm the `0xC375EB68/ED3C` field offsets on THIS unit before arming.
