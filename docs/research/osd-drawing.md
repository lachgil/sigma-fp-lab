# Drawing your own pixels on the LCD

SIGMA fp, firmware Ver.5.02. Everything below is read from the firmware
instructions (Capstone, 2026-09-20) unless marked as measured on the camera.

## Two layers, two formats

The LCD has a **main** layer and a **sub** layer, and they are different
objects with different pixel formats. Every descriptor says which it is:

```
descriptor +0x00   format: 0 = 32-bit, 1 = 16-bit, 3 = one byte per pixel (index)
           +0x04   pixel base
           +0x08   -> geometry: +0x00 width, +0x04 height
           +0x0C   -> palette descriptor {entries, count, generation}, indexed layers only
```

`0xC052B118` addresses a pixel as `base + (y*width + x) * {4, 2, 1}` by that
format, so the width is the pixel stride and there is no padding. **Check
`+0x00` before writing bytes**: on the LCD the sub layer is format 3
(1024 x 682 bytes) and the main layer is format 1 (16-bit, 4-bit alpha in the
top nibble; the order of the three low nibbles is not established).

### Getting a layer

```
0xC0698D80          display manager
[r0+4] -> [+0xC]    resolve(selector): 0 main, 1 sub (monitor 0); 2, 3 for monitor 1
```

This is how the firmware's own FalseColorBarDrawer gets the sub layer
(`0xC057DA78`). The shell's `display` commands go through their own
singleton (`0xC03E5698`, then `0xC03E56D8`) which turns `display monitor A B`
into the same selector; **`display monitor 0 1` selects the sub layer**.
`0xC03E5698` is not a display lock -- it is a guarded singleton accessor.

The drawable's vtable:

