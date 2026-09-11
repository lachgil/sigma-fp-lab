# Stage 3 — load our own code from the card (loader + resident menu)

Goal: stop hand-writing hundreds of `mem set` lines (Stage 1) and instead load a
compiled payload from an SD `VSHL.BIN` (Stage 2/3), carrying our hooks and a
resident on-camera menu. Uses fpSup's sanctioned loader, the same family FP3K uses.

## The engine (reused, not reinvented)
`reference/fpSup/fp_usb_shell/build_autorun.py --loader --payload <our.S> --entry <sym>`
- Assembles our `.S`, packs it into `VSHL.BIN` (`"VBIN"` magic, `{dest,len}`
  sections, absolute entry), and emits a short AutoRun that reads the file into
  RAM, checks the magic, places the sections, **flushes the instruction cache**
  (`0xC000E91C` — the thing plain `mem set` cannot do), and branches to our entry.
- Addresses actually used by the shipped card: loader `0xC072DE64`, absolute
  entry/trampoline `0xC072F000`, menu code at pool+0x50000, menu state
  `0xC072FB00`, geometry hook `0xC072F800`. Gyro keeps its own cave/pool layout.
- Card carries TWO files: `AutoRun.txt` + `VSHL.BIN`. Revert = power cycle.

## Step 1 (DONE): load + execute proven
A marker payload loaded from `VSHL.BIN` read back on the camera, proving the
file was placed, cache-flushed and run. The scaffold has been removed now that
the real card supersedes it.

## Step 2 (DONE): key map captured live
Descriptor-pointer swap at `0xC091EA38` (stock `0xC0265800`) is not cached, so a
resident handler sees every keypress and can tail-call the stock handler. Map:
UP `0x14`, DOWN `0x18`, LEFT `0x10`, RIGHT `0x0C`, OK `0x1C`, TONE `0x2F`
(release = press+1). Only RIGHT and UP have no native action, so they are the
only usable triggers.

## Step 3 (BUILT, awaiting hardware): combined boot card
`build_combined_card.py` emits `builds/combined-menu/{AutoRun.txt,VSHL.BIN}`:
the verified gyro release sections plus our geometry hook and resident menu,
with RIGHT selecting and UP toggling Stock / Open Gate / High FPS / Gyro /
Gyro-Gate. No resident task is used: the key handler owns state and repaints.
Details and the install procedure are in MENU-UI.md and README.md.

`emulate_menu.py` runs the packaged machine code (real stage2, trampoline, gyro
boot and menu) through Unicorn. Cold-boot display, real recordings and RTOS
timing remain unverified on hardware.

## Reality checks folded in
- Open gate (M117) is a 2x2 sensor readout; its softness cannot be patched away.
  The only full-readout 3:2 mode is M3 (6K, ~1 GB/s = short bursts only).
- UHD (M7) and 4K (M102) are ALREADY full readout; same-resolution swaps (e.g. M6)
  do not beat them. Genuine gains = higher-res full modes (throughput-bound) via
  the record-geometry hook, or workflow (12-bit 4K vs 8-bit UHD).
- The record-monitor green is a separate, still-unsolved record-start buffer hook;
  it does not block the loader/menu work and is parked.
