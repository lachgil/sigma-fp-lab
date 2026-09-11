# sigma-fp-lab

Experimental SIGMA fp Ver.5.02 research and card-loaded RAM patches, building on
[fpSup](https://github.com/ijigen/fpSup). **Not replacement firmware, a complete
camera extension, or a supported release. No SIGMA firmware image is included.**

## Current status

Start with [STATUS.md](STATUS.md), reviewed 2026-09-11.
**Want to just test a card? See [`autoruns/`](autoruns/)** for ready-to-run files
(open gate, open gate + green fix, FHD120, 2K120) with install instructions, and
[`TEST-RUNBOOK.md`](TEST-RUNBOOK.md) for the live shell session + green probe.

- **Open-gate isolation:** selector-gated record hook, with limited earlier
  hardware observations and current-source ARM emulation.
- **Mode unlock (current direction):** all 70 IMX410 modes mapped by readout
  quality in [MODE-MAP.md](MODE-MAP.md); M117 open gate is 2x2 (soft), so the
  focus is dormant full-readout modes (M6/M130/M3/M10). `build_modeswap_autorun.py`
  repoints any picker slot to any mode.
- **Green preview:** record-time MONITOR only (live view and recorded files are
  fine). Standby canvas has an FP3K-verified accessor fix (0xC0437E98,
  `build_greenfix`); the record monitor needs a record-start buffer hook. Both are
  documented and independently verified offline in [GREEN-HOOK.md](GREEN-HOOK.md).
- **High-fps RAW:** M58 selection observed; recorded 119.88/120fps cadence and
  sustained throughput still unverified. A short auto-stop does not prove overflow.
- **Menu/Stage3:** combined boot card built in `builds/combined-menu/` using
  `build_combined_card.py`. RIGHT/UP interaction was proven live; the combined
  binary passes ARM emulation. Its cold boot and recordings still need hardware.
  AF remains research.
- **Darkness/playback:** unverified hypotheses and explicit experiments, not fixes.

## Combined menu card

Build with `.venv/bin/python build_combined_card.py`; exercise the resulting
binary with `.venv/bin/python emulate_menu.py`.

`./publish_card.sh` does both and then copies `AutoRun.txt`/`VSHL.BIN` plus a
plain-language `README.txt` to the NAS card folder, refusing to publish if the
build or either emulation fails. Destination defaults to the gvfs mount of
`smb://192.168.1.40/media/fp/card`; override with `FP_CARD_DEST`. Files are
written to a temp name on the share and moved into place, so a half-copied
binary never sits beside a new script, and both hashes are re-checked after.

For **SIGMA fp 5.02 only, not fp L**. Back up the previous card files, then replace
both `AutoRun.txt` and `VSHL.BIN` at the SD root using a card reader. Do not append
this AutoRun to another hack. Start with a full power-off and battery removal,
then boot with USB disconnected. No firmware-update operation is involved.

- Boots with all features disabled. RIGHT cycles Stock, Open Gate, M98 30P,
  M130, M130 FAST, M98 60P, M6 4K, Gyro, Gyro-Gate, SEL. UP toggles; Stock
  switches all off. SEL is read-only: `SEL=xx C=nn L=mm` -- the probed
  selector, how many cells the last change rewrote, and how many name M130 now.
- **None of these modes is reachable from the stock UI**: no picker cell in the
  image names M117, M98, M130 or M6. M58 *does* have its own cells, so FHD
  119.88 is already a stock preset and the old "High FPS" option was removed
  rather than shipped as a duplicate that also mis-times the 60p preset.
- Frame rate is `72 MHz / (HMAX * VMAX)` and rolling shutter is
  `HMAX * lines / 72 MHz`, verified against all 70 modes (max error 0.15 fps).
  The timing table is `0xC0B59500 + n*0x20`: mode id at +0, HMAX low16 at +4,
  VMAX low16 at +8. Only the low half is written; the high half differs per mode
  (4 on binned entries, 1056 on M130, 952 on M6) and is preserved. Detuning
  VMAX is what makes an otherwise throughput-impossible mode recordable.

| option | sensor mode | sensor FOV | readout | canvas | RS | rate |
|---|---|---|---|---|---|---|
| Open Gate | M117 | **100% x 100%** | 2x2 binned | 3032x2012 | 9.22 ms | 274 MB/s |
| M98 30P | M98 | 100% x 100% | 2x2 binned | 3032x2012 | 12.44 ms | 274 MB/s |
| M130 30P | M130 | 65% x 65% (1.53x crop) | **FULL 1:1** | 3968x2640 | 16.32 ms | 471 MB/s |
| M130 FAST | M130 | 65% x 65% | FULL 1:1 | 3968x2640 | **12.10 ms** | 471 MB/s |
| M130 24P | M130 | 65% x 65% | FULL 1:1 | 3968x2640 | 16.32 ms | **377 MB/s** |
| M98 60P | M98 | 100% x 100% | 2x2 binned | 3032x2012 | 12.44 ms | 548 MB/s |
| M6 4K | M6 | 69% x 54% | FULL 1:1 14-bit | 4176x2174 | 27.51 ms | 476 MB/s |

For reference, stock: FHD/M106 is 100% FOV 2x2 (10.56 ms), 4K/M102 is a 1.45x
crop at 1:1 (13.44 ms), UHD/M7 is full-width 1:1 (21.09 ms).

- **Open gate is the full sensor area at HALF resolution** (3032x2012 is exactly
  half of the 6064x4042 full readout in each axis, i.e. 2x2 binned). M130 is the
  opposite trade: real 1:1 detail, but only the middle 65%, so it frames like a
  1.53x crop. Neither is strictly better -- open gate for the widest field, M130
  for maximum detail on a tighter frame. Full area AND full resolution is M3
  (6064x4042, 24.5 MP), which needs detuning to about 12-15 fps to fit storage.
- **M130 30P** is confirmed recording on hardware (2026-09-11): a sustained
  sequence of 3968x2640 12-bit frames to SSD, `DefaultCropOrigin (0,0)`,
  `DefaultCropSize (3968,2640)`, `ActiveArea (0,0,2640,3968)`, so publishing the
  active area equal to the base was correct. Its crop margin is unknown (the 12/6 measured on M117's binned
  DNGs does not carry over to a 1:1 window), so no margin is claimed -- check
  `DefaultCropOrigin`/`DefaultCropSize` on the first clip.
- **SOLVED (2026-09-11, hardware): the `raw_zoom` scaler.** The camera's own
  `imager mode_now` reports a raw_zoom stage; unity is 1024 and the FHD profiles
  ship 0x640 = 1600, a 1.5625x downscale. Open gate only ever set unity for the
  FHD 29.97 profile, so 29.97 was the only rate that recorded a whole frame:
  every other rate produced a correctly-sized buffer containing a 1.5625x
  shrunken picture in the corner (3968/1.5625 = 2540, 2640/1.5625 = 1690, which
  is what the clips measured on both axes). The four arrays are indexed by
  profile with a 4-byte stride and profile = selector - 53, so the cells for any
  rate are the 29.97 ones offset by (selector - 175) * 4. Predicted, poked live
  at 25p, and M130 then filled the frame and sustained. Now computed per option
  from the probed selector, so every framerate works.
- **The record path will not accept an arbitrary raster** (2026-09-11,
  hardware). M10 (6064x2022, full sensor width) was built and REFUSED at both
  29.97 and 23.976: the cells were rewritten (C=04) and the clip came back a
  clean, ordinary 1920x1080 -- the signature of the path validating the
  substitute and falling back, as distinct from a corner-boxed frame (canvas
  applied, scaler wrong) or a torn one (sensor cannot read it). M130 at 3968
  wide is accepted; 6064 is not, and the FHD path's buffers are the likely
  reason. So a 6064-wide mode has to be substituted into the UHD family, which
  already handles that width -- which needs the hook to gate on the UHD source
  row as well as 1936x1090. The option was removed rather than left in the menu
  doing nothing; the parameterised target table it proved out stayed.
- **M130 follows the preset you have selected: choose the preset FIRST, then
  turn it on.** It gates the canvas on the selector the probe last saw and
  repoints the mode ids belonging to that rate, so it works at 29.97, 23.976 or
  25 without a rebuild. An unmeasured selector is refused with
  `SEL=xx RATE UNKNOWN` rather than guessed -- 156 was read on hardware at
  another bit depth, so **bit depth changes the selector too**; report the
  number and it can be added. Measured: 175 = FHD 29.97 12-bit, 173 = 59.94,
  176 = 23.976, 180 = 25, and 156 = another FHD configuration (rate unknown).
- **M130 30P stopped after about five seconds on hardware** (2026-09-11): the
  recorder runs out of buffer at 471 MB/s. M130 FAST does the same, as expected
  -- HMAX changes rolling shutter, not data rate. **M130 24P** is the fix that
  addresses the actual limit: the same full-readout canvas in the FHD 23.976
  cell (selector 176, measured), timing entry 4116 -> 6748, **377 MB/s**. Also
  worth trying on any of them: 10-bit takes 29.97 down to 392 MB/s and 8-bit to
  314, since file depth follows the menu setting.
- **Mode swaps rewrite EVERY cell naming that mode, found by scanning.**
  Hardcoding addresses failed twice on hardware: a mode id appears both in the
  `0xC0BE5700` records (`{group, format id, mode, descriptor}`, 0x10 stride) and
  in each of the three parallel tables above them, and which of those a given
  framerate reads is not uniform -- open gate works rewriting three of M106's
  four cells, while FHD 23.976 has **five** M109 cells and rewriting three left
  the sensor on its stock mode. The signature of that failure is specific and
  worth recognising: the canvas applies, the mode does not, and the clip is the
  stock raster in the top-left corner of an oversized frame with garbage in the
  rest. `SEL` reports the count, so a swap that found nothing is visible.
  (An earlier attempt asked the firmware for the live mode via `0xC032C720`;
  that returns 8 at every framerate, so it is not the sensor mode id and the
  option built on it was removed rather than left to act on a wrong value.)
- **M130 FAST** is the HMAX experiment: the same M130 canvas with its line
  period cut 445 -> 330, which is what rolling shutter is made of (16.32 ms ->
  12.10 ms, better than stock 4K), VMAX 4116 -> 7278 to hold 29.97. Every 12-bit
  mode in the image uses 445 whatever its width (2016 and 6064 alike) and 330
  appears only on binned modes <=3032 wide, so 330 at 3968 wide 1:1 is
  unobserved. If the sensor cannot read a line that fast the frames tear; it is
  RAM only, so toggling off or a power cycle undoes it.
- **SEL** shows the selector the hook's probe last saw for an FHD row. Switch the
  preset to any framerate, read the two hex digits, and that is the number a new
  option for that framerate must be built with. Measured so far: FHD/29.97 reads
  AF (175), 59.94 reads AD (173), 23.976 reads B0 (176).
- **M6 4K** was tested live 2026-09-11 and looked identical to stock 4K: the
  CinemaDNG file depth follows the menu's 8/10/12 setting, so M6's extra two
  bits are quantised away. Kept because it is a three-word swap and the only
  path to 14-bit if that recorder limit is ever lifted.
- The FHD/59.94 selector is **173**, measured on hardware: the hook's probe read
  175 with the preset at FHD/29.97 and 173 at FHD/59.94, which is a
  framerate-tracking cross-check rather than one reading. It is the default;
  `--og60-sel 0` puts the option back to refusing, and any unmeasured framerate
  draws `NEEDS SEL=xx` with the probe value instead of guessing.
- Conflicting options hand the cell over instead of stacking: Open Gate, M98 30P
  and M130 30P share the FHD 29.97 cell; M98 30P and M98 60P share M98's single
  timing entry. M6 (4K cell) and M98 60P (59.94 cell) are independent, and the
  canvas is published per selector slot so a 3968x2640 mode at 29.97 can run
  beside a 3032x2012 one at 59.94.
- After changing any mode option, switch the recording preset away and back
  before recording, including after returning to Stock.
- Gyro uses the working example's GCSV writer and lens JSON beside CinemaDNG
  clips. No sidecar folder setup is needed. It does not add MOV gyro support.
- Change features only while stopped and storage has finished writing. The
  menu refuses known recording/writer-busy states; concurrent record-start
  versus menu-toggle timing is not verified by the emulator.
- Stock disables the feature hooks but retains the menu and gyro allocations.
  For an entirely stock boot, remove both files and remove/reinsert the battery.
- This card intentionally has no USB shell or endpoint patches, preserving the
  normal USB path. Combined SSD operation still requires a hardware test.

`manifest.json` records section hashes and addresses. Emulator boundaries stand
in for allocation, cache, mode query and display services; passing does not
prove LCD persistence, real file writes, RTOS timing or sustained frame rate.

## Setup for offline work

Python3, Git and Clang with ARMv7 assembly support are needed for the checks below.
Obtain fp5.02 firmware yourself and keep it at `inputs/FP__V502.bin`.

```sh
python -m venv .venv
.venv/bin/python -m pip install capstone unicorn
mkdir -p reference
git clone https://github.com/ijigen/fpSup.git reference/fpSup
git -C reference/fpSup checkout 26befa62ed0df95bfd22661eb98b53d3f962c9f0
.venv/bin/python inspect_firmware.py inputs/FP__V502.bin --out analysis/MAIN_c0000000.bin
.venv/bin/python fwmap.py build
.venv/bin/python emulate_hook.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python build_gated_autorun.py --out builds/opengate-gated/AutoRun.txt
.venv/bin/python build_highfps_autorun.py fhd120
.venv/bin/python build_greenfix_autorun.py            # standby green fix fragment
.venv/bin/python build_opengate_greenfix_autorun.py   # open gate + standby green
.venv/bin/python build_modeswap_autorun.py 13 6       # example: slot 13 -> M6
```

The HFR CLI writes `builds/highfps/AutoRun.txt`; a second profile run overwrites
that path. Preserve outputs separately when comparing profiles. Generated files
are local-only. Build-time verification does not verify the camera's firmware.
Additional historical DNG/PTP tooling needs tifffile, pyusb and the separately
obtained sigma-ptpy reference; it is not needed for the core offline checks above.

## Tools and evidence

- `inspect_firmware.py`: exact-input LZSS extraction for analysis, no repack/flash.
- `fwmap.py`, `af_dis.py`: candidate xrefs/disassembly, not complete control-flow
  recovery. Missing xrefs do not establish unreachable code.
- `inspect_preview.py`: offline RAM-capture traversal with an explicit base,
  missing-range errors and descriptor-derived extents. Example:
  `python inspect_preview.py captures/geom_singleton_current.bin --base 0xC375D7C0`.
  The current private capture is incomplete and intentionally exits2.
- `emulate_hook.py`: compiles and exercises the actual current ARM source.
- `build_gated_autorun.py`, `src/rowpatch_gated.S`: selector isolation work,
  submitted upstream as [ijigen/fpSup#2](https://github.com/ijigen/fpSup/pull/2).
- `build_highfps_autorun.py`: picker-only HFR experiments, no bundled USB shell.
- `build_modeswap_autorun.py <slot> <mode>`: repoint any picker slot to any of the
  70 IMX410 modes (needs the fpSup `analysis_imx410` CSVs); flags CLEAN vs
  needs-geometry-hook. See [MODE-MAP.md](MODE-MAP.md).
- `build_greenfix_autorun.py` / `build_opengate_greenfix_autorun.py`: FP3K-style
  standby display-canvas green fix (accessor 0xC0437E98), alone or combined with
  open gate. Standby only; the record monitor needs a record-start buffer hook.
- `toggle_opengate.py`: host control requiring matching cold-boot-installed
  payload and fpSup `host/fpsh`/fpshd. Checked failures and data-only arming.
  **Idle use only, no recording-state interlock.** `off` also restores its
  one-cell gain experiment. The revised host path has not been camera-tested.
- `memread.py`, `snapshot.py`, `regiondiff.py`, `sigma_test.py`: older diagnostic
  tools. Host pointer walks can race; their outputs are not atomic captures.

Research: [FIRMWARE-DECODE.txt](FIRMWARE-DECODE.txt), [MODE-MAP.md](MODE-MAP.md),
[GREEN-HOOK.md](GREEN-HOOK.md), [PLAYBACK-DARKNESS.md](PLAYBACK-DARKNESS.md),
[MENU-UI.md](MENU-UI.md), [SIDELOAD.md](SIDELOAD.md), [AUTOFOCUS.md](AUTOFOCUS.md),
[MODES.txt](MODES.txt), [MENU-AND-MODES.txt](MENU-AND-MODES.txt).
`RESEARCH.txt`, `DISCORD-FINDINGS.txt` and `manifest.json` also retain dated initial
observations. Use STATUS.md for current conclusions, not old completion claims.

## Safety boundary

Only fp5.02 is targeted, not fp L. Do not concatenate AutoRuns or combine cave
owners. Boot files must be matched. USB helper injection/file-transfer code can
overlap the open-gate cave. Do not run those helpers while its hook is active.
Do not modify the camera during recording or run unattended hardware experiments.

Remove boot payloads before cold-booting to stock. RAM-only is not a guarantee
against lost recordings, persistent side effects or hardware trouble. No flash
acceptance/recovery path has been proven. Never infer unsigned update acceptance
from missing crypto strings or a matching checksum.

## Publication and provenance

Tracked history was checked before public publication for firmware images,
binaries, excluded-directory files and common credential patterns. Only small
text source/research files were present; this is not a guarantee against every
possible secret pattern. `inputs/`, `analysis/`, `captures/`, `reference/`,
`host/`, `builds/` and `.venv/` stay excluded. Do not force-add proprietary dumps,
raw recordings, SDK distributions or credentials.

The gated assembly derives from fpSup's `opengate/rowpatch_v4.S`, with the added
ARMED/selector gate. The builder uses fpSup's helpers from a separate local clone.
No explicit upstream license grant was found in the pinned revision. Public
visibility does **not** create an open-source license or grant rights to SIGMA
firmware or upstream code. No new blanket license is asserted; clarify upstream
permission before further redistribution/licensing. Magic Lantern was an
architectural reference, not a SIGMA port or a license for camera firmware.
