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

## Before you boot it: clear your UP and RIGHT assignments

This card **takes the UP and RIGHT keys for itself** the whole time it is
loaded. There is no shortcut to open the menu, the keys are simply ours.

So, in the camera: **Menu -> Custom -> button settings, and clear anything you
have assigned to UP and to RIGHT.** Whatever is mapped there will not fire while
the card is in.

**Known bug, being worked on:** because the keys are taken globally, pressing
UP or RIGHT while you are inside SIGMA's own menu also moves our cursor in the
background. Nothing is written until you press UP on a row, but it is untidy and
it is the next thing on the list.

## What it does

**An on-camera menu, drawn top right.** RIGHT moves down the list, UP turns the
highlighted option on. Everything starts off. **Stock** turns everything off again. After
switching an option, change the recording mode away and back once so the camera
re-latches the geometry, otherwise the current take keeps the old one.

The panel lists every option at once with the current row highlighted. It has
its own thread, so it repaints about seven times a second, hides itself after
roughly four seconds of nothing changing, and reappears on the next key. It also
notices changes made anywhere else in the camera rather than only on a keypress.

**Turning an option on now makes the camera adopt it.** The menu writes geometry
cells, and nothing re-reads those on its own, which is why earlier builds needed
you to switch the recording preset away and back. Each option now also selects
the framerate it needs, and that write is what forces the re-latch. It is
drawn by our own code into the OSD layer, not by the firmware's one-line debug
text, which is fixed at the top left and could not be moved.

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

- **The panel's thread, the auto re-latch and the FALSE COL readout are new.**
  The offline emulator models the OSD surface, the display calls, the task
  creation and the framerate write, and asserts the panel really paints at
  780,60 and that a toggle really queues the re-latch. A model is still not a
  camera.
- **The drawn panel is new on the card.** It ran for weeks pushed over USB, and
  it now travels in `VSHL.BIN` like everything else. The offline emulator paints
  it into a modelled 1024x682 OSD surface and passes, but a model is not a
  camera. **Test on the dev card first**; if it locks up, pull the battery,
  nothing is written to flash.
- **FALSE COL** was confirmed working on a card. It is the one row that changes
  no geometry, so it needs no preset re-latch.

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
