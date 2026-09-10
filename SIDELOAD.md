# Stage-3 sideload architecture (SD-loaded RAM extension + on-camera menu)

Goal: move from per-word `mem set` AutoRuns (Stage 1) to a small AutoRun that
loads a single compiled payload from the card into RAM (Stage 3), carrying all
features + a resident controller/menu. This EXTENDS the mechanism fpSup already
ships (the gyro base card), using the fp's own service-AutoRun feature. RAM-only,
reverts on power-off, nothing flashed.

## The existing mechanism to build on (confirmed)
fp boot runs `\AutoRun.txt` through the firmware shell (XC_ShellScriptAutoRun,
FUN_c03da758 reads it). The gyro base card already does a compiled-blob load:
- AutoRun writes a ~272-byte loader stub at cave `0xC072DE64` via `mem set`.
- `memmgr bufmem get 0 1048576` allocates a 1 MiB USER pool (pointer lands in
  `0xC3757A7C`); the loader derives all pool-relative addresses from it.
- One hook word (`0xC00D0794`, the gyro callback / or the echo handler
  `0xC0BAC2F8` one-shot) fires the loader once.
- Loader (`templates/loader.S`) reads `\VSHL.BIN`, checks magic, and stage2
  (`templates/stage2.S`) places sections and runs `F_CACHE 0xC000E91C` so the
  copied code is coherent, then branches to the named entry.

VBIN layout: `+0x00 "VBIN"`, `+0x04 count`, `+0x08 entry (absolute)`,
`+0x0C data bytes`, then `count` × `{dest, len}` records, then aligned data.
`dest < 0x40000000` = pool-relative (base+off); else absolute.

## Stage-3 payload = one VSHL.BIN carrying our whole extension
Sections (each a compiled .S, placed by the loader):
1. **feature-patch applier** (runs once at entry): applies the data patches for
   whichever features are enabled — open-gate picker/VMAX/RWZM, high-fps picker
   swap, darkness scalar — reading an enable-mask from a state word. Pure data
   writes; no per-mode risk when disabled.
2. **gated geometry hook** (`src/rowpatch_gated.S`, cave `0xC072F800`) armed only
   when open gate is enabled (existing, verified).
3. **green live-view hook** (from GREEN-HOOK.md) — added once verified on hardware.
4. **resident controller task** + **OSD menu renderer** (see MENU-UI.md): a
   priority-6 task (tk_cre_tsk `0xC0016A58`, stksz `0x2000`) that owns feature
   state and repaints the OSD; parked when the menu is closed.
5. **key-observer hook** (see MENU-UI.md, `0xC0269B50`) posts button events to
   the controller to open/navigate the menu.

## Memory map (avoid collisions)
- `0xC072DE64`..`0xC072E064`  loader stub (existing).
- `0xC072E064`..`0xC072F000`  worker/producer region (gyro uses this; free here).
- `0xC072F000`..`0xC072F800`  shell/worker state (keep clear if shell present).
- `0xC072F800`..`0xC072FA14`  open-gate gated hook + telemetry + ARMED (existing).
- USER pool (1 MiB from `bufmem get`): pool+0x0..0x7000 loader scratch,
  +0x8000..0x28000 loader read/stage2 window (do NOT place resident code here),
  +0x40000+ resident controller/menu code + state (gyro places its writer at
  +0x44000; pick +0x60000 for menu code, +0x70000 for menu state, to stay clear).
  Confirm with a live pool dump before committing offsets.
- State block (feature enable-mask + menu cursor + saved-originals) in a fixed
  pool-relative slot so the controller and the applier share it.

## Build pipeline (extend, not replace)
`build_gated_autorun.py` already assembles `src/rowpatch_gated.S` and emits an
AutoRun. Add a `build_extension.py` that:
1. Assembles each section .S (via `fp_usb_shell/armasm.py`, clang armv7).
2. Emits a VSHL.BIN (VBIN header + {dest,len} + data), reusing fpSup's packer
   (`fp_usb_shell/build_autorun.py --also-bin` / `gyro/build_base_card.py`
   `sections()`), placing sections at the map above.
3. Emits a minimal AutoRun that writes the loader stub + `bufmem get` + arms the
   one-shot hook — i.e. reuse `build_autorun.py --loader --vshl-entry <entry>`.
4. Verifies every firmware patch site against the hash-checked MAIN (stock words)
   and that all sections fit their reserved ranges (overlap check like
   `build_base_card.check()`).

## Firmware-version guard + revert
- Build-time: verify MAIN sha256 = `92a8ee993f...` and stock words at every patch
  site (already done in build_gated_autorun.py). No runtime version guard exists
  in stock; ADD one: the applier reads a known version marker in ROM and refuses
  to arm hooks if it does not match Ver.5.02 (fail-closed, leaves stock behavior).
- Revert: remove `\AutoRun.txt` (and `\VSHL.BIN`) and cold-boot (battery out).
  Everything is RAM; nothing is written to flash. The controller also exposes an
  in-menu "disable all" that runs the saved-originals restore (toggle_opengate.py
  logic, generalized).

## Why not flash (Stage 5)
fwup validates CRC32/checksum only (no crypto signature), so a modified update is
technically accepted, BUT there is no recovery bootloader in MAIN and brick risk
is high. The sideload path delivers persistent-per-card features with zero brick
risk and instant revert, so Stage 3 is the right ceiling for now; Stage 5 is
out of scope until a hardware recovery path (e.g. UART/JTAG) is proven.

## Status
Design confirmed against the existing fpSup loader + our decode. Implementation =
assemble the sections + packer wiring; the resident menu/controller and key hook
are drafted in MENU-UI.md and need on-camera bring-up (input ABI + OSD timing).
