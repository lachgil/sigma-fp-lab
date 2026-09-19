# 2026-09-19 — corrections, and where the native menu actually stands

Read this before the older log below.

- **The native FP LAB row was built against the wrong page and is withdrawn.**
  Record Settings is scene `B2_5`; `B1_2_5` is Auto ISO Settings (ISO limits and
  shutter limits). The `--fplab-page` card option and `cards/fp-fplab-row-card.zip`
  are gone: the row they produced was a copy of an Auto ISO limit row with its
  list, activation and animation records dropped, which cannot focus, open or
  label itself. Nothing was lost -- it had never been on a camera.
- **The prerequisite that was missing is now done.** `tools/nbu_components.py`
  reads each component's property table out of the firmware (registration at
  `0xC05D5E30`, reader types from `0xC05E7B78`) and decodes records against it:
  206,836 component records across all 221 scenes consume exactly their own
  length. Field offsets, including the type-14 object references a graft has to
  renumber, are now read rather than guessed. The Frame Rate row is 294 records
  and 17,144 bytes with 73 object references, 4 of which leave the row.
- **Still blocked, and why.** Clips are allocated per animation group by the
  header (that relation holds for all 221 scenes: `nbu_scene.py verify`), but the
  clip records in a row do not follow their group record in stream order -- the
  Frame Rate row's six groups allocate 30 clips while 29 clip records sit in
  spans of 0/0/0/5/0/22. Until that mapping is established, a copied row's
  positional header entries cannot be sliced, so an animated row cannot be
  emitted. A row without animation also needs a private label: every four-digit
  localization key in the pool is referenced, so it needs the synthetic-string
  resolver hook FP3K uses at `0xC05E5B58`, not an overwritten stock string.
  And a native row is a UI change: it has to be seen on a camera before it is
  packaged, which is exactly the step the withdrawn card skipped.
- **Release card rows are now Stock, M130, Open Gate, FHD 120** -- the three
  modes that have recorded on a camera. Green Fix moved to the debug card
  because it has never run on a camera; the consequence is that Open Gate greens
  the standby preview on the release card.
- **Open gate upstream has moved well past ours.** ijigen's OG3K v0.2.2a (tag
  `fpsup-og3k-v0.2.2a`, commit `a029b6a4`, 2026-09-14) is eight rates, 3024x2010
  recording geometry, in-camera playback, ISO/highlight headroom restored,
  shutter-angle correction and 8/10/12-bit. Its latest core sources are not in
  the public tree or the handoff, so it cannot be ported from here, and nothing
  in this repo is that implementation. Ours is still the FHD-slot M117 swap.

# SIGMA fp mod — project status (overnight session 2026-09-11)

Worked the whole task list autonomously. Everything is RAM-only/reversible; no
flashing. Summary of what's **ready to test on hardware** vs **designed (needs
on-camera bring-up)**.

## READY TO TEST (in ~/Downloads)
- **`AutoRun-opengate-fixed.txt`** — open gate that no longer corrupts other
  modes (gated hook, r5==175). Confirmed working earlier.
- **`AutoRun-FHD120-test.txt`** — NEW: high-fps CinemaDNG **RAW** at ~120fps.
  Repurposes the FHD 59.94 CinemaDNG slot to sensor mode M58 (3032x1708→1080).
  You already recorded this once with **no green** — this is the persistent card.
  Records ~389 MB/s → use the **SSD**. Verify the .DNG FrameRate tag + frame
  count to confirm it truly ran 120 (SD may have capped it).
- **`AutoRun-2K120-test.txt`** — 2K (2088x1174→1080) @120, ~441 MB/s, SSD.
Load: copy to card root as `AutoRun.txt`, cold boot, select the preset, switch
away/back to re-latch, record to SSD.

## LIVE TOOLS (camera on the class-ff shell; idle only, never during record)
- `toggle_opengate.py on|off|status` — data-only flip of an ALREADY cold-boot-
  installed gated payload. It verifies the payload digest, hook and patch cells,
  aborts on any failed write/readback, disarms before table edits, and never
  reports enabled after a partial transition. It does NOT install code. No
  recording-idle interlock exists; a failed transition needs a cold-boot recovery.
- `toggle_opengate.py dark-on|dark-off` — EXPERIMENTAL one-cell gain
  (profile-122 0xC0BD8714 -> 4.8828125f). Unverified brightness; `off` restores it.
- `inspect_preview.py <capture> --base <addr>` — offline capture traversal:
  reports the node selector, the record-monitor sub-object (O+0x5C) and descriptor
  extents, with missing-range errors. No camera needed.
- `snapshot.py`, `memread.py`, `regiondiff.py`, `sigma_test.py` (sigma-ptpy).
  Host multi-hop reads can race; outputs are not atomic captures.

## DESIGNED — needs on-camera bring-up (docs)
- **Green** (`GREEN-HOOK.md`, `FIRMWARE-DECODE.txt` §2): SUPERSEDED by the
  session-2 update below. Record-time monitor only (live view + files fine).
  Standby canvas fix = accessor 0xC0437E98 (FP3K-verified, build_greenfix);
  record-monitor needs a record-start buffer hook, not the per-frame 0xC04376E0.
