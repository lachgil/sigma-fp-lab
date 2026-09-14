# sigma-fp-lab — test release

**SIGMA fp, firmware Ver.5.02 only. Not the fp L.**

Two cards. Both are RAM-only: they load from the SD card at boot, nothing is
flashed, and pulling the battery puts the camera back exactly as it was.

| Card | Who it is for |
|---|---|
| `fp-menu-card.zip` | **Everyone.** The on-camera menu, nothing else. |
| `fp-menu-card-dev.zip` | Development: same menu plus the USB shell and the `SEL` diagnostics. |

Extract the zip straight to the card root, so the card holds `AutoRun.txt` and
`VSHL.BIN`. Cold boot with the card in. **Both files are required, and they are
a matched pair:** each card has its own `VSHL.BIN` (the menu code lives there,
`AutoRun.txt` is only the loader), so never mix files between the two zips.

## What it does

**An on-camera menu.** RIGHT moves down the list, UP turns the highlighted
option on. Everything starts off. **Stock** turns everything off again. After
switching an option, change the recording mode away and back once so the camera
re-latches the geometry, otherwise the current take keeps the old one.

**Recording modes the stock firmware does not offer:**

| Option | What you get |
|---|---|
| Open Gate | Full-sensor 3032×2012 (3:2) CinemaDNG instead of the 16:9 crop |
| M98 30P / M98 60P | Alternative readout at 30p / 60p |
| M130, M130 Fast | 3968×2640 cropped RAW12 |
| M6 | Full-readout 14-bit class mode |
| Gyro-Gate | Open gate together with the gyro metadata build |
| SEL | A readout, not a switch (dev card only) |
| FALSE COL | False colour as a **latch**, not a held button |

**False colour without holding a button.** The camera only exposes false colour
while an assigned function key is held down: the key calls CameraIF `+0xCC` on
press (rec-manager event 0x21) and `+0xD0` on release (0x22). Nothing is stored,
which is why it never showed up in a diff of the settings store. The FALSE COL
row calls those two methods itself, so it stays on until you turn it off. It
writes no geometry cell, so unlike every other row it needs no re-latch.

The menu writes the camera's own geometry cells. It refuses to act while the
camera is busy, and it never changes a recording already in progress.

## What is proven, and what is not

Proven on hardware:

- Open gate records and the files are correct; other modes stay untouched
  (the record hook is gated on selector `r5==175`).
- The menu itself: options apply, `Stock` clears them, the busy guard holds.
- The standby live-view green fix, on the camera it was fingerprinted against.

Not proven, and where help is wanted:

- **FALSE COL is brand new and untested on a card.** It has only ever run from
  the USB-deployed overlay, where it worked. On the card it runs from the key
  path, which is where the camera's own function key runs it, but that has not
  been through a camera yet. **Test it on the dev card first**, and if the
  camera locks up, pull the battery: nothing is written to flash.

- **High-fps cadence.** The 120fps experiments hit ~389–441 MB/s and need a fast
  USB-C SSD. Nobody has independently confirmed the recorded files really run at
  120fps. Shoot a clip, read the DNG `FrameRate` tag and the frame count.
- **M130 / M6 / M98 in real use.** These are firmware-table modes that the stock
  menu never names. They record, but rolling shutter, banding and highlight
  behaviour have had almost no real-world footage put through them.
- **The record-time monitor.** Open gate's preview greens while recording. The
  files are fine. The standby fix does not cover the record path.
- **Other bodies.** Everything here was worked out on one camera. The display fix
  in particular checks a fingerprint and safely does nothing if it does not match.
  If it does nothing on yours, that is the expected safe path, please say so.

## Known limits, stated plainly

- **Focus magnification and false colour cannot be had during a take.** This is
  measured, not guessed: magnification is a distinct sensor readout mode
  (selector 0x11–0x28, e.g. 6064×2022), and recording runs a different one. See
  `docs/overlay/README.md`.
- There is **no firmware-version guard** in the card. Using it on anything other
  than Ver.5.02 is on you.
- The dev card's USB shell **replaces the camera's normal PTP interface**, so
  SIGMA Camera Control and tethering tools will not see the camera while it runs.

## Safety

Read `cards/README.md` before using a card. In short: RAM-only and reversible,
but a RAM change can still spoil a take. Do not run these on a paid job, do not
record anything you cannot afford to lose, and remove `AutoRun.txt` plus a
battery-out cold boot returns the camera to stock.

Built on [ijigen/fpSup](https://github.com/ijigen/fpSup), which worked out how to
run code on this camera. No SIGMA firmware is included; you supply your own.
