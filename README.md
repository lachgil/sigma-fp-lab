# sigma-fp-lab

Extra recording modes, an on-camera menu to switch them, and a way to run your
own code on the **SIGMA fp** (firmware Ver.5.02).

Everything here lives in RAM and loads from the SD card at boot. **Nothing is
flashed.** Take the files off the card, or pull the battery, and the camera is
exactly as it was.

> **fp Ver.5.02 only, not the fp L.** This is experimental work by camera
> owners, not SIGMA. It is not a firmware release and comes with no warranty.
> Read [Safety](#safety) before you put a card in.

Built on [ijigen/fpSup](https://github.com/ijigen/fpSup), which worked out how
to run code on this camera in the first place. No SIGMA firmware is included;
you supply your own copy.

## What it does

### 1. A menu on the camera

Boot the card, press **RIGHT** to cycle options and **UP** to turn one on.
Everything starts off, and **Stock** switches it all back off.

The options are recording modes the stock menu cannot reach: the sensor
supports them, but no menu item names them. See **[docs/menu/](docs/menu/)**.

### 2. Recording modes the fp does not ship

| Option | Framing | Readout | Recorded | Rolling shutter |
|---|---|---|---|---|
| Open Gate | 100% of the sensor | 2x2 binned | 3032x2012 | 9.2 ms |
| **M130 24P** | middle 65% (1.53x crop) | **full 1:1** | 3968x2640 | 16.3 ms |
| M130 30P | middle 65% | full 1:1 | 3968x2640 | 16.3 ms |
| M6 4K | 69% x 54% | full 1:1, 14-bit | 4176x2174 | 27.5 ms |

**M130 24P is the one that holds.** Full 1:1 readout at 3968x2640 is 377 MB/s
at 23.976; the same mode at 29.97 needs 471 MB/s and stopped after about five
seconds on test. Open Gate is the widest field, M130 the most real detail on a
tighter frame. Neither is simply better. Details and measurements in
**[docs/modes/](docs/modes/)**.

### 3. Our own code, reading the image and drawing on the screen

![A histogram drawn on the camera by our own code](docs/osd_histogram.png)

That histogram is computed and drawn inside the camera, by our code, on its own
thread, about ten times a second.

**The camera's built-in histogram is better, and the fp already has false
colour.** The point is not the plot: it is that the display and the live image
are now reachable, which is what any custom tool would need. How it works, and
what is still missing, is in **[docs/overlay/](docs/overlay/)**.

## Try it

**The easy way, no computer needed after the copy:** grab a file from
[`cards/`](cards/), rename it to `AutoRun.txt` on the SD card root, and cold
boot. Instructions and what each one does are in [`cards/README.md`](cards/README.md).

**Build the full menu card yourself** (needs your own firmware copy, see below):

```sh
.venv/bin/python tools/build_combined_card.py
```

That writes `AutoRun.txt` and `VSHL.BIN` into `builds/combined-menu/`. Copy both
to the root of the SD card with a card reader, then cold boot with USB
unplugged. The screen reads `fpLAB MENU` when it has loaded.

**Live experiments over USB** need a debug card and the fpSup USB shell running:

```sh
.venv/bin/python tools/hist_deploy.py place    # the histogram overlay payload
.venv/bin/python tools/hist_deploy.py start    # its resident thread
.venv/bin/python tools/hist_deploy.py stop
```

## Layout

| Folder | What is in it |
|---|---|
| [`cards/`](cards/) | Ready-to-run AutoRun files and how to use them |
| [`src/`](src/) | The code that runs on the camera (ARM assembly) |
| [`tools/`](tools/) | Host-side scripts: build cards, deploy, disassemble, emulate |
| [`docs/menu/`](docs/menu/) | The menu card, and the camera's native menu system |
| [`docs/modes/`](docs/modes/) | Sensor modes, timings, what recorded and what did not |
| [`docs/overlay/`](docs/overlay/) | Reading the live image, drawing on the screen |
| [`docs/display/`](docs/display/) | The green preview, playback darkness |
| [`docs/research/`](docs/research/) | Open leads, firmware notes, dated raw findings |
| [`tests/`](tests/) | Offline tests for the host tools |

[`docs/status.md`](docs/status.md) is the current state of play;
[`docs/test-runbook.md`](docs/test-runbook.md) is the live-camera procedure.

## Building from source

You need Python 3, Clang with ARM support, and **your own copy of the fp 5.02
firmware** at `inputs/FP__V502.bin`. No firmware is distributed here.

```sh
python -m venv .venv
.venv/bin/python -m pip install capstone unicorn
git clone https://github.com/ijigen/fpSup.git reference/fpSup
git -C reference/fpSup checkout 26befa62ed0df95bfd22661eb98b53d3f962c9f0
.venv/bin/python tools/inspect_firmware.py inputs/FP__V502.bin --out analysis/MAIN_c0000000.bin
.venv/bin/python tools/fwmap.py build
```

Then the offline checks, none of which touch a camera:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tools/emulate_hook.py          # the geometry hook, in an ARM emulator
.venv/bin/python tools/emulate_menu.py          # the packaged menu binary, likewise
.venv/bin/python tools/frame_access_probe.py    # firmware image-access paths, offline
```

`inputs/`, `analysis/`, `builds/`, `captures/`, `reference/` and `host/` are
deliberately not published: that is where firmware-derived material lives.

## Safety

- **fp Ver.5.02 only.** Not the fp L, not other versions.
- Back up whatever is on the card first, and keep only one `AutoRun.txt` on it.
  Do not paste two projects' AutoRuns together.
- Cold boot with the battery out for a few seconds, card in, USB unplugged.
- Change options only while stopped, with the card finished writing. After
  changing one, switch the recording preset away and back before you roll.
- Anything over roughly 400 MB/s wants the SSD, attached **before** power-on.
- Back to stock: remove the files, battery out and in. RAM only is still not a
  guarantee against a lost take, so do not shoot anything you cannot reshoot.
- No firmware flashing path is involved, proven or implied.

## Credits

[ijigen/fpSup](https://github.com/ijigen/fpSup) for the loader, the USB shell
and the gyro logger this builds on. The gated record hook was contributed back
as [fpSup#2](https://github.com/ijigen/fpSup/pull/2). fp3k is credited in
[`docs/display/green.md`](docs/display/green.md) for the standby display fix.
