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
- **Menu/Stage3/AF:** research, not implemented. The unsafe placeholder menu
  assembly was withdrawn rather than shipped as if working.
- **Darkness/playback:** unverified hypotheses and explicit experiments, not fixes.

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
