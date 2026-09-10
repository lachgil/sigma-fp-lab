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

## LIVE TOOLS (when camera on the shell)
- `toggle_opengate.py on|off|status` — flip open gate live (proven).
- `toggle_opengate.py dark-on|dark-off` — EXPERIMENTAL darkness compensation
  (profile-122 digital gain 0xC0BD8714 → 4.883f). Verify brightness vs stock.
- `snapshot.py`, `memread.py`, `regiondiff.py`, `sigma_test.py` (sigma-ptpy).

## DESIGNED — needs on-camera bring-up (docs)
- **Green** (`GREEN-HOOK.md`): cause fully traced (display uses live-view geom
  obj via MovSigProcess accessor 0xC04376E0; recomputed per frame → needs a code
  hook, not a poke). Hook point identified; must be placed + verified live
  (wrong preview geometry can freeze). Cosmetic (files are fine).
- **Darkness** (`PLAYBACK-DARKNESS.md`): the +0xfc per-profile digital-gain float
  is the knob; proposed cell 0xC0BD8714=0x409C4000. Needs a frame measurement to
  set the exact factor. Wired into `toggle_opengate.py dark-on`.
- **Playback** (`PLAYBACK-DARKNESS.md`): open-gate 3:2 shows as 1936x1090; the
  0xC096F580 table is DEAD, so the fix is in the develop-geometry chooser (needs
  more live tracing).
- **Stage-3 sideload** (`SIDELOAD.md`): SD-loaded VSHL.BIN extension carrying all
  features + a resident menu/controller, built on fpSup's existing loader.
- **On-camera menu + buttons** (`MENU-UI.md` + `src/menu_keyhook.S`, assembles):
  key handler 0xC0265800, interception via descriptor swap at 0xC091EA38, OSD
  renderer, controller FSM, feature-toggle map. Needs live input-ABI bring-up.
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
