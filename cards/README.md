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
| `fp-menu-card.zip` | **The menu card.** Every extra recording mode in one on-camera menu, plus **FALSE COL**, which latches false colour instead of making you hold a button. Extract both files to the card root. | On camera, then re-latch the mode | **Start here.** No USB shell, no diagnostics. |
| `fp-menu-card-dev.zip` | The same menu **plus the fpSup USB shell** and the `SEL` row's cell counts, for working on the patches over USB. | As above | For development. The shell replaces the camera's normal PTP interface, so SIGMA Camera Control will not see the camera. |
| `fp-fplab-row-card.zip` | **Untested dev card.** The menu card with the USB shell, plus a genuinely new fifth row injected into the camera's own **Record Settings** page. Extract both files to the card root. | Nothing to select: open **Menu → Record Settings** and look below the last row | **Has never been on a camera.** The row is a copy of the last stock row, so it reads the same label; it has no focus highlight and Right/OK do nothing on purpose. What it is testing is whether a new row appears at all. If the menu misbehaves, remove the files and pull the battery. |
| `AutoRun-opengate.txt` | Open gate: full-sensor **3032×2012 (3:2)** CinemaDNG. Record hook is gated (selector `r5==175`) so other modes are untouched. Includes the fpSup USB shell. | **CinemaDNG → FHD → 29.97p** | Preview greens while recording (cosmetic, see below); files are correct. Submitted upstream as [ijigen/fpSup#2](https://github.com/ijigen/fpSup/pull/2). |
| `AutoRun-opengate-greenfix.txt` | Same open gate **plus** the FP3K-style display hook that reframes the preview to 3:2. Boot-and-shoot (no shell). | **CinemaDNG → FHD → 29.97p** | Green fix is **standby/live-view** and only engages if this unit's display object matches the fingerprint (safe no-op otherwise). The record-time monitor may still band; recorded files are fine. Record to a **fast SSD**. |
| `AutoRun-fhd120.txt` | High-fps experiment: repoints the FHD slot to **M58 (3032×1708 @≈120)** CinemaDNG RAW. | **CinemaDNG → FHD** (the repointed slot) | **EXPERIMENTAL.** ~389 MB/s → needs a **fast USB-C SSD**; on slower media it auto-stops after a couple of seconds. Recorded 120fps cadence is not yet independently confirmed. |
| `AutoRun-2k120.txt` | High-fps experiment: repoints the 2K slot to **M103 (2088×1174 @≈120)** CinemaDNG RAW. | **CinemaDNG → 2K** (the repointed slot) | **EXPERIMENTAL.** ~441 MB/s → **fast SSD required.** Same cadence caveat as above. |

## Status and cautions

- **Open gate works and files are correct.** The green tint is a record-time
  **monitor** artifact, not in your footage. The standby green fix
  (`opengate-greenfix`) is verified in firmware but needs a per-camera fingerprint
  match to engage; see [the green preview notes](../docs/display/green.md).
- **High-fps is experimental.** M58/M103 are 2×2-binned (softer) sensor modes.
  Use a fast SSD, keep clips short, and verify your own files.
- These are RAM-only and reversible, but RAM changes can still spoil a take.
  Do not run them unattended and do not record anything you cannot afford to lose
  while testing. No flashing is involved.
- fp Ver.5.02 only. There is no runtime firmware-version guard in the file; using
  the wrong version is on you.

See [docs/status.md](../docs/status.md) and [docs/modes/map.md](../docs/modes/map.md)
for the full picture and the current mode-unlock work.
