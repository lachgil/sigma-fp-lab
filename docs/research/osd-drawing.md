# Drawing your own pixels on the LCD

SIGMA fp, firmware Ver.5.02.

This is how to get arbitrary graphics onto the screen from your own code: where
the buffer comes from, what format it is, and what the palette is. The colour
chart photograph came from about forty instructions using this.

## Getting a buffer

```
0xC03E5698   take the display handle
0xC03E56D8   handle -> drawable object
[drawable]   its vtable; vtable +0x10 is getBackbuffer, called with the drawable
```

`getBackbuffer` returns a descriptor:

```
+0x04   the buffer base
+0x08   -> geometry: +0x00 width (also the stride), +0x04 height
```

On the LCD that is **1024 x 682, one byte per pixel**, so a pixel is
`base + y * stride + x` and the byte is a palette index.

Three things that are not obvious and cost time:

- **Call this from key context.** It works from a key handler, which is where
  the camera's own function keys run. Called from an AutoRun at boot it returns
  nothing, because the display is not up yet.
- **The layer rotates three buffers.** Only the UI decides which is live, so
  remember every base you are handed and paint all of them. Painting one makes
  the overlay flicker or vanish as the UI presents another.
- **Repaint.** The UI paints over whatever it finds, so a resident thread that
  redraws every 150 ms is what makes graphics stay up. Take the display lock
  only to learn the buffers, not on every paint.

## The palette

The camera will dump it for you, from an AutoRun or the shell:

```
display monitor 0 1
display palette 2 1 \PAL.BIN        sel: main 1, sub 2, both 3 -- isRgb: yuv 0, rgb 1
```

On the LCD's 8-bit layer that returns **120 bytes, thirty entries, A,R,G,B**:

| Index | Colour | | Index | Colour |
|---|---|---|---|---|
| 0 | transparent | | 5 | `1D7FEC` blue |
| 1 | `FF0000` red | | 6 | black, alpha `BB` |
| 2 | black | | 7 | `440000` dark red |
| 3 | `4DD67F` green | | 8-15 | empty |
| 4 | `95AC96` grey | | 16-29 | teal to pale grey, rising alpha |

**Only 0 to 29 exist.** Anything above that reads past the table into whatever
memory follows, which is why a chart of all 256 indices comes out mostly
transparent with scattered colour, and why index `0xB2` draws red. Those
colours are not yours and should not be relied on.

The camera also loads a palette, which is the way to get colours it does not
already have:

```
display osdPalette 1 \FPLAB.PAL
```

The file is the same layout, A,R,G,B per entry. Keep entries 0 to 29 identical
to the dump and nothing the camera's own UI draws changes colour.

## Capturing the screen

```
display capture \OSD.XCI 1 0        sel: main 1, sub 2, both 3 -- compress: none 0, RLE 1, LZ4 2
```

Header is `XC\0\0`, then payload size, width, height, a format byte and a depth
byte, then the pixels. The LCD gives two files: a 16-bit main layer and the
8-bit sub layer, `1024 * 682` bytes, which is the one you draw into. Useful for
reading back exactly what your code put on screen without photographing it.

## Bounds

Check `x + w` against the stride and `y + h` against the height before every
write, in the fill routine rather than in its callers. A chart drawn from a
fixed origin with no check froze the camera outright: if the buffer is smaller
or strided differently than you assumed, you write straight past the end of it.

## Where this is used here

`src/fcscale.S` is a complete, self-contained example: it takes the buffers
from a key handler, runs a 150 ms repaint thread, carries a 5x7 font, and draws
a stop scale across the bottom of live view. It is built into a single AutoRun
by `tools/build_fcscale_autorun.py`, with no menu, no key hook of its own and
nothing else resident.
