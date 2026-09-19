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

The **release card carries Stock and four rows**: the three modes that have
actually recorded on a camera. Everything else, Green Fix included, is on the
**debug card**, where whoever is testing it can read what it did. RIGHT moves,
UP switches on, **Stock** switches everything off.

| Row | Sensor mode | Field of view | Readout | Recorded | Rolling shutter | Rate | Status |
|---|---|---|---|---|---|---|---|
| **M130** | M130 | middle 65% (1.53x crop) | **full 1:1** | 3968x2640 | 16.3 ms | 377 MB/s at 23.976 | **sustained on hardware**, two minutes. At 29.97 it needs 471 MB/s and stops after about 5 s |
| Open Gate | M117 | **the whole sensor** | 2x2 binned | 3032x2012 | 9.2 ms | 274 MB/s | works; greens the standby preview, and the fix for that is a dev row |
| FHD 120 | M58 (from M27) | whole sensor | 2x2 binned, 3032x1708 read | 1920x1080, **12-bit** | | 60 -> **120 fps** | **recorded on hardware** at about 389 MB/s, then overflows after a couple of seconds: a burst, not a take |
| Green Fix *(dev)* | | | | | | | 3:2 preview for the 3:2 modes, ported from FP3K; **never run on a camera**, which is why it is a dev row |
| M130 FAST *(dev)* | M130 | middle 65% | full 1:1 | 3968x2640 | **12.1 ms** | as M130 | line period only; **not** a frame rate |
| M98 60P *(dev)* | M98 | whole sensor | 2x2 binned | 3032x2012 | 12.4 ms | 548 MB/s | offline only, expect short bursts |
| 2K120 *(dev)* | M56 (from M139) | whole sensor | 2x2 binned | 2016x1344 | | 60 -> 120 fps | the readout is selected; the cadence is unconfirmed |
| 672 240 *(dev)* | M12 (from M140) | whole sensor, letterboxed | 2x2 binned | 2016x672 | | 60 -> 240 fps | same caveat |
| False Col *(dev)* | | | | | | | latches false colour, which the camera only offers held |
| Gyro, Gyro-Gate *(dev)* | | | | | | | writes Gyroflow GCSV alongside the take |
| M98 30P *(dev)* | M98 | whole sensor | 2x2 binned | 3032x2012 | 12.4 ms | 274 MB/s | a comparison against Open Gate: same raster, more skew |
| M6 4K *(dev)* | M6 | 69% x 54% | full 1:1, 14-bit | 4176x2174 | 27.5 ms | 476 MB/s | **no visible gain**: file depth follows the menu's 8/10/12 |
| 2088 120 *(dev)* | M103 (from M88) | whole sensor | 2x2 binned | 2088x1174 | | 60 -> 120 fps | FHD 120's smaller sibling, never run |
| SEL *(dev)* | | | | | | | read-only: what the mode probe last saw |

The rate follows your recording preset, so M130 means 24p when the camera is on
CinemaDNG FHD 23.976 and 30p when it is on 29.97.

**M130 at 23.976 is the one that holds**, and it is the mode this project found.
Open Gate is the widest field; M130 is the most real detail on a tighter frame,
and it frames like a 1.53x crop **because** it is 1:1: 3968 of the sensor's 6064
columns, one photosite per pixel, no binning. Neither is simply better.
Measurements in **[docs/modes/](docs/modes/)**.

Open gate itself is worth saying plainly: it came down the fpSup/FP3K line, and
**ijigen/fpSup has since released a much more complete open gate of its own**,
OG3K v0.2.2a (tag `fpsup-og3k-v0.2.2a`, commit `a029b6a4`): eight frame rates,
3024x2010 recording geometry that follows the fp's own +16/+10 crop convention,
working in-camera playback, native ISO/highlight headroom restored, shutter-angle
correction and 8/10/12-bit CinemaDNG. What is here is the original FHD-slot M117
swap plus our own menu around it, and it is not a port of that work: the latest
OG3K core sources are not published. If open gate is what you want, start there.

### 3. Our own code, reading the image and drawing on the screen

![A histogram drawn on the camera by our own code](docs/osd_histogram.png)

That histogram is computed and drawn inside the camera, by our code, on its own
thread. The same thread also draws a **menu panel**: every option in the card's
menu at once, with the selected row highlighted and each state read live, using
a font we carry ourselves. And it can **change camera settings** through the
camera's own property call, which is the groundwork for an option that selects
its own recording preset.

**The camera's built-in histogram is better, and the fp already has false
colour.** The point is not the plot: it is that the display and the live image
are now reachable, which is what any custom tool would need. How it works, and
what is still missing, is in **[docs/overlay/](docs/overlay/)**.

## Try it

**The easy way, no computer needed after the copy:** grab a file from
[`cards/`](cards/), rename it to `AutoRun.txt` on the SD card root, and cold
boot. Instructions and what each one does are in [`cards/README.md`](cards/README.md).

**Latest dev builds:** the newest cards, including work that has not been on a
camera yet, are published as
[releases](https://github.com/lachgil/sigma-fp-lab/releases) and as zips in
[`cards/`](cards/). A dev card is a real experiment: read its notes before
booting it, and expect to pull the battery.

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

## How you can help

**The one thing worth your time: shoot M130 24P and grade it.**

Of everything here, M130 24P is the mode this project actually unlocked. It is
the fp recording **3968x2640, full 1:1 readout, no binning at all**, 12-bit
CinemaDNG at 23.976 to an SSD, and it holds: a two-minute take with correct
full frames. Open Gate came down the fpSup/FP3K line; M6 4K measured no real
gain because file depth follows the menu's 8/10/12 setting. This one is ours,
and it has been tested by one person on one camera.

What we cannot do alone is tell you whether the **footage** is good. That needs
other eyes, other lenses and a real grade.

How to try it:

1. Card: [`cards/fp-menu-card.zip`](cards/) (or a
   [release](https://github.com/lachgil/sigma-fp-lab/releases)). Both files at
   the card root, cold boot.
2. Attach a fast USB-C SSD **before** power-on. 377 MB/s sustained.
3. On camera: **CinemaDNG -> FHD -> 23.976p**, 12-bit.
4. In the card's menu (RIGHT to move, UP to turn on): **M130 24P**. Then switch
   the recording preset away and back once so the camera re-latches.
5. Roll. Load the CinemaDNG in DaVinci Resolve, and grade it like you would any
   other clip.

What to report, in an [issue](https://github.com/lachgil/sigma-fp-lab/issues):

- Does Resolve open the clip and play it, and does its frame rate read 23.976?
- How long did it record before it stopped, and on which SSD?
- How does it hold up graded: highlight roll-off, noise at ISO 400 to 3200,
  colour against the same scene shot in stock UHD.
- Aliasing and moire against stock FHD, which is 2x2 binned. This is where a
  full 1:1 readout should show its worth.
- Anything that looks wrong in the frame: banding, a green cast, partial rows.
- Frame-accurate audio sync is not part of this; CinemaDNG here is video only.

Also useful: **negative results.** A mode that refuses, a card that does not
boot, a camera that behaves differently from the one it was developed on. Those
are findings, not noise. Say which firmware (it must read Ver.5.02), which
card, and what the screen showed.

If you want to work on the code, the offline checks under
[Building from source](#building-from-source) run without a camera, and
[`docs/status.md`](docs/status.md) lists what is open. Please keep camera
claims separated from emulator claims, the way the docs here try to: say how
each thing is known.

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
