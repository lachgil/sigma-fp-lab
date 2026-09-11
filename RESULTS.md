# What has been tested on the camera

SIGMA fp, firmware Ver.5.02, lgeeh's body. Everything RAM-loaded from an
`AutoRun.txt` + `VSHL.BIN` on the SD card; nothing is ever flashed, and a
battery pull returns the camera to stock.

**Measured** means it was observed on this camera. **Offline** means it passes
ARM emulation of the shipped binary but has not been on hardware. Where a claim
came from one clip or one reading, it says so.

## Recording modes

| option | mode | raster | readout | rate | status |
|---|---|---|---|---|---|
| Open Gate | M117 | 3032×2012 | 2×2 binned | 274 MB/s | **works** (measured, earlier sessions) |
| **M130** | M130 | **3968×2640** | **FULL 1:1** | 377 MB/s @23.976 | **works, sustained** — 2 min, correct full frames |
| M130 @29.97 | M130 | 3968×2640 | FULL 1:1 | 471 MB/s | **records, stops ~5 s** — buffer-limited, not a bug |
| M130 FAST | M130 | 3968×2640 | FULL 1:1, line period 330 | 377–471 MB/s | offline only; RS 16.3 → 12.1 ms, untested |
| M98 30P | M98 | 3032×2012 | 2×2 binned | 274 MB/s | offline only — comparison against M117, worse RS |
| M98 60P | M98 | 3032×2012 | 2×2 binned | 548 MB/s | offline only; expect short bursts |
| M6 4K | M6 | 4176×2174 | FULL 1:1, 14-bit | 476 MB/s | **measured: no visible gain** — file depth follows the menu's 8/10/12 setting, so the extra bits are quantised away |
| ~~M10 WIDE~~ | M10 | 6064×2022 | FULL 1:1 | 441 MB/s | **REFUSED by the camera** — see below |
| High FPS | M58 | 3032×1708 @119.88 | 2×2 binned | 931 MB/s | **removed** — M58 has its own picker cells, so FHD 119.88 is already a stock preset |
| Gyro | — | — | — | — | **works** — `.gcsv` + lens `.json` beside CinemaDNG clips |

### The headline
**FHD 23.976 CinemaDNG 12-bit + `M130` = sustained 3968×2640 full 1:1 readout**,
377 MB/s to a USB-C SSD. 10.5 MP with no binning at all, 3:2.

Against stock: FHD is 2×2 binned (soft), stock 4K is a 1.45× crop at 1.92:1,
stock UHD is full-width but 21 ms of rolling shutter. M130 is a 1.53× crop with
16.3 ms. It is a different tool from open gate, not a replacement: **open gate
is the full sensor AREA at half resolution; M130 is real detail on a tighter
frame.**

## What blocks the rest

| limit | measured value | consequence |
|---|---|---|
| Storage throughput | ~377 MB/s sustained, ~471 MB/s dies near 5 s | the binding constraint on every full-readout mode |
| Record path raster | 3968 wide accepted, **6064 refused** | M10/M3/M7/M97 cannot be substituted into an FHD preset at all |
| File bit depth | follows the menu's 8/10/12 | 14-bit sensor modes (M6 etc.) give no visible gain |
| Slowest CinemaDNG preset | 23.976 | full-sensor 6064×4042 needs 882 MB/s minimum — out of reach |
| Live view while recording | green / flickering in every geometry-changed mode | recorded files are unaffected; gyro (no geometry change) is clean |

## The mechanisms, and how each was established

