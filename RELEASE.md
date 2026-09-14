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

| Option | What you get |
|---|---|
| Open Gate | Full-sensor 3032×2012 (3:2) CinemaDNG instead of the 16:9 crop |
| M98 30P / M98 60P | Alternative readout at 30p / 60p |
| M130, M130 Fast | 3968×2640 cropped RAW12 |
| M6 4K | 4176×2174, 14-bit full readout |
| Gyro-Gate | Open gate together with the gyro metadata build |
| FALSE COL | False colour as a latch, instead of a button you hold |
| SEL | A readout, not a switch (dev card only) |

Turning an option on also selects the framerate it needs, which is what makes
the camera adopt it. You should not have to switch the preset by hand.

## Credits

This is a menu and a loader around work other people did first. The recording
modes are not our discovery:

- **[ijigen/fpSup](https://github.com/ijigen/fpSup)** — worked out how to run
  code on this camera at all, the AutoRun loader, the USB shell, the gyro
  metadata build, and the sensor mode table these options are selected from.
  Open gate was submitted back as [fpSup#2](https://github.com/ijigen/fpSup/pull/2).
- **FP3K** — the display accessor (`0xC0437E98`) the standby green fix is
  built on.
- **[magiclantern](https://github.com/reticulatedpines/magiclantern_simplified)**
  — reference for how this kind of thing is done, no code used.

No SIGMA firmware is included; you supply your own copy.

## What is proven, and what is not

Confirmed on hardware: open gate records and the files are correct; the menu,
`Stock`, the busy guard; FALSE COL; the auto re-latch; the key gate.

**Not confirmed, and where help is wanted:**

- **120fps cadence.** The high-fps cards hit ~389–441 MB/s and need a fast
  USB-C SSD. Nobody has confirmed the files really run at 120. Shoot a clip and
  read the DNG `FrameRate` tag and the frame count.
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
