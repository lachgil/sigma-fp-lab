# Sensor modes

The IMX410 in the fp has 70 readout modes. The menu only names a handful. Most
of the work here is finding which of the rest can be made to record, and at what
cost. Every number below was measured, on the camera or from the firmware's own
tables, and the failures are kept alongside the successes.

Full per-mode table: [`map.md`](map.md). Tested results and failure signatures:
[`results.md`](results.md). Dated raw notes: [`notes-modes.txt`](notes-modes.txt).

## What is worth using

| Option | Sensor mode | Field of view | Readout | Recorded | Rolling shutter | Rate |
|---|---|---|---|---|---|---|
| Open Gate | M117 | **100% x 100%** | 2x2 binned | 3032x2012 | 9.22 ms | 274 MB/s |
| M98 30P | M98 | 100% x 100% | 2x2 binned | 3032x2012 | 12.44 ms | 274 MB/s |
| M130 30P | M130 | 65% x 65% (1.53x crop) | **full 1:1** | 3968x2640 | 16.32 ms | 471 MB/s |
| M130 FAST | M130 | 65% x 65% | full 1:1 | 3968x2640 | **12.10 ms** | 471 MB/s |
| **M130 24P** | M130 | 65% x 65% | full 1:1 | 3968x2640 | 16.32 ms | **377 MB/s** |
| M98 60P | M98 | 100% x 100% | 2x2 binned | 3032x2012 | 12.44 ms | 548 MB/s |
| M6 4K | M6 | 69% x 54% | full 1:1, 14-bit | 4176x2174 | 27.51 ms | 476 MB/s |

For comparison, stock: FHD (M106) is the full field 2x2 at 10.56 ms, 4K (M102)
is a 1.45x crop at 1:1 and 13.44 ms, UHD (M7) is full width 1:1 at 21.09 ms.

**Open gate is the whole sensor at half resolution.** 3032x2012 is exactly half
of the 6064x4042 full readout on each axis, which is what 2x2 binning means.
M130 is the opposite trade: real 1:1 detail from the middle 65%, so it frames
like a 1.53x crop. Full area *and* full resolution is M3 (6064x4042, 24.5 MP),
which would need detuning to roughly 12-15 fps to fit any storage here.

## M130 24P, and why the others stop

M130 30P is confirmed recording on hardware: a sustained run of 3968x2640
12-bit frames to SSD, with `DefaultCropOrigin (0,0)`, `DefaultCropSize
(3968,2640)` and `ActiveArea (0,0,2640,3968)`. It then **stops after about five
seconds**: at 471 MB/s the recorder runs out of buffer.

M130 FAST does not help with that, and was never meant to: HMAX changes rolling
shutter, not data rate.

**M130 24P is the fix that addresses the actual limit.** The same full-readout
canvas in the FHD 23.976 cell (selector 176, measured), timing entry 4116 ->
6748, **377 MB/s**. Worth trying on any of them as well: 10-bit takes 29.97 down
to 392 MB/s and 8-bit to 314, because the file depth follows the menu setting.

## How the timing works

Frame rate is `72 MHz / (HMAX * VMAX)` and rolling shutter is
`HMAX * lines / 72 MHz`, checked against all 70 modes with a worst error of
0.15 fps. The table is at `0xC0B59500 + n*0x20`: mode id at +0, HMAX low half at
+4, VMAX low half at +8. Only the low half is written; the high half differs per
mode (4 on binned entries, 1056 on M130, 952 on M6) and is preserved.

Detuning VMAX is what makes an otherwise throughput-impossible mode recordable.

## Things that cost real time to learn

- **The `raw_zoom` scaler, solved on hardware.** `imager mode_now` reports a
  raw_zoom stage: unity is 1024, and the FHD profiles ship 0x640 = 1600, a
  1.5625x downscale. Open gate only ever set unity for the FHD 29.97 profile, so
  29.97 was the only rate that recorded a whole frame. Every other rate produced
  a correctly sized buffer holding a shrunken picture in the corner. The arrays
  are indexed by profile, 4-byte stride, profile = selector - 53. Now computed
  per option from the probed selector, so every framerate works.
- **The record path will not accept an arbitrary raster.** M10 (6064x2022, full
  sensor width) was rewritten correctly and *refused*: the clip came back a
  clean, ordinary 1920x1080. That is the path validating the substitute and
  falling back, which looks nothing like a corner-boxed frame (canvas applied,
  scaler wrong) or a torn one (sensor cannot read it). 3968 wide is accepted,
  6064 is not. A 6064-wide mode would have to go into the UHD family instead.
- **A mode swap has to rewrite every cell naming that mode.** Hardcoding
  addresses failed twice: an id appears both in the `0xC0BE5700` records and in
  three parallel tables above them, and which one a given framerate reads is not
  uniform. Open gate needs three of M106's four cells; FHD 23.976 has **five**
  M109 cells. The failure signature is specific: canvas applies, mode does not,
  and the clip is the stock raster in the corner of an oversized frame. `SEL`
  reports the count, so a swap that found nothing is visible.
- **Selectors are per framerate and per bit depth.** Measured: 175 = FHD 29.97
  12-bit, 173 = 59.94, 176 = 23.976, 180 = 25, and 156 = another FHD
  configuration. An unmeasured one is refused rather than guessed.
- **M6 4K looked identical to stock 4K** on test: CinemaDNG depth follows the
  menu's 8/10/12 setting, so M6's extra two bits are quantised away. Kept
  because it is a three-word swap and the only route to 14-bit if that recorder
  limit is ever lifted.
- **Binning: lead tested and closed.** Two clips with the flag off and on are
  identical to 0.9% noise and 0.5% detail, so nothing consults
  `SetMovBiningSupport` when the mode is chosen.
- **High-fps RAW** (M58, FHD 119.88): selection observed, but the recorded
  cadence and sustained throughput are still unverified. A short auto-stop does
  not prove overflow. M58 is already a stock preset, so it is not in the menu.

## Tools

| Script | What it is for |
|---|---|
| `tools/build_modeswap_autorun.py <slot> <mode>` | Repoint any picker slot to any of the 70 modes |
| `tools/build_highfps_autorun.py` | Picker-only high-framerate experiments |
| `tools/build_gated_autorun.py` | The selector-gated record hook on its own |
| `tools/oversample.py` | Compares readout options feeding a given output |