- **Darkness** (`PLAYBACK-DARKNESS.md`): the +0xfc per-profile digital-gain float
  is the knob; proposed cell 0xC0BD8714=0x409C4000. Needs a frame measurement to
  set the exact factor. Wired into `toggle_opengate.py dark-on`.
- **Playback** (`PLAYBACK-DARKNESS.md`): open-gate 3:2 shows as 1936x1090; the
  0xC096F580 table is DEAD, so the fix is in the develop-geometry chooser (needs
  more live tracing).
- **Stage-3 sideload** (`SIDELOAD.md`): SD-loaded VSHL.BIN extension carrying all
  features + a resident menu/controller, built on fpSup's existing loader.
- **On-camera menu + buttons** (`MENU-UI.md`): candidate handler 0xC0265800
  (0xC0269B50 is a destructor, not the callback). The earlier `src/menu_keyhook.S`
  draft was WITHDRAWN in review (placeholder addresses, no idle interlock, live
  instruction rewrite without cache maintenance). Design retained in MENU-UI.md;
  real key ABI, renderer lifecycle and idle gate still need on-camera bring-up.
- **Autofocus** (`AUTOFOCUS.md`): contrast metric readable at 0xC32914xx
  (manual-lens-safe); lens drive via 0xC033FDA0. BLOCKED by manual lens
  (LmountFocusNone::update 0xC0342238 is a no-op stub = no motor). PdCaf is dead
  on the fp. Custom contrast-AF designed for when an AF lens is fitted.

## REFERENCE
- `FIRMWARE-DECODE.txt` — full 6-subsystem decode + picker map.
- `analysis/fw.sqlite` (via `fwmap.py`) — 359k strings, 29k funcs, xrefs.
- `MODES.txt`, `RESEARCH.txt`, `DISCORD-FINDINGS.txt`, `MENU-AND-MODES.txt`.

## STAGE LADDER (your framing)
- Stage 1 (RAM pokes / table changes): DONE (open gate, high-fps, toggle).
- Stage 2 (function hooks / custom ARM): DONE for record (gated hook); green +
  menu hooks drafted, need bring-up.
- Stage 3 (persistent SD loader + custom menu/UI): DESIGNED end-to-end
  (SIDELOAD.md + MENU-UI.md); implementation = assemble sections + on-camera
  bring-up of input/OSD.
- Stage 4/5 (replace subsystems / flash): flash is CRC-only but no recovery
  bootloader → NOT recommended; stay sideload.

## NEXT HARDWARE SESSION (priority order)
1. Verify FHD120/2K120 recorded files (FrameRate tag + frame count) → confirm HFR.
2. Green: read the live-view geom object layout in open gate, place the display
   hook, iterate.
3. Darkness: `dark-on`, record fixed scene, measure vs stock, tune the float.
4. Menu: bring up the key-hook (confirm the key ABI live), then the OSD menu.

## Update 2026-09-11 (hardware)
- High-fps M58 CONFIRMED engaged: selected-mode register 0xC343B590 read 0x3A (58) during the take.
- It auto-stops after ~2-3s = eMOVREC_STOP_OVERFLOW (data rate > sustained storage) => running at 120 (2x data of 60p). Needs a fast SSD for longer takes, or a lower-data-rate mode. Short bursts work.
- fpSup PR opened: https://github.com/ijigen/fpSup/pull/2 (mode-isolation gated hook).

## Update 2026-09-11 (session 2 — live USB shell + mode intelligence)
Tooling now live: `fpshd` USB shell BUILT (reference/fpSup/fp_usb_shell, needs
libusb-1.0-dev + clang; both installed). Camera driven live over class-ff shell.
keystone/unicorn/capstone installed for offline assemble+emulate.

### GREEN — CORRECTED UNDERSTANDING (supersedes GREEN-HOOK.md live-view theory)
Ground truth from the user + live shell reads:
- The green is **RECORD-TIME MONITOR ONLY** (LCD/HDMI while recording). **Live
  view is clean. The RECORDED FILES ARE CORRECT open gate.** It is purely cosmetic
  monitor framing, NOT the live-view preview and NOT the files.
- Root cause (read live during a take): during record the geometry manager points
  `[0xC375D840+0xc]` (lv-geom) at node **`0xC375E480`** (selector `+0x00`=175) →
  sub-object **`0xC375E4DC`**. That sub is all 3:2 (3032x2012, 3008x2000) EXCEPT
  one stale FHD 16:9 pair at **`+0x0c/+0x10` = 1936x1090**. 16:9 output in a 3:2
  monitor canvas = green top/bottom bands.
- FP3K's `0xC0437E98` hook (build_greenfix) only fixes the STANDBY/live-view
  canvas → marginal on the record monitor. The record path uses accessor
  **`0xC04376E0`** (`[mgr+0xc]`), consumed by MovSigProcess `0xC0428B18` → YUV
  builder `0xC042A570`.
