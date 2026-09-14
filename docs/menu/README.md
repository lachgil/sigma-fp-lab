# The menu card

A menu that lives on the camera, so the extra recording modes can be switched
with the buttons instead of by rebuilding a card. RAM only, loaded at boot.

Build it, copy `AutoRun.txt` and `VSHL.BIN` to the SD card root, cold boot:

```sh
.venv/bin/python tools/build_combined_card.py     # -> builds/combined-menu/
```

The screen reads `fpLAB MENU` when it has loaded. **RIGHT** cycles the options,
**UP** toggles the one shown. Everything boots off.

## The options

| Option | What it does |
|---|---|
| `STOCK` | Everything off |
| `OPEN GATE` | M117, 3032x2012, the whole sensor area, 2x2 binned |
| `M98 30P` | M98, same framing, slower readout |
| `M130 30P` | M130, 3968x2640 full 1:1, 1.53x crop. Follows the preset you pick |
| `M130 FAST` | M130 with the line period cut: rolling shutter 16.3 -> 12.1 ms. Experimental |
| `M130 24P` | M130 at 23.976, 377 MB/s. The one that sustains |
| `M98 60P` | Open-gate framing at 59.94, 548 MB/s |
| `M6 4K` | M6, 4176x2174, 14-bit full readout |
| `GYRO` | Writes `.gcsv` and `.json` beside CinemaDNG clips, for Gyroflow |
| `GYRO-GATE` | Open gate and gyro together |
| `SEL` | Read-only: `SEL=xx C=nn L=mm`, the probed selector and what the last change rewrote |

Numbers, framing and what actually recorded: [`docs/modes/`](../modes/).

## Using it without surprises

- **Pick the recording preset first, then turn M130 on.** It reads the selector
  the camera is using and repoints that framerate. `SEL=xx RATE UNKNOWN` means
  that framerate and bit-depth combination has not been measured yet: note the
  two hex digits and it can be added. Bit depth changes the selector, so stay on
  12-bit for now.
- **After changing anything, switch the recording preset away and back**, before
  you roll. That includes going back to Stock.
- Conflicting options hand the cell over rather than stack: Open Gate, M98 30P
  and M130 30P all live in the FHD 29.97 cell; M98 30P and M98 60P share M98's
  single timing entry.
- Change things while stopped, with the card finished writing. The menu refuses
  the recording and writer-busy states it can see; the emulator does not prove
  what happens if you race it against a record start.
- Stock turns the feature hooks off but keeps the menu and the gyro allocations.
  For a truly stock boot, remove both files and cycle the battery.
- This card has no USB shell, so the normal USB path is untouched, and nothing
  here can be asked questions over a cable. Build with `--debug` for a card that
  does carry the shell.

## The native menu, which is a different thing

The card's menu is ours, drawn over the top. The camera's *own* menu is a
separate system, and how far it can be bent is still being worked out:

- [`surface.md`](surface.md) decodes the NBR resource directory, NDB
  localisation and NBU scene definitions: 2,259 resources, 221 scenes. Renaming
  a native row works; changing its packaged English string did not.
- [`ui.md`](ui.md) covers the drawing and dispatch side.
- [`stage3.md`](stage3.md) is the earlier staged build-up.
- [`notes-menu-and-modes.txt`](notes-menu-and-modes.txt) keeps dated raw notes.

Open Gate can also be driven from a real native switch, **Playback → Slideshow →
Slideshow Settings → Repeat** (`0xC31ADA36`): the payload watches that byte for
changes on each key press. The RIGHT/UP controls still work, and either source
can win. The cost is that Repeat genuinely stays on, so slideshows loop.

## Tools

| Script | What it is for |
|---|---|
| `tools/build_combined_card.py` | Builds the card. `--debug` keeps the USB shell in |
| `tools/emulate_menu.py` | Runs the packaged binary in an ARM emulator, offline |
| `tools/publish_card.sh` | Build, verify, then copy the files to `FP_CARD_DEST` |
| `tools/menu_resources.py` | Reads the camera's own menu resources out of the image |
| `tools/menu_text.py`, `tools/menu_row.py` | Bounded live edits to menu text and rows |

`cards/manifest.json` records section hashes and addresses. Passing the emulator
proves the code runs; it does not prove LCD persistence, real file writes, RTOS
timing or a sustained frame rate.
