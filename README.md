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

For **SIGMA fp 5.02 only, not fp L**. Back up the previous card files, then replace
both `AutoRun.txt` and `VSHL.BIN` at the SD root using a card reader. Do not append
this AutoRun to another hack. Start with a full power-off and battery removal,
then boot with USB disconnected. No firmware-update operation is involved.

- Boots with all features disabled. RIGHT cycles Stock, Open Gate, M98 30P,
  M130 30P, M98 60P, M6 4K, Gyro, Gyro-Gate. UP toggles; Stock switches all off.
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

| option | sensor mode | readout | canvas | rate | notes |
|---|---|---|---|---|---|
| Open Gate | M117 2x2 | 9.22 ms | 3032x2012 | 274 MB/s | works; card is fine |
| M98 30P | M98 2x2 | 12.44 ms | 3032x2012 | 274 MB/s | comparison against M117, more skew |
| **M130 30P** | **M130 FULL** | 16.3 ms | **3968x2640** | **471 MB/s** | no binning: the real image gain, SSD |
| M98 60P | M98 2x2 | 12.44 ms | 3032x2012 | 548 MB/s | open-gate framing at 60p, short bursts |
| M6 4K | M6 FULL 14-bit | 27.5 ms | 4176x2174 | 476 MB/s | picker-only; looked identical to stock 4K |

- **M130 30P** is the one with a genuine quality gain: full 1:1 readout, bigger
  than 4K, 3:2. Its crop margin is unknown (the 12/6 measured on M117's binned
  DNGs does not carry over to a 1:1 window), so no margin is claimed -- check
  `DefaultCropOrigin`/`DefaultCropSize` on the first clip.
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
