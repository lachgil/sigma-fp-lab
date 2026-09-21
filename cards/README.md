# AutoRuns — ready-to-test cards

Prebuilt AutoRuns for the **SIGMA fp, firmware Ver.5.02 ONLY**. These are the
generated files themselves so you can just download and run them (the `build_*.py`
scripts in the repo root regenerate them from your own firmware).

## How to use

1. **Clear any custom function mapped to UP and to RIGHT** (Menu -> Custom ->
   button settings). The menu card takes both keys for itself while it is loaded.
1. Confirm your camera is **fp Ver.5.02** (Menu → firmware). Do **not** use these
   on any other version or the fp L.
2. Format an SD card in the camera. Copy the chosen file to the **card root** and
   rename it to exactly **`AutoRun.txt`**. Keep only one on the card.
3. **Cold boot** with the card in: power the camera on. A banner shows it loaded.
4. Select the matching preset (see each row), then switch the mode **away and back
   once** to re-latch.
5. To go back to stock: **remove `AutoRun.txt` and cold-boot with the battery out
   for ~10s**. Everything here is RAM-only and nothing is written to flash.

## Files

| File | What it does | Select on camera | Notes |
|---|---|---|---|
| `fp-menu-card.zip` | **The menu card.** Stock and the three modes that have recorded on a camera: **M130** (24p when the camera is on 23.976), **Open Gate** and **FHD 120** (1920x1080 12-bit at 120 fps from a 3032x1708 readout, a couple of seconds before the recorder overflows). Extract both files to the card root. | On camera, then re-latch the mode | **Start here.** No USB shell, no diagnostics. Open Gate greens the standby preview here: the fix for it is untested on hardware, so it is a dev row. |
| `fp-menu-card-dev.zip` | The same, **plus the USB shell and every other row**: Green Fix, M130 FAST, M98 60P, 2K120, 672 240, False Col, Gyro, Gyro-Gate, M98 30P, M6 4K, 2088 120 and the `SEL` readout. Those are experiments and dead ends kept for their evidence. | As above | For development, and the card to use for Open Gate with a usable monitor. The shell replaces the camera's normal PTP interface, so SIGMA Camera Control will not see the camera. |
| `AutoRun-fc-latch.txt` | **False Color / EL Zone as a toggle**, on a button you already mapped. One file, nothing else in it: no menu, no USB shell, no recording changes. Rename to `AutoRun.txt` at the card root. | **Menu → Custom → button settings → False Color** first, then press that button | **Works, confirmed on a camera.** Press once and it stays on; press again for off. Whether you get False Color or EL Zone is your own **False Color Style** setting. Playback/menu/record transitions while it is latched are not tested yet. |
| `AutoRun-fc-scale.txt` | The toggle above **plus the camera's own EL Zone scale**, worked by how long you hold the button: **press** for the mode, **hold ½ s** for the mode with the scale, **hold again** for the scale off and on, **press** for off from either state. Bands, colours and label glyphs are the firmware's own tables (`0xC0D1B74C`, palette `0xC0D1B34C`), redrawn at 0.85 size with 84 rows cropped off the bar so it sits along the bottom of a 16:9 or wider picture (30-row bar, 870 px wide, sat on the bottom edge at row 681). `tools/verify_fcscale.py` renders it to `builds/fcscale/scale.png`; `--scale` and `--crop-bottom` on the builder retune it. | As above | **The scale itself is confirmed on a camera** (2026-09-20, after a battery pull). The hold gesture and the smaller geometry are **new and not yet on a camera**; 28 offline cases pass. Drawn on the 16-bit main OSD layer inside the display submit (`0xC02E8A08` hook), once per frame; a mode or scale change paints the front buffer directly. Also as `fp-fc-scale-card.zip`. **Pull the battery when changing AutoRun builds**: a soft power cycle keeps the previous build resident. |
| `AutoRun-opengate.txt` | Open gate: full-sensor **3032×2012 (3:2)** CinemaDNG. Record hook is gated (selector `r5==175`) so other modes are untouched. Includes the fpSup USB shell. | **CinemaDNG → FHD → 29.97p** | Preview greens while recording (cosmetic, see below); files are correct. Submitted upstream as [ijigen/fpSup#2](https://github.com/ijigen/fpSup/pull/2). |
| `AutoRun-opengate-greenfix.txt` | Same open gate **plus** the FP3K-style display hook that reframes the preview to 3:2. Boot-and-shoot (no shell). | **CinemaDNG → FHD → 29.97p** | Green fix is **standby/live-view** and only engages if this unit's display object matches the fingerprint (safe no-op otherwise). The record-time monitor may still band; recorded files are fine. Record to a **fast SSD**. |
| `AutoRun-fhd120.txt` | High-fps experiment: repoints the FHD slot to **M58 (3032×1708 @≈120)** CinemaDNG RAW. | **CinemaDNG → FHD** (the repointed slot) | **EXPERIMENTAL.** ~389 MB/s → needs a **fast USB-C SSD**; on slower media it auto-stops after a couple of seconds. Recorded 120fps cadence is not yet independently confirmed. |
| `AutoRun-2k120.txt` | High-fps experiment: repoints the 2K slot to **M103 (2088×1174 @≈120)** CinemaDNG RAW. | **CinemaDNG → 2K** (the repointed slot) | **EXPERIMENTAL.** ~441 MB/s → **fast SSD required.** Same cadence caveat as above. |

## Status and cautions

- **Open gate works and files are correct.** The green tint is a record-time
  **monitor** artifact, not in your footage. The standby green fix
  (`opengate-greenfix`) is verified in firmware but needs a per-camera fingerprint
  match to engage; see [the green preview notes](../docs/display/green.md).
- **If open gate is what you want, look upstream first.** Our open gate is the
  original FHD-slot M117 swap. ijigen/fpSup has since released **OG3K v0.2.2a**
  (tag `fpsup-og3k-v0.2.2a`, commit `a029b6a4`), which is a different and more
  complete implementation: eight frame rates, 3024x2010 recording geometry,
  in-camera playback, corrected native ISO/highlight headroom, shutter-angle
  correction and 8/10/12-bit. Its latest core sources are not published, so
  nothing here is a port of it, and none of its work is claimed here.
- **High-fps is experimental.** M58/M103 are 2×2-binned (softer) sensor modes.
  Use a fast SSD, keep clips short, and verify your own files.
- These are RAM-only and reversible, but RAM changes can still spoil a take.
  Do not run them unattended and do not record anything you cannot afford to lose
  while testing. No flashing is involved.
- fp Ver.5.02 only. There is no runtime firmware-version guard in the file; using
  the wrong version is on you.

See [docs/status.md](../docs/status.md) and [docs/modes/map.md](../docs/modes/map.md)
for the full picture and the current mode-unlock work.