| thing | address / value | how |
|---|---|---|
| **raw_zoom scaler** | unity 1024, stock 0x640 = 1600 (1.5625×) | `imager mode_now` over the shell |
| its per-profile arrays | `0xC0BD9A34`, `0xC0BD9EFC`, `0xC0BE1684`, `0xC0BE1B4C` = profile 122 | profile = selector − 53, stride 4; the array runs begin exactly at cell − 122×4 |
| picker cells | scan `0xC0BE5700..0xC0BE5D00` | a mode id appears in 4–5 cells; hardcoding missed them |
| record geometry hook | site `0xC043A19C`, canvas per selector slot | gated on the 1936×1090 source row **and** an enabled selector |
| timing table | `0xC0B59500 + n*0x20`: id at +0, HMAX low16 at +4, VMAX low16 at +8 | verified against all 70 modes, 0 mismatches |
| fps / rolling shutter | `fps = 72MHz/(HMAX·VMAX)`, `RS = HMAX·lines/72MHz` | max error 0.15 fps across 70 modes |
| FieldAngle selectors | 175 = FHD 29.97 12-bit, 173 = 59.94, 176 = 23.976, 180 = 25, 156 = another FHD config | shell, then the in-payload probe; **bit depth moves the selector** |
| key handler | descriptor `0xC091EA38`, stock `0xC0265800`, not cached | live pointer swap |
| button ids | UP 0x14, DOWN 0x18, LEFT 0x10, RIGHT 0x0C, OK 0x1C, TONE 0x2F (release = press+1) | logged live; only RIGHT and UP have no native action |
| OSD text draw | sub-handler `0xC03E4620`, composite `0xC03E3D00` | resident code draws with a fake ctx; the dispatcher `0xC03E5510` fails from resident context |
| OSD is multi-buffered | repaint 4× per action | single draws took ~3 presses to appear |
| `0xC032C720` | returns 8 at every framerate | **not** the live sensor mode id |

## Failure signatures, so the next one is read correctly

| what the clip looks like | what it means |
|---|---|
| clean, ordinary 1920×1080 | the camera **validated the substitute and fell back** (M10) |
| correct-size frame, shrunken picture in a corner | canvas applied, **raw_zoom still downscaling** — filled fraction 0.64 on **both** axes |
| torn / garbage rows | the sensor cannot deliver that raster at that rate |
| records then stops after a few seconds | throughput; nothing to do with geometry |

The both-axes detail matters: a stride or bit-depth artefact can only affect
width, so a proportional shrink is always the scaler.

## What the camera calls its own modes

`imager mode_list` on a live camera names all 70. There are only two families,
`ACQ` (stills acquisition) and `MONIT1` (monitor) — **there is no movie family,
because video records on MONITOR modes.** So:

| our label | real name |
|---|---|
| open gate | `MONIT1_100` |
| **M130** | **`ACQ_12BIT_CROP`** — a stills mode used for video |
| M98 | `ACQ_12BIT_MIX` |
| M10 (refused) | `MONIT1_60_LOWP2` |
| stock UHD | `MONIT1_30_HD_LOWP` |
| stock 4K 14-bit | `MONIT1_30_HD` |

And the re-latch we do by hand is a **live-view stop/restart**: changing a
setting through the UI tears live view down and brings it back, and the picker
is re-read on the way up. Captured from the RECMGR log with our probe counter
as witness (3 -> 4). A property write moves the master block without any of
that, which is why it never re-latched.

## Dead ends, recorded so they are not retried

- **High FPS (M58)** duplicated a stock preset and mis-timed the 60p one.
- **M6 14-bit** gains nothing while the recorder quantises to the menu depth.
- **M10 / any 6064-wide mode** is refused by the FHD record path. The way in is
  the UHD family, which already handles that width — it needs the hook to gate
  on the UHD source row too.
- **`0xC032C720` as "current mode"** — returns a monitor enum, not the mode id.
- **Keeping picker cells against a re-latch** — `L=04` after a re-latch proved
  nothing overwrites them, so the mechanism was unnecessary.

## Offline verification that backs the shipped card

`build_combined_card.py` refuses to build unless the verified gyro sections
match byte-for-byte and 29 stock firmware words are intact. `emulate_hook.py`
(14 cases) and `emulate_menu.py` (11 groups) execute the **packaged machine
code** under Unicorn against the real firmware image. They caught, before any
card was written: an UP press on an already-on option cancelling itself, a
canvas release zeroing one word of four, a repoint matching every framerate at
once, an uninitialised counter walking wild memory on a refusal path, and a
readout buffer colliding with state.

Cold-boot display, real recordings, and RTOS timing are not simulated and never
will be by these — they are what the camera is for.
