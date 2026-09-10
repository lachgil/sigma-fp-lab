# Open-Gate Darkness + CinemaDNG Playback Geometry — decode & fix design

SIGMA fp Ver.5.02, MAIN base 0xC0000000. `CONFIRMED` = string+xref+disasm grounded.
`INFERRED` = mechanism reasoning. Camera offline this session — no on-hardware numbers.
All addresses verified against `analysis/MAIN_c0000000.bin`.

Scope note: MAIN dump ends at 0xC2F30CCA. The CinemaDngPlay/TagReader *library*
overlay (`src/library/CinemaDngPlay/...`, code ~0xC3xxxxxx) is **beyond the dump**
(e.g. its develop entry pointer 0xC3032D3A is out of range), so the tag-reader /
develop-geometry chooser itself cannot be disassembled here. What is confirmable is
the record-side builder, the profile tables, the playback dispatch/vtable that lives
in MAIN, and the negative result that the static frame-size table is dead in MAIN.

================================================================================

## PART A — DARKNESS

### A1. Builder 0xC0439D18 — how profile fields reach the record object (CONFIRMED)

`0xC0439D18` is a flat field-copy: for each record-object offset it does
`movw/movt r6,<table_base>; ldr r3,[r6, r4, lsl #2]; str r3,[ip,#<off>]`, where
`r4` = **profile index** and `ip` = record object. So every field is `table_base[profile]`.
Selector→profile map is the preceding stage `0xC043A158`:
`movw/movt ip,#0xC0BD05D4; ldr r1,[ip, r5, lsl #2]` — selector `r5` indexes 0xC0BD05D4.

Confirmed selector→profile (0xC0BD05D4[sel]):

| selector | profile | note |
|---|---|---|
| 117 | 65 | stock FHD-ish |
| 122 | 70 | stock |
| **175** | **122** | **open gate (the r5==175 hook)** |

**Only selector 175 maps to profile 122** (scanned 0..255). So every profile-122
table cell is *exclusively* the open-gate mode → editing them is **inherently gated,
no runtime r5 check needed.**

The two float stores at the tail of the builder (CONFIRMED disasm):
```
0xC043A118  movw r6,#0x852c; movt r6,#0xC0BD ; ldr r3,[r6,r4,lsl#2]; str r3,[ip,#0xfc]
0xC043A128  movw r6,#0x89f4; movt r6,#0xC0BD ; ldr r3,[r6,r4,lsl#2]; str r3,[ip,#0x100]
```
→ record-object **+0xfc = table 0xC0BD852C[profile]**, **+0x100 = table 0xC0BD89F4[profile]**.

### A2. The +0xfc/+0x100 float is a per-profile DIGITAL GAIN (CONFIRMED value pattern)

Reading `0xC0BD852C[profile]` / `0xC0BD89F4[profile]` (both tables always hold the
same value = an H/V pair) against readout width:

| profile | float | srcW | meaning |
|---|---|---|---|
| 65 | 1.0 | 6064 | full-width readout, no decimation |
| 106 | 1.0 | 6064 | full-width |
| 55 | 2.0 | 3032 | 2× horizontal skip (3032 = 6064/2) |
| 117 | 2.0 | 2088 | 2× |
| **122** | **2.0** | **3032** | **open-gate base (2× skip)** |
| 0/3 | 3.0 | 2016 | 3× |

The float tracks the sensor decimation factor **1.0 / 2.0 / 3.0** exactly → it is a
digital brightness multiplier that normalizes binned/line-skipped readouts back to
full-readout level. (Value pattern CONFIRMED. A direct `vldr [obj,#0xfc]` consumer
was not located in MAIN — the field is consumed after the record object is handed to
the ISP develop param path, likely in the un-dumped overlay — so "multiplies pixel
level" is INFERRED-strong from the decimation pattern; the A5 frame measurement is
the definitive check either way.)

### A3. Why open gate goes dark (mechanism)

Profile 122 is the **only** profile in the set whose RWZM cells are *not* unity —
stock they are 0x640 (Q10 = 1.5625) on all four:

| obj off | profile-122 cell | stock | open-gate patch |
|---|---|---|---|
| +0x60 rwzm1_h | **0xC0BD9A34** | 0x640 | 0x400 |
| +0x64 rwzm1_v | **0xC0BE1684** | 0x640 | 0x400 |
| +0xd0 rwzm2_h | **0xC0BD9EFC** | 0x640 | 0x400 |
| +0xd4 rwzm2_v | **0xC0BE1B4C** | 0x640 | 0x400 |

