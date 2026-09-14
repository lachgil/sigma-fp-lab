# The GUI resource system, and what it means for a native FP LAB entry

**Source: ijigen's handoff tree, `fp-re-handoff-2026-09-14`, `research/ui/`,
measured on a live camera 2026-09-14.** Not our work. Recorded here because it
answers a question this project got wrong, and because two of its results change
what we should build next. Where a claim is repeated below it is because it was
re-verified against our own `MAIN_c0000000.bin`.

## Why our "native menus are blocked" conclusion was too broad

`docs/menu/ui.md` and `docs/menu/surface.md` concluded that a new native row is
impossible: rows are NBU scene objects, and the 303,600 chunks are byte-packed
with zero gaps. That remains true, and it is still the right answer *for rows*.

It is the wrong answer for **value lists**, which are a different subsystem:

| Layer | Where | Rebuilt? |
|---|---|---|
| Sorted string pool | `0xC18C0474` | on demand |
| Serialised UI records | `0xC1900000` – `0xC2D80000` | interpreted once at startup |
| Embedded Lua | above `0xC2D80000` | GUI bindings and expressions |

Records reference strings by **big-endian pool offset**,
`offset = string address - 0xC18C0474`.

## The result that matters most

**The enum→state pair table at `0xC2E4020C` is patchable at runtime and takes
effect on the next draw.** `MenuValueConverterBase__v21` (`0xC06BDA30`) returns a
hard-coded count of 3, and the table is:

```
0xC2E4020C  {3, 0}   UHD
0xC2E40214  {2, 1}   FHD
0xC2E4021C  {4, 2}   a third state SIGMA already defines
```

Verified in our own image: `[(3,0), (2,1), (4,2)]`. The third display state is
**stock**, not something a modification adds. What is missing is artwork:
`qs_resolution`, `set_resolution` and `font_menu_MB_resolution` ship exactly two
members each, while families that need more have them (`qs_aspect` 7,
`qs_mode` 7, `qs_iso` 43), and numbering need not be contiguous.

Their measured runtime-patchability table is worth repeating exactly:

| Data | Read when | Runtime patch |
|---|---|---|
| pair table `0xC2E4020C` | every evaluation | **works, immediately** |
| serialised UI records | interpreted once | no effect |
| parsed element objects | freed with their owner screen | does not persist |

## Icons are LZ4, and encodable

2005 XCI blobs live at `0xC1240014` – `0xC151C348`, in writable DRAM at runtime.
16 bpp, little-endian u16 per pixel, low byte luminance, high byte alpha. Their
`tools/xci.py` round-trips pixel-exact. Two spare families the fp never draws:

```
M_resolution_dci4K  @0xC1359724   2896 bytes
L_resolution_dci4K  @0xC12CA138   3472 bytes
```

## The GUI region is Thumb-2, and our hook machinery is ARM

This is the practical gap in our toolkit. Everything in the GUI area is Thumb-2,
so `src/*.S` ARM hooks do not apply there. Their technique, which we should
adopt if we go near it:

- enter with **`B.W` (T4), not `BL`**, so `lr` still holds the original caller's
  return and the displaced instructions can run unchanged;
- return with `bx` to a Thumb address (bit 0 set);
- avoid `bl` between local labels: it emits `R_ARM_THM_CALL`, which the
  relocation handler in `armasm.py` does not implement.

## The open question, and why we are well placed to answer it

The resolution array is built **once**, during the startup resource pass, and no
runtime event rebuilds it. They tried every screen transition with a filtered
hook armed on the append (`FUN_c05e2ca8`) and got zero hits: Quick Set draw (314
appends, none matching), `gui scr set 1_03_CINE`, resolution changed away and
back, MENU open/close, `SetRecMode`, `STILL`/`CINESW`.

So a hook has to be armed **before** the resource pass, which is an AutoRun
timing question they had packaged but not yet run.

**We boot our own payload from AutoRun already**, with a cave, a shell and a
menu that runs at boot. Arming an observation-only Thumb hook on `0xC05E2CA8`
from our boot path and reading the log after a cold boot answers it: a hit means
AutoRun runs before the resource pass and a native third resolution entry is
reachable; zero hits rules the route out and sends the search earlier.

That is a cheap, read-only experiment, and it is now built:

```sh
.venv/bin/python tools/build_combined_card.py --debug --ui-probe --out builds/uiprobe
# extract that card, cold boot, settle in live view, then
.venv/bin/python tools/uiprobe_read.py
```

`src/uiprobe.S` is a 44-byte Thumb hook on `0xC05E2CA8` that counts appends and
records the first eight element names. It changes nothing. The builder emits it
as three sections -- code at `0xC0732100` (verified empty in stock), state at
`0xC0732300`, and the `B.W` word at the hook site -- and the loader writes all
sections before jumping to our boot, which runs `F_CACHE`, so the site is
coherent before anything executes it. The branch encoding is checked by
round-tripping it through the disassembler at build time.

Result to report back to ijigen either way: a non-zero count means an AutoRun is
early enough and the native-entry route is open; zero rules it out.

## Corrections to our own notes

- The compression engine's throughput, unknown in earlier notes, was **measured
  at 169.7 Mpix/s**: enough for FHD compressed recording, not UHD. Calling it
  cold fails (power/clock domain requests refused) and damages still compression
  until restart.
- Sensor mode numbering: a single sample at either end is untrustworthy; take
  the mode that persists longest across a GFM6 history.
