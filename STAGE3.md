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
- `--payload-addr` must be ABOVE the loader: loader lives `0xC072DE64..0xC072E064`,
  worker/shell state at `0xC072F000+`. Safe payload window: `0xC072E100..0xC072EF00`.
- Card carries TWO files: `AutoRun.txt` + `VSHL.BIN`. Revert = power cycle.

## Step 1 (BUILT): prove load + execute  ->  `autoruns/loader-test/`
`src/payloads/payload_marker.S` writes `0x510AADED` to `0xC072FA00` and returns.
Build: `src/payloads/build_loader_test.sh` (VSHL.BIN is git-ignored; regenerate).
TEST (camera on shell): boot the two files, then
`./host/fpsh mem get 0xC072FA00,,4` -> expect `D:0x510AADED`.
That single read proves our BIN loaded, placed, cache-flushed and ran. Do this
before trusting the loader with hook installs (a bad hook can freeze; a marker
cannot).

## Step 2 (NEXT, low risk): key-input logger  ->  unlocks the menu
The menu is blocked on one unknown: which physical button sends which id to the
key handler `0xC0265800`. Payload plan (data-only, reversible):
- entry: save `0xC091EA38` (stock `0xC0265800`), write our `logger` there
  (descriptor-pointer swap, MENU-UI.md option A — no instruction patch, no cache
  risk). Return.
- `logger(this, keyid)`: write `keyid` into a ring at `0xC072FA00`, then tail-call
  the real `0xC0265800(this, keyid)` so the camera behaves normally.
LIVE: press each button, `mem get` the ring, build the button->id map. If the
dispatcher caches the pointer (menu never sees keys), fall back to the inline
branch at `0xC0265800` (needs a cache flush, which the loader already does).

## Step 3: resident controller + OSD menu
With the id map, add to the payload a small priority task (tk_cre_tsk
`0xC0016A58`) that owns feature state and repaints the OSD, opened by a key
sequence, applying features (open gate, mode swaps) from menu entries with a
recording-idle gate. This is the Stage-3 headline. Renderer path and idle gate
still need live confirmation (MENU-UI.md).

## Reality checks folded in
- Open gate (M117) is a 2x2 sensor readout; its softness cannot be patched away.
  The only full-readout 3:2 mode is M3 (6K, ~1 GB/s = short bursts only).
- UHD (M7) and 4K (M102) are ALREADY full readout; same-resolution swaps (e.g. M6)
  do not beat them. Genuine gains = higher-res full modes (throughput-bound) via
  the record-geometry hook, or workflow (12-bit 4K vs 8-bit UHD).
- The record-monitor green is a separate, still-unsolved record-start buffer hook;
  it does not block the loader/menu work and is parked.
