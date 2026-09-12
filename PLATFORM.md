# Running our own code on the fp

The goal here is not more sensor modes. Sigma has already optimised that space,
and the community is covering it. The goal is to make the camera **programmable**
so that better tools -- histograms, false colour, waveform, sensor telemetry --
can be written by people who are not reverse engineers.

This file is the honest inventory: what is proven, what is missing, and what the
next person should pick up. Firmware Ver.5.02, original fp. Everything is RAM
only, loaded from the card at boot; a battery pull returns the camera to stock.

## What already works

These are running on hardware today, not proposals.

| capability | how | address |
|---|---|---|
| run our code at boot | `AutoRun.txt` + `VSHL.BIN` from the card | entry `0xC072E064` |
| intercept every key | publish our handler | `0xC091EA38` (stock `0xC0265800`) |
| draw text on screen | firmware's own text/OSD calls | `0xC03E4620`, `0xC03E3D00` |
| a resident thread | the firmware's thread pool | `XT_CREATE 0xC036E108` |
| change any of ~195 settings | fetch object, call property fn | `0xC0057AE8` + `analysis/menu_setters.json` |
| hook record geometry | our gated row patch | see `GREEN-HOOK.md` |
| rename a native menu row | scene `drawText` key | `menu_row.py` |
| retarget a native menu row | scene `controlAppState` | `menu_row.py` |
| read a native toggle | a real menu item's value byte | `0xC31ADA36` |
| ask the camera its state | `imager mode_now`, `gui geti <name>`, `tsd temp` | shell |
| read the camera's own logs | 37 channels, already running | `log act/out` |

So "run custom code" is **done**. What is missing is everything a useful tool
needs to see.

## The missing piece: access to the image

Neither a histogram nor false colour can be written without the frame. Nothing
in the list above reaches pixel data, and that is the single blocker.

Two routes are open, both found but neither finished:

**1. The QR reader already does it.** `qr lv_read` decodes a QR code from live
view, so the firmware has a supported path to the LV frame:

    qr subcommand table   0xC0BC1660, stride 12 {name, help, handler}
    lv_read handler       0xC0407048
      -> 0xC0370CF8       (prepare)
      -> 0xC0406E78       (the read loop; config block at 0xC0BC1588)
           vtable calls at obj+0x88 (capture), obj+0x98 (configure)

Everything past `0xC0406E78` is vtable dispatch, so the buffer belongs to an
object that has to be walked **on the camera, in one routine** -- a host-side
pointer walk across separate `mem get` calls has already produced a convincing
wrong answer once.

`qr dump_yuv` and `qr dump_y8` exist beside it and write frames out, which is
the cheapest way to learn the format before touching any of the above.

**2. The monitor path has its own geometry.** While chasing the green live view
we found candidate per-mode scaler arrays for the monitor that nothing we patch
touches (`0xC0BD9B5C`, `0xC0BDA024`, `0xC0BE1D00`, and a 75-entry neighbourhood
at `0xC0B36938`/`0xC0B369C0`). Whatever consumes those sees the display frame.

## What a module ABI should look like

Once the frame is reachable, the useful shape is small. A module is a `.BIN`
with a header the loader already understands, and it asks for what it wants:

    on_key(code)            -> handled?     the key hook we already own
    on_frame(buffer, w, h)  -> void         needs the piece above
    draw_text(x, y, str)    -> void         0xC03E4620, proven
    get(name)               -> value        `gui geti`-style named state
    set_property(fn, value) -> void         analysis/menu_setters.json

The point of writing it down: a histogram is then about forty lines, and the
person writing it does not need to know any of this file.

## For anyone picking this up

- **Acknowledgement is not effect.** This camera returns `OK` for writes that do
  nothing, in at least four distinct subsystems. Verify by read-back, by the
  camera's own log, or with your eyes on the screen -- never by the return value.
- **Two stores.** `menu <Setter>` reads a mirror. The value a menu item edits and
  the value a subsystem reads are not always the same word.
- **Measure before believing a rule.** Several "state machines" here turned out
  to be invalid input being ignored.
- The tables that make this tractable are dumped in `analysis/`:
  `menu_setters.json`, `sensor_modes.json`, `MAIN_c0000000.bin`.