| slot | |
|---|---|
| `+0x0C` | front descriptor |
| `+0x10` | back descriptor |
| `+0x14` | rotate the three buffers (takes the layer's mutex); **not** a present |
| `+0x18` | `setPalette(&{entries, count, generation})`: copies `count*4` bytes, points all three descriptors at the copy |
| `+0x1C` | restore the default palette |
| `+0x20` | fill the back buffer with a raw pixel value |
| `+0x28` | byte stride (`r1 = 0` for the full width) |

The shell's `display osd` draws, rotates, then submits the same descriptor
with `0xC02E8A08(controller, descriptor, sub)`. Nothing holds a lock across
acquire/draw/rotate, so a resident thread that paints cached buffers races
the UI: measured as horizontal tearing lines and brief dropouts of the overlay
with a 150 ms repaint thread (2026-09-20).

### Drawing at the submit

`0xC02E8A08` is the last point every frame passes before the panel is
programmed, and its `descriptor` argument is the exact buffer about to be
shown. Hooking its first instruction (`push {r4, r5, r6, r7, fp, lr}`,
`0xE92D48F0`; replay it and resume at `+4`) gives a paint routine that runs
once per frame on the right buffer with no cached addresses and no thread:

```
r0  controller      LCD: 0xC02E44B0's object; remember it to present yourself
r1  descriptor      check +0x00 == 3 and the geometry before writing bytes
r2  sub             0 main, 1 sub
```

Early returns inside the function: `controller[0] == 3`, `[controller+4][0]
== 0`, and `sub != 0 && [controller+4]+0x78 != 0` (the sub layer hidden).

Because the layer's three buffers are recycled, a buffer that carried the
overlay has to be cleared once when the overlay is no longer wanted; track a
drawn flag per buffer base. Flush the cache (`0xC000E91C`) after painting:
the panel reads memory. To make a change visible at a key press rather than
at the UI's next frame, do the shell's sequence from the key handler: `+0x10`
back descriptor, `+0x14` rotate, `0xC02E8A08(controller, that descriptor, 1)`,
and let the hook paint it. `src/fcscale.S` (`fc_submit`, `fc_on_submit`,
`fc_present`) is the implementation.

Measured: asking for the buffer from an AutoRun at boot returns nothing; from
a key handler it works.

## The palette is A, Y, U, V

Palette entries are four bytes: **alpha, Y, signed U, signed V**. Not RGB.
The shell's own converter (`0xC03E4C38`) prints them as `a, y, u, v` and, when
asked for RGB, computes `R = Y + 1.402 V`, `G = Y - 0.344136 U - 0.714136 V`,
`B = Y + 1.772 U`.

Two traps that cost a card each:

- **`display palette <sel> <isRgb> <file>` writes the raw bytes whatever
  `isRgb` says.** The flag only changes the printed text. A file dumped that
  way is AYUV, and reading it as ARGB gives colours that do not exist.
- **`display osdPalette <sel> <file>` keeps the layer's current entry count.**
  It swaps the entries pointer and passes the old count to `setPalette`, so a
  256-entry file loaded over the stock 30-entry palette still has 30 entries.
  Indices 30 and up stay undefined, which is why a scale drawn with them showed
  nothing. Call the drawable's `+0x18` yourself with the count you mean.

The stock UI palette on the sub layer has 30 entries (measured, 2026-09-20).
Index 0 is transparent. Anything above the count reads past the table, which
is what a 256-cell chart photographed: scattered colours from whatever memory
follows. Do not rely on them.

## The firmware's own EL Zone scale

`FalseColorBarDrawer` (`0xC057DB98`) draws the scale from tables in MAIN:

```
0xC0D1B74C   15 x {u16 x0, u16 x1 inclusive, u32 palette index}   the bands
0xC0D1B34C   256 x {a, y, u, v}                                   its palette
```

It installs the palette through the sub layer's `+0x18` with `{table, 256, 0}`
and memsets row 464, then copies that row down to row 582. The labels are
scene `B5_9`'s `ElZoneScale` (object 33212): sign glyphs 22x25, numerals
18x25 and a 44x25 half glyph, XCI images in MAIN. `tools/build_fcscale_autorun.py`
reads all of it out of the verified image and `src/fcscale.S` redraws it;
`tools/verify_fcscale.py` runs the result in an emulator and renders it
through that palette to `builds/fcscale/scale.png`.

### Redrawn smaller, 2026-09-21

That geometry is built for the camera's own 3:2 screen, so on a 16:9 or wider
picture the bar lands across the middle of the frame. The card now rescales it
instead of copying it: `tools/build_fcscale_autorun.py` has three knobs
(`SCALE`, `CROP_BOTTOM`, `BAR_BOTTOM`, also `--scale` / `--crop-bottom` /
`--bar-bottom`) and emits the resulting `BAR_Y`, `BAR_H`, `LABEL_Y`, `CLEAR_Y`
and `CLEAR_H` into `src/fcscale_native.inc.S`, with the band boundaries and the
label positions mapped onto the narrower bar and the glyph bitmaps box-averaged
down to the smaller size. The default is 0.85 with 84 rows cropped off the bar:
a 30-row bar 870 px wide, inset 77 px, its last row at 628, and the labels 21
rows tall ending at 588, 10 rows above it.

Placement was settled by eye on the camera (2026-09-21), which is the only
oracle for it:

- the firmware's own 582 leaves 99 rows of layer under the bar and reads as the
  scale floating in the picture;
- the layer's last row, 681, sits it on the edge, but the camera draws its
  FPS/shutter/ISO strip inside rows 623..681 and the submit hook paints after
  the UI, so the scale's clear wiped that strip;
- 640 left the strip intact, so the strip begins below 640;
- 628 with the labels held at 568 is the placement that looked right. The label
  gap is its own knob (`LABEL_GAP`, `--label-gap`) because the bar and the
  labels wanted moving in opposite directions.

The bands stay contiguous by construction (each ends one pixel before the next
begins) and the verifier checks the rescaled edges carry the firmware's own
colours, that nothing is drawn outside the bar's new width, and that every
painted pixel lands inside `CLEAR_Y..CLEAR_Y+CLEAR_H`. Resized glyph pixels are
drawn at coverage 96 rather than 128, because averaging dims a one-pixel stroke
instead of removing it. **Geometry is verified offline only; it has not been on
a camera.**

### The scale showing up inside the camera menu, 2026-09-21

Reported on the camera: with the mode and the scale off, opening the camera
menu brought the scale back and disturbed the menu.

A buffer was only ever cleared when it came round again **in live view**: the
submit hook clears a buffer whose drawn flag is set once the scale is not
wanted. Taking the scale down painted the front buffer immediately
(`fc_present`) and left the layer's other two carrying it. If the camera menu
claimed one of those before live view did, the menu was drawn on top of our
rows and the scale reappeared with it.

`clear_all` now runs at the moment the scale goes off, walking the tracked
buffers (`+0x28..+0x30`), clearing the scale's rows on each with the geometry
of the last paint and cleaning those rows out of the data cache. The drawn
flags are reset with them. It takes the same `+0x6C` paint lock as the hook, so
it cannot race a frame. `tools/verify_fcscale.py` covers it: both buffers carry
the scale, one hold takes it down, and **neither buffer has a pixel left with no
further frames submitted** -- that case fails if the call is removed.

That was not the reported symptom, and the `--probe` build settled why. It
draws `UI_STATE` as a row of ticks at the top left on every frame whatever the
gate decides, so the value is counted off the screen with no USB shell. Read on
the camera, 2026-09-21:

| screen | `UI_STATE` |
| --- | --- |
| live view, scale off | 2 |
| live view, scale up | 2 |
| recording | 2 |
| camera menu | 5 |

So the gate was right all along, and **the scale in the menu was never painted
there**: it was pixels left in buffers the menu inherited. This also retires the
note that false colour has a UI state of its own -- with the mode and the scale
both on the state still reads 2.

The clear had to move from the transition to the gate: `fc_paint` now calls
`clear_all` on the **first** frame after the gate closes, so all three buffers
go at once. Clearing one buffer per frame is what left the scale visible in the
menu for about a second, until each buffer happened to be submitted again.
`verify_fcscale.py` covers it -- both buffers carrying the scale, one frame in
playback, neither buffer has a pixel left -- and that case fails if the call is
removed.

### The camera releases a key it never pressed, 2026-09-21

Opening and closing the camera menu switched the scale on by itself. Two RAM
fixes (clearing every buffer at the transition, then on the first frame the gate
closes) did not touch it, because neither was the cause.

The USB shell settled it. With the shell up and the payload armed over it
(`tools/autorun_over_usb.py`), `tools/fcstate.py` read the state block live
while the camera was operated:

    presses=1  releases=3  held_ms=21659  holds=3  mode=2  scale=1

**The camera calls the release method (`0xC0372330`, CameraIF `+0xD0`) with no
matching call to the press method.** Our release measured one of those against a
press timestamp 21 seconds old, read it as a 21,659 ms hold, and worked the
scale. A press now sets an outstanding flag and only a release that finds it set
does anything; it returns 1 on every path either way, which is what the stock
method's callers read as success.

Two things worth keeping from this:

- **`+0x2C` is not free.** The flag went there first and the bug survived,
  because `+0x28..+0x30` are the buffer slots: it read back as a buffer address,
  was never zero, and the check never fired (`presses=1` against `holds=8`). It
  lives at `+0x34` now. The offline suite had passed the bad version because no
  frame had been submitted that early in its script, so the slot was still
  zero -- the unpaired-release cases now run after the buffers are tracked, and
  a case asserts the slots hold buffer addresses.
- **Appending a payload's AutoRun to the shell's card does not work.** The
  loader becomes the worker part way through the script and the rest is never
  executed. Send the lines over the live shell instead, which also means arming
  and re-arming without a reboot: three fix attempts were tested in minutes.

Confirmed on the camera: `presses=3` against `releases=6`, `holds=1`, and the
menu no longer disturbs it.

The button is worked by duration rather than by counting presses: the press
switches the mode on (posting `0x21` immediately, so it feels instant), and the
release decides what else happens from the camera's own millisecond clock
(`0xC002B920`, `HOLD_MS` = 500). Press for the mode, hold for the mode with the
scale, hold again to take the scale down or put it back, press to turn
everything off from either state. Nothing is posted from the display submit.

## Capturing the screen

```
display capture \OSD.XCI 1 0        sel: main 1, sub 2, both 3 -- compress: none 0, RLE 1, LZ4 2
```

Header is `XC\0\0`, then payload size, width, height, a format byte and a depth
byte, then the pixels: the main layer 16-bit, the sub layer one byte per pixel.

## Bounds

Check `x + w` against the stride and `y + h` against the height before every
write, in the fill routine rather than in its callers. A chart drawn from a
fixed origin with no check froze the camera outright: if the buffer is smaller
or strided differently than you assumed, you write straight past the end of it.