(These four are exactly `toggle_opengate.py`'s RWZM patch cells — confirmed identical.)

Stock profile 122 uses the ISP RWZM resampler to downscale 3032→1936 (H) and
1708→1090 (V), ratio 0x640/0x400 = **1.5625 per axis**. Open gate forces unity 0x400
to keep the full 3032 width for the 3032×2012 frame, i.e. it **removes the RWZM
resize entirely**. If that resampler is **non-normalizing (summing/accumulating)** —
as the RWZM coefficients being raw Q10 *ratios* fed to a HW block strongly suggests —
then the stock downscale contributed brightness that unity no longer provides:

- one axis lost: ×1/1.5625 = 0.64  → **−0.64 stop**
- both axes lost: ×1/2.4414 = 0.41 → **−1.29 stop**

The +0xfc digital gain (2.0) compensates only the *sensor* 2× skip; it does **not**
know about the RWZM downscale, so removing the RWZM removes uncompensated gain → dark.
(Resampler-is-summing is INFERRED: RWZM is a silicon block, not confirmable from ARM
disasm; but it is the *only* pixel-value change open gate makes, so it is the
mechanically necessary cause if darkness is real — which the field reports.)

### A4. DARKNESS FIX — exact cell + value (gated to profile 122)

Compensate the lost summing gain by raising profile-122's digital-gain float. Both
H and V cells must move together:

```
# add to toggle_opengate.py PATCHES  (addr: (stock, opengate, label))
0xC0BD8714: (0x40000000, 0x409C4000, "profile122 digital gain H (+0xfc) 2.0->4.883"),
0xC0BD8BDC: (0x40000000, 0x409C4000, "profile122 digital gain V (+0x100) 2.0->4.883"),
```

- **0xC0BD8714** = 0xC0BD852C + 122×4  (record-obj +0xfc source, profile 122)
- **0xC0BD8BDC** = 0xC0BD89F4 + 122×4  (record-obj +0x100 source, profile 122)
- stock word 0x40000000 = 2.0f
- **primary proposal 0x409C4000 = 4.883f** = 2.0 × 1.5625² (both-axis summing comp)
- **fallback 0x40480000 = 3.125f** = 2.0 × 1.5625 (one-axis, if only H sums)
- clean +1 stop reference 0x40800000 = 4.0f

Because only selector 175 → profile 122, these two writes affect open gate **only**;
stock recording is untouched. They slot straight into the existing RAM patch-set
(same `mem set` mechanism, no new hook).

### A5. CONFIRM METHOD (frame measurement — the required step)

Darkness magnitude/direction must be measured, not assumed:

1. Fix a scene: evenly-lit gray card, tripod, **fully manual** exposure (fixed
   ISO + shutter + aperture, AWB off / fixed WB, lens cap off).
2. Record clip **STOCK** (e.g. selector 122 / FHD) and clip **OPEN-GATE**
   (selector 175), identical settings, back-to-back.
3. On the computer, decode both CinemaDNGs to *linear* (dcraw `-D -4` or rawpy,
   no auto-bright), take the median of a central patch. Compute
   `k = median_stock / median_opengate`.
4. Set profile-122 float word = float32(`2.0 × k`) into 0xC0BD8714 and 0xC0BD8BDC.
5. Re-record open gate, confirm central-patch median matches stock within a few %.

Measurement must use the **recorded frames** (the live histogram/preview is unusable
— that path is the green-display bug). Iterate `k` once; it should converge in one step
since the compensation is a pure linear multiply.

================================================================================

## PART B — CINEMADNG PLAYBACK GEOMETRY (3032×2012 shown as 1936×1090)

### B1. Playback pipeline (CONFIRMED from class/source strings + MAIN vtable)

- Shell/playback entries **0xC0405050 / 0xC0405078 / 0xC04050A0** → all tail-call
  the poster/request router **0xC05BF2C0** (confirmed disasm: `bl 0xC05BF2C0` with
  r1 = 0/1/2). Router is pure event dispatch — carries no geometry.
- Develop sequence object: constructor **0xC03C8230** installs vtable **0xC0BA3CE0**;
  the class name `XC_PlayFuncSeqDngDeveloping` sits at 0xC0BA3CB8. The step/handler
  pointer table begins **0xC0BA3CE8** (0xC03C8230, 0xC03B0D18, 0xC03C82D8,
  0xC03B0D48 …), i.e. the develop-sequence steps live in MAIN at 0xC03Bxxxx/0xC03Cxxxx.
- Source strings confirm the roles of the (overlay) helper classes:
  `CinemaDngTagReader` (0xC2DFE598) parses the DNG IFD tags; `DngBufferInfo`
  (0xC2DFE75C) holds buffer geometry; `CinemaDngYuvCopier` (0xC2DFE6B8) / `YuvResize`
  (string 0xC0BA4298) do the scale/copy; `XC_PlayDngDeveloping.cpp` (0xC0BAAF2C).

### B2. The "only frame-size table" 0xC096F580 is DEAD in MAIN (CONFIRMED negative)

Table contents (4 × {W,H}):

| slot | W × H |
|---|---|
| 0 | 3856 × 2170 |
| 1 | **1936 × 1090** |
| 2 | 6064 × 4042 |
| 3 | 3968 × 2640 |

**No reference to 0xC096F580 exists anywhere in MAIN** — scanned literal pools *and*
movw/movt pairs in both ARM and Thumb: zero hits. The only literals near it are the
self-referential list nodes at 0xC096F5CC–0xC096F610. Likewise the geometry constants
1936(0x790)/1090(0x442)/3032(0xBD8)/2012(0x7DC) appear as **immediates zero times** in
MAIN. **Conclusion: the develop raster is fully data-driven; the "1936×1090" does not
come from a MAIN-readable static table.** Any fix that just edits/appends
0xC096F580 would be a no-op for the develop path — this corrects the earlier
"add a 3032×2012 entry to 0xC096F580" direction.

### B3. Why playback picks 1936×1090 (mechanism, INFERRED)

Each open-gate clip carries **two independent geometry descriptors**:

1. **DNG IFD tags** (ImageWidth/ImageLength) — the record geometry hook
   (0xC043A19C→cave) rewrote these to the true **3032×2012**, which is why the files
   **decode correctly on a computer** (desktop raw tools read the IFD).
2. **The clip's movie-mode / format-slot id** — the open-gate patch parasitizes the
   **FHD-29.97 picker slot**; it changed the *record geometry builder* (profile 122)
   but **not** the movie-mode id stamped into the clip's attributes, which still says
   *FHD-29.97 → format slot 1 → 1936×1090*.

On playback, `XC_PlayDngDeveloping` / `DngBufferInfo` size the develop + display
buffers from the clip's **movie-mode id** (→1936×1090), not from a re-read of the
DNG IFD tags. Result: 3032×2012 source data is developed into a 1936×1090 raster —
wrong aspect (1.507:1 squeezed into 1.777:1) and the same class of resize/gain
mismatch as the green-display bug. Same root family: a consumer that reads its own
mode-derived geometry instead of the actual open-gate frame.

### B4. PLAYBACK FIX — two concrete options

**Option 1 (robust, recommended) — teach the develop path to trust the IFD tags.**
`CinemaDngTagReader` already parses the correct ImageWidth=3032 / ImageLength=2012
into `DngBufferInfo`. Hook the point where the develop sequence *chooses* its geometry
so that, when the IFD width/height disagree with the mode-slot geometry, it uses the
IFD values. Gate condition that uniquely identifies an open-gate clip **without a
mode flag**: `dng_tag_width == 3032 && dng_tag_height == 2012` (or more generally
`tag_w != slot_w`). This is self-identifying — no per-clip marker needed and it
touches nothing about normal FHD/UHD clips.
- Reachable hook anchors in MAIN: the develop-sequence step table at **0xC0BA3CE8**
  (redirect the geometry-setup step to a cave that overrides W/H from DngBufferInfo),
  or the `DngBufferInfo` accessor the steps call. The exact step function and the
  `DngBufferInfo` width/height offsets must be pinned on the loaded overlay
  (`CinemaDngPlay` library, ~0xC3xxxxxx) which is **outside this MAIN dump** — capture
  it live with `memread.py`/`snapshot.py` over the address the constructor 0xC03C8230
  branches into, then place the cave the same way as the record geometry hook.

**Option 2 (data-only, narrower) — stamp the clip's movie-mode geometry.**
Give open-gate a real mode-id whose format-slot geometry equals 3032×2012, so the
mode-derived path is already correct. This needs (a) a spare/new movie-mode id used
only by open-gate recording, and (b) a matching geometry entry in whatever table the
playback path *actually* honors — which is **not** 0xC096F580 (B2), so that table
must first be identified live (it is reached from the overlay). Heavier: it also
changes the recorder side, and desktop compatibility already works, so Option 1 is
preferred.

**Gating:** both options key on the 3032×2012 signature so they fire only for
open-gate clips; all other CinemaDNG playback is untouched. Cosmetic only —
the recorded files are already correct off-camera.

================================================================================

## SUMMARY

- **Darkness**: profile-122 digital-gain float at **0xC0BD8714 (+0xfc)** and
  **0xC0BD8BDC (+0x100)**, stock 2.0f (0x40000000) → **4.883f (0x409C4000)** primary
  / 3.125f (0x40480000) fallback; inherently gated (profile 122 = selector 175 only);
  finalize the exact factor by the A5 gray-card stock-vs-opengate median measurement.
- **Playback**: geometry is data/tag-driven; static table 0xC096F580 is **dead in
  MAIN** (no xref) — the 1936×1090 comes from the clip's FHD movie-mode id, not the
  DNG's correct 3032×2012 IFD tags. Fix: hook the develop-sequence geometry choice
  (step table **0xC0BA3CE8**, ctor **0xC03C8230**; DngBufferInfo width/height on the
  live overlay) to prefer the IFD dims when `tag_w==3032 && tag_h==2012`. Exact
  overlay offsets require a live read (`CinemaDngPlay` code >0xC3 is beyond this dump).
