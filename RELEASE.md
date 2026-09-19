# sigma-fp-lab — test release

**SIGMA fp, firmware Ver.5.02 only. Not the fp L.**

Two cards. Both load from the SD card at boot and live in RAM: nothing is
flashed, and pulling the battery puts the camera back exactly as it was.

| Download | Who it is for |
|---|---|
| `fp-menu-card.zip` | **Everyone.** The on-camera menu. |
| `fp-menu-card-dev.zip` | Development: same menu plus the USB shell and the `SEL` diagnostics. |

Extract the zip to the card root, so the card holds `AutoRun.txt` and
`VSHL.BIN`. Cold boot with the card in. **Both files are required and they are a
matched pair** (the code lives in `VSHL.BIN`), so never mix files between zips.

## Before you use it

**Unmap UP and RIGHT.** In the camera: **Menu → Custom → button settings, and
make sure nothing is assigned to UP or to RIGHT.** The card uses both keys while
you are in live view, so anything mapped there will not fire.

In the camera's own menu and in playback the keys stay the camera's, so you can
navigate normally.

## Using it

RIGHT moves down the list, UP switches the highlighted option on. Everything
boots OFF and **Stock** clears it all. The panel is drawn top right, hides after
about four seconds, and comes back on the next key.

**Release card:** Stock, M130, Open Gate, FHD 120. Those are the three modes
that have recorded on a camera, and nothing else.

| Option | What you get |
|---|---|
| M130 | 3968×2640, full 1:1 readout, RAW12, follows the preset you select |
| Open Gate | Full-sensor 3032×2012 (3:2) CinemaDNG instead of the 16:9 crop |
| FHD 120 | 1920×1080 12-bit at 120 fps; a burst, not a take |

**Dev card adds:** Green Fix (3:2 standby preview, never run on a camera, which
is why it is not on the release card), M130 Fast, M98 30P / M98 60P, M6 4K,
2K120, 672 240, 2088 120, FALSE COL, Gyro, Gyro-Gate and the `SEL` readout.
Open Gate greens the standby preview on the release card; use the dev card with
Green Fix on if that is in your way.

Turning an option on also selects the framerate it needs, which is what makes
the camera adopt it. You should not have to switch the preset by hand.

## Credits

This is a menu and a loader around work other people did first. The recording
modes are not our discovery:

- **[ijigen/fpSup](https://github.com/ijigen/fpSup)** — worked out how to run
  code on this camera at all, the AutoRun loader, the USB shell, the gyro
  metadata build, and the sensor mode table these options are selected from.
  Open gate was submitted back as [fpSup#2](https://github.com/ijigen/fpSup/pull/2).
  ijigen has since released **OG3K v0.2.2a** (tag `fpsup-og3k-v0.2.2a`), a far
  more complete open gate than the one here: eight frame rates, 3024×2010
  recording geometry, in-camera playback, native ISO/highlight headroom,
  shutter-angle correction and 8/10/12-bit. No part of it is included here.
- **Vitaly Li / FP3K** — got open gate out of an fp first (3000×2000 12-bit
  CinemaDNG), and the display accessor (`0xC0437E98`) our standby green fix is
  built on. FP3K also demonstrates a native resolution entry, with its own
  `.xci` artwork.
- **ijigen's research handoff** — the GUI resource system: the XCI icon format,
  the enum→state table at `0xC2E4020C`, and the measurement that value lists are
  built once at startup. See `docs/menu/gui-resources.md`.
- **[magiclantern](https://github.com/reticulatedpines/magiclantern_simplified)**
  — reference for how this kind of thing is done, no code used.

No SIGMA firmware is included; you supply your own copy.

## What is proven, and what is not

Confirmed on hardware: open gate records and the files are correct; the menu,
`Stock`, the busy guard; FALSE COL; the auto re-latch; the key gate.

**Not confirmed, and where help is wanted:**

- **120fps and 240fps cadence.** `2K120` and `672 240` select the faster sensor
  readout for a cell that keeps its resolution, so they need a fast USB-C SSD
  and nobody has confirmed the files really run at the rate the sensor is
  clocked to. Shoot a clip and read the DNG `FrameRate` tag and the frame count.
  This is the single most useful thing anyone can report back.

  Only swaps whose target mode is **not already in the camera's picker table**
  are offered. 3032×1708@120 (mode 58) and 2088×1174@120 (mode 103) are not in
  the menu for that reason: switching them off would rewrite the cells that were
  genuinely 58 or 103. They remain available as the standalone `fhd120` and
  `2k120` cards.
- **M130 / M6 / M98 in real use.** They record, but rolling shutter, banding
  and highlight behaviour have had almost no real footage through them.
- **Other bodies.** Everything was worked out on one camera.

## Known limits

- **Focus magnification and false colour cannot be had during a take.**
  Measured, not guessed: magnification is a distinct sensor readout mode
  (selector 0x11–0x28, e.g. 6064×2022) and recording runs a different one.
- Open gate's preview greens while recording. The files are fine.
- No firmware-version guard in the card. Using it on anything but Ver.5.02 is
  on you.
- The dev card's USB shell replaces the normal PTP interface, so SIGMA Camera
  Control will not see the camera while it is loaded.

## Safety

RAM-only and reversible, but a RAM change can still spoil a take. Do not use it
on a paid job, do not record anything you cannot afford to lose, and to go back
to stock remove `AutoRun.txt` and cold boot with the battery out ~10 s.
