# sigma-fp-lab

Reverse-engineering + feature development for the **SIGMA fp** (firmware Ver.5.02),
building on the [fpSup](https://github.com/ijigen/fpSup) project. Everything here
is **card-loaded RAM modification** of one's own camera — reversible on power-off,
nothing is flashed. **No SIGMA firmware is included in this repo** (git-ignored);
regenerate analysis locally from your own `FP__V502.bin`.

## Read this first
- **`STATUS.md`** — current state: what's ready to test vs designed, and the next
  hardware steps. Start here.
- **`FIRMWARE-DECODE.txt`** — the full subsystem decode (geometry manager, record
  chain, display/live-view, sensor picker/timing, key input, loader) with
  addresses. The map everything else builds on.

## Feature notes / designs (addresses + hooks, confirmed vs inferred)
- `GREEN-HOOK.md` — open-gate live-view green: cause + concrete hook point.
- `PLAYBACK-DARKNESS.md` — darkness compensation knob + playback geometry.
- `MENU-UI.md` + `src/menu_keyhook.S` — on-camera OSD menu + button interception.
- `SIDELOAD.md` — Stage-3 persistent SD-load extension architecture.
- `AUTOFOCUS.md` — AF metric/drive map (blocked on a manual lens = no motor).
- `MODES.txt`, `MENU-AND-MODES.txt`, `RESEARCH.txt`, `DISCORD-FINDINGS.txt`.

## Tools (run in a venv with capstone, unicorn, pyusb, sigma-ptpy, tifffile)
- `inspect_firmware.py` — extract the analysis image from *your* FP__V502.bin.
- `fwmap.py` — build/query the firmware map DB (`str`/`xref`/`near`).
- `af_dis.py`, `emulate_hook.py` (unicorn) — disasm/emulation helpers.
- `build_gated_autorun.py` — open-gate AutoRun with the r5-gated hook (submitted
  upstream as ijigen/fpSup#2).
- `build_highfps_autorun.py` — high-fps CinemaDNG RAW (fp only offers 120/100 in
  MOV): repoints a picker slot to a high-fps sensor mode with matching raster.
- Live shell (needs the fpSup USB shell + `host/fpshd`, not in this repo):
  `toggle_opengate.py` (on/off/status/dark-on), `snapshot.py`, `memread.py`,
  `regiondiff.py`, `sigma_test.py` (sigma-ptpy Linux control).
- `src/rowpatch_gated.S` — the r5-gated open-gate canvas hook.

## Status snapshot (2026-09-11)
- Open gate isolation: FIXED (r5==175 gated hook), confirmed on hardware.
- High-fps RAW: sensor mode engages (mode reg=58 = M58/120fps), storage-limited
  (trips `eMOVREC_STOP_OVERFLOW` on slower cards; use a fast SSD).
- Green / darkness / playback / on-camera menu: decoded + designed, need on-camera
  bring-up.
- Autofocus: mapped; blocked on a manual lens.
- Flash (Stage 5): CRC-only but no recovery bootloader → not pursued; RAM/sideload
  is the ceiling.

## For an agent continuing this
Read `STATUS.md` then `FIRMWARE-DECODE.txt`. Analysis artifacts (extracted MAIN,
`fw.sqlite`, camera captures) are git-ignored — rebuild with `inspect_firmware.py`
+ `fwmap.py build` from the local firmware. Hardware work needs the camera on the
fpSup USB shell (class ff) or in Camera Control mode (sigma-ptpy).