- Live experiments (shell): poking `0xC375E4DC+0x10` reverts every frame. A code
  hook on `0xC04376E0` forcing the field to 3032x2012 overscanned (full sensor
  into monitor buffer → more green/grain); 1936x1284 gave grain/stride drift.
  => the monitor **buffer is allocated 16:9 at record-start**; a per-frame accessor
  patch can't resize it. FIX MUST hook the record-start buffer/geometry build (not
  the per-frame accessor), following `[mgr+0xc]` dynamically, gated on selector 175.
  Cave `0xC072E600` + hook-on-`0xC04376E0` scaffolding exists (reverts on power-off).
- NOTE: `0xC375Exxx` node addresses are DYNAMIC (manager cycles them per state);
  must gate on the node's `+0x00`==175 selector, never a fixed address.

### OPEN GATE — ON HOLD (user decision)
M117 records correct 3032x2012, BUT M117 is a **2x2 readout (discards 3 of 4
photosites)** → soft. Only real win is 9.222 ms rolling shutter. Files fine;
not worth the green chase right now.

### NEW DIRECTION — UNLOCK FULL-READOUT MODES (see MODE-MAP.md)
All 70 IMX410 modes mapped by readout quality (firmware table `0xC0B59Exx`,
cross-checked vs reference/fpSup/gyro/analysis_imx410 CSVs). Sampling field
`+0x44/48/4c/50`: `1,1,1,1`=FULL(23 modes), `2,2,2,2`=2x2 soft(36 modes — M117
AND every 3K/2K CinemaDNG preset), `3,x`=heavier(11). The current "UHD" = **M7
6064x3412 FULL @30 (slot 0)** — already oversampled, why 2x2 can't beat it.
- Beat-UHD picks: **M6** 4176x2174 14-bit FULL (CLEAN 1-cell swap slot 13),
  **M130** 3968x2640 FULL @40, **M3** 6064x4042 FULL 6K, **M10** 6064x2022 FULL @60.
- Only dormant modes that CLEAN-swap (raster matches a slot) are the 14-bit ones
  into slot 13; all other new full modes need the record-geometry hook (retarget
  `rowpatch_gated.S`/build_gated to the new raster + VMAX). 14-bit CinemaDNG
  recorder is UNPROVEN (MODES.txt) — test first.
- Tool: **`build_modeswap_autorun.py <slot> <mode>`** — repoints any picker slot
  to any of the 70 modes; verifies stock, flags CLEAN vs NEEDS-GEOM-HOOK.

### NEW FILES THIS SESSION
- `MODE-MAP.md` — 70-mode quality map + unlock guide.
- `build_modeswap_autorun.py` — mode-unlock tool (needs reference/ CSVs).
- `build_greenfix_autorun.py` — FP3K-style standby-canvas display hook (0xC0437E98).
- `build_opengate_greenfix_autorun.py` — combined open-gate + standby green fix
  (record hook keystone-built; green hook only fixes standby, see GREEN note above).
- `GREEN-HOOK.md` updated with the effective objects + the record-monitor findings.

### NEXT AGENT — priority
1. MODE UNLOCK (primary): test M6 (clean, 14-bit 4K); build the geom-hook path for
   M130 (4K 3:2 @40 full) and M3/M10 (6K full) — retarget rowpatch_gated to the new
   raster + set VMAX from imx410_timing.csv. `build_modeswap_autorun.py` is the base.
2. GREEN (if revisited): hook the record-start buffer/geometry build, not the
   per-frame `0xC04376E0` accessor; the monitor buffer is 16:9-allocated.
3. Shell workflow: `fpshd --socket /tmp/fpshd.sock` (sudo) + `host/fpsh mem get/set`
   for live poke/observe. Records auto-stop ~20s (storage overflow) — enough to test.

## Update 2026-09-11 (session-2 evidence review — Main)
Read the session-2 work, verified it offline, and corrected overclaims without
touching the mode-unlock or green findings, which hold up.
- Independently disassembled the green accessor 0xC0437E98 (indexed:
  `*(obj+idx*8)`) and decoded build_greenfix's handler: a gated accessor-return
  substitution with an FNV-1a fingerprint over the object's 44-byte head, so a
  unit whose object differs passes through safely. Confirm the fingerprint on
  THIS camera before trusting the standby fix. inspect_preview.py now derives the
  same record-monitor sub-object (0xC375E4DC) the dev found live.
- Hardened `toggle_opengate.py`: aborts on any failed write, verifies the
  installed payload/hook/cells, disarms before table edits, arms only after
  success, no live instruction replacement. Added `tests/` (7 passing regressions).
- `emulate_hook.py` now assembles the current source and checks the returned R0
  plus preserved R4/R5/SP (it previously validated R4 under the name r0).
- Builders relabelled honestly: the record hook gates on ARMED + 1936x1090 +
  r5==175 (not a mode-id check); HFR output cadence/geometry remain unverified.
- Withdrew the non-deployable `src/menu_keyhook.S`; corrected the SIDELOAD key
  hook from the destructor 0xC0269B50 to candidate handler 0xC0265800.
- Reframed darkness/playback notes as unverified experiments/hypotheses.
- Caveat unchanged: M58 selection is not a measured 120fps; a short auto-stop is
  consistent with overflow but was not captured as a stop reason. No flashing.
