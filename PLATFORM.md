# Running our own code on the fp

The goal is firmware extensibility: native controls, custom functions, image
analysis and sensor information. More sensor-mode experiments are not the current
priority. This is a scope decision, not a claim that Sigma exhausted the hardware.

Original fp, firmware 5.02. Evidence below distinguishes earlier camera observations,
upstream reports, offline instruction traces and proposals. RAM patches are not a
firmware flash. A battery pull clears RAM, but an installed AutoRun card can apply
its payload again on the next boot.

## Existing building blocks and limits

| Capability | Evidence and boundary |
|---|---|
| Card-loaded code | Existing AutoRun/VSHL payloads; not a general module loader or SDK. |
| Key hook | Published handler pointer `C091EA38`, stock handler `C0265800`. Existing RIGHT/UP controls remain. |
| On-screen text | Existing payload invokes shell-style handlers `C03E4620` and `C03E3D00`. These are not established `draw_text(x,y,string)` APIs. |
| Resident work | Firmware thread-pool creation at `C036E108`; existing gyro implementation demonstrates the pattern. Task context and ownership still matter. |
| Property changes | Several `menu` properties have been exercised. `analysis/menu_setters.json` contains candidates, not a verified callable API for every entry. |
| Native captions | NBU `drawText` key replacement was visually confirmed. Changing English.nloc strings alone had no observed menu effect. |
| Native destinations | Zebra Right/OK could enter False Color, but Menu/back then failed. Experiment restored; not a safe general remapping API. |
| Native toggle | Slideshow Repeat Yes/No tracked byte `C31ADA36` on the tested boot. Payload polls its edges on key events, not continuously. Real slideshow behavior is not disabled. |

## Prefer the existing detection feed for a first histogram

Ijigen's fpSup reports a **320x240, linear 8-bit grayscale detection channel** at
`C375D8C0`, already transferred through hook-push. Our subsequent hardware sample
below independently confirms grayscale image access, at **320x180** in the tested
camera state, not a universally fixed 320x240 raster:

- [fpRemote source](https://github.com/ijigen/fpSup/blob/main/projects/fp-remote.md)
- [raw-sup source](https://github.com/ijigen/fpSup/blob/main/projects/raw-sup.md)
- Local copies: `reference/fpSup/projects/fp-remote.md` and `raw-sup.md`.

Our offline trace independently resolves the descriptor address:

```text
C0436590()                          -> image manager C375D840
C0436F00(manager, channel_index)    -> manager + 0x18 + 0x34*index
channel_index = 2                  -> descriptor C375D8C0
```

`C0436F00` does not bounds-check its index. Constructor `C0436608` creates four
0x34-byte entries. `C0436538` writes each entry's first word from its buffer
argument, copies 44 bytes of source description into entry+4, then stores a
channel flag at +0x30. **C375D8C0 is a descriptor, not the address of pixel zero.**
Its geometry layout is different from the QR descriptor below; do not reuse that
layout by address similarity.

This feed is the preferred first candidate for a luminance histogram, not proof
of raw-sensor linearity, clipping, transfer function or RGB histogram fidelity.
False-color exposure thresholds need calibration to the actual image path. The
upstream timing figures describe transfer work, not sensor frame rate.

### First local hardware image sample, 2026-09-14

After USB reconnection, read-only shell access returned this slot-2 descriptor:

```text
4483C340 00000140 000000B4 00000000 00000000 00000140 000000B4
00000003 00000004 00012C00 00000001 00000000 00000020
```

The first word pointed to `4483C340`; width/height words were 320 and 180.
Reading 57,600 bytes and interpreting them as row-major 8-bit grayscale produced
a recognizable scene image. Descriptor word +24 was 76,800; it must not be used
as the active width*height byte count. Its exact allocation/layout role remains
unresolved. The camera reported MONIT1_60 standby, sensor size 2016x1344.

Those artefacts (the frame, a host-rendered plot, and the descriptor with all
256 bins) stay in the local `analysis/` folder, which this repository does not
publish: it is the same folder the firmware image lives in.

Transfer used `mem get` in checked, ordered 1,024-byte chunks. It took about
1.01 seconds; the descriptor was identical before and after. **This is a rolling
live-buffer sample, not a guaranteed coherent single frame.** An unchanged
descriptor does not prevent pixels from changing during the read.

The histogram accounts for all 57,600 sampled pixels. 62.4% have code 255 in this
bright sample; this is not a measurement of raw-sensor clipping. The plot is
rendered on the host, not an overlay installed on the camera. No memory writes,
code injection, card writes or recording-mode changes were made. Shell ping still
responded after acquisition.

## QR path: concrete access trace, not a frame lease API

The QR implementation provides another route and useful ownership evidence.
All findings in this section are offline disassembly unless stated otherwise.

### Control and scheduling

```text
qr lv_read                         C0407048
  object accessor                  C0370CF8 -> C0371730 -> C351FE88
  constructor                      C03716F0 installs vtable C0B9928C
  shell reader                     C0406E78
    register observer              slot +98 -> C0372898 -> C03A0C68
    enable                         slot +84 -> C0372740, message 1A
    request                        slot +88 -> C0372790, message 1B
    poll completion                at most 20 sleep calls, argument 50
    disable                        slot +84, argument 0
    unregister observer            slot +9C -> C03728B8 -> C03A0D20
```

Both message wrappers put byte 1 at message+4. `C03A0798` copies and queues the
request and returns without waiting for completion. Slot +88 returns a truncated
status value, **not a frame pointer**. The first dispatch table is verified:
`C0B9E1D4` routes 1A to `C03A2040`, and `C0B9E1E4` routes 1B to `C03A2098`.
Both forward to a second queue through `C03A9580`.

**One dispatch link remains unresolved:** `C038ACA8` uses initialized RAM table
`C2F1F5A0`. Entries for 1A/1B are `C2F1F740`/`C2F1F750`. Same-offset bytes in the
extracted MAIN file are not valid final table records. Do not infer their contents
from adjacent handlers. Candidate concrete state handlers `C03ABE38`/`C03ABE60`
are referenced together in 13 state vtables and implement the matching lifecycle
and trigger contracts, but the RAM table must establish their selection.

The concrete QR worker is independently traced:

```text
QR object C3588D30
  C0381588                         enable under QR mutex +4
  C03815C0                         reset decoder and disable under same mutex
  C0381600                         attach work when enabled
  C036EFE8                         thread loop invokes work vtable +0C
  C0381460                         QR work body, holds QR mutex
    C0423C48 -> C0423E48            require producer state == 3
    C0423E28 -> C0429560            obtain borrowed image descriptor
    C02E9278                       shallow descriptor copy
    C05C4878 -> C05C48A0            grayscale preparation and decode
    C03A0C40                       notify observers before unlocking
```

### Producer descriptor and ring selection

`C0429560` rebuilds static descriptor `C375C094`. It returns an empty descriptor
for producer states 0, 1 and 5; the QR caller separately requires state 3. Otherwise
it scans driver channels 0..2 for the first metadata record whose first word is 0.
No matching channel produces an empty descriptor.

Thumb function `C01D7638` refreshes ring indices through MMIO accessor `C01822B8`
and copies a 0x80-byte metadata record from `C30255B4 + 0x80*channel`. The getter
uses record+0x0C/+0x10 as dimensions, record+0x7C as the selected index, and the
word-addressed buffer vector at record+0x24. `C0022FB8` converts a selected word
address to `(word_address << 2) + 0x40000000` using 32-bit arithmetic, after
asserting the word address is below 0x40000000.

No buffer reservation, reference increment or release is present in this getter.
Its static descriptor is overwritten on another call. A shallow copy stabilizes
the description, not the pixels. Neither the QR mutex nor a fast memcpy has been
shown to prevent the producer from reusing a frame during consumption.

### QR descriptor layout and byte counts

Analytical field names, not recovered SDK types:

| Offset | Input descriptor field |
|---|---|
| +00, +04 | Raster width, height |
| +08, +0C | Crop origin x, y |
| +10, +14 | Crop width, height |
| +18 | Unknown |
| +1C | CPU data pointer |
| +20 | Unknown |
| +24 | One-byte layout flag; +25..27 are not copied by the copy helper |
| +28 | Total-size override, not a stride |
| +2C | Auxiliary field; getter copies producer+54 for channel 0 only |

`C02DA6E0` returns the source dump size, with firmware uint32 arithmetic:

```text
layout_flag != 0:  ((width + 15) >> 4) * ((height + 7) >> 3) << 7
otherwise:         size_override if nonzero, else 2 * width * height
```

The flag branch suggests 16x8-block storage, but does not establish tile traversal
or compression. The default size does not prove YUYV, UYVY, chroma ordering or a
universal row stride. Do not write a host YUV decoder from this formula alone.

The grayscale descriptor is 0x24 bytes: six geometry words, +18 zero, +1C allocated
pixel pointer, +20 mode 1. `C02DA750` computes its byte count as width*height. The
allocation and decoder arguments support tightly packed one-byte grayscale;
orientation, range and pixel content remain unverified here.

### Conversion, dump and lifetime

`C03C7E60` only constructs a descriptor. It does not convert pixels.
`C05C48A0` allocates grayscale storage through `C001D800` and invokes conversion
through `C04278B0 -> Thumb C01B5C88`. The conversion uses the **original input
descriptor**, not the locally crop-adjusted copy used for output-size selection.
The 0x120-byte low-level parameter block contains encoded word addresses, source
raster/crop dimensions, output dimensions and layout/mode fields. The low-level
path configures shared converter state; it is not a CPU luminance-extraction loop.
Hardware completion and cache-coherency contracts remain unresolved.

The grayscale pointer is used by the decoder and dump calls, then freed through
`C001D8F8` at `C05C49E0`. It must not be retained by a custom asynchronous consumer.
The runtime output-dimension cap at `C2F2D8E8` also cannot be read as final data from
same-offset MAIN bytes. No fixed QR output dimensions are established.

`qr dump_yuv on` / `qr dump_y8 on` set bytes `C37CE91A` / `C37CE91B`. Their parser
accepts exactly one argument equal to `on`; other forms clear the flag. These are
persistent flags, not capture commands. The decode path consults them and calls
`C05C3F98(path, data, byte_count)` before decoding. Generated names have the form
`\QR_LOG\LOG_dddd.YUV` / `.Y8`. The helper opens with firmware flags 0x406,
attempts one raw write and closes. Selected medium, collision/overwrite semantics,
short-write handling below this layer and durability are not established. **No
dump commands were run in this investigation.**

### Observer ownership is not image ownership

Registration, unregistration and observer invocation use the same service+0x38
lock. Unregister clears all matching active slots; returning from it excludes an
in-flight callback under the inferred mutex semantics. Merely submitting disable
does not drain either queue or prove that a worker finished.

The callback receives a temporary notification and runs while QR, notification and
observer-list locks are held. Keep it bounded; do not synchronously reenter QR
work or registration without proving lock recursion. Copy needed results before
returning. The observer's QR result pointer is not a pixel-buffer ownership token.

## Reproducible offline checks

```sh
.venv/bin/python frame_access_probe.py
```

This executes original firmware instructions in Unicorn with a SHA-256 identity
guard. It covers concrete vtable dispatch, successful/timeout shell cleanup,
completion-event filtering, producer states and channel selection, word-address
translation, detection-slot indexing, and dump-size precedence/block rounding.
Services, allocation, sleep, output, memory helpers and the hardware index accessor
are stubbed; metadata is synthetic. **Passing is not hardware capture, converter
execution, scheduling or concurrent-buffer proof.** The script imports no camera
transport and cannot deploy a payload.

For instruction inspection, use `.venv/bin/python af_dis.py dis ADDRESS COUNT`;
append `t` for Thumb. Relevant anchors are listed above. The probe checks MAIN hash
`92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4`.

## Frame ownership: DMA only, no lease

The detection path was traced to its producer and consumers. There is **no
software mutex, no buffer flip and no completion signal** in it. Manager
`C375D840` holds four 0x34-byte entries; the source object at `[mgr+0x14]` was
read live as `C375EB68`, vtable `C0BD0318`, and its accessors `C0437E98`,
`C0437ED8`, `C0437F20` are all non-blocking lookups. Entry `+0x30` is a channel
type mask (detection `0x20`), not a ready bit, and `+0x20` is the allocation
size: detection is allocated `0x12C00` (320x240) while only 320x180 is active.

So the descriptor pointer is stable while the ISP keeps overwriting the pixels
behind it. That is exactly why the earlier one-second host read was incoherent.
A guaranteed single frame needs a copy taken on the display task's frame
cadence; the true write-complete interrupt is upstream and was not identified.

## A live overlay drawn by our own code, 2026-09-14

`src/payloads/hist_overlay.S` runs inside the camera, RAM only, deployed by
`hist_deploy.py` over the USB shell:

```sh
.venv/bin/python hist_deploy.py place    # payload into the pool, surfaces measured
.venv/bin/python hist_deploy.py once     # a single pass, to check before going live
.venv/bin/python hist_deploy.py start    # the resident thread
.venv/bin/python hist_deploy.py stop     # ask it to exit, then its code can be replaced
```

**Read this before being impressed: the camera's built-in histogram is better,
and the fp already has false colour.** This is not a better exposure tool. What
it demonstrates is the thing the camera does not otherwise offer: our own code
reading the image and drawing arbitrary pixels on screen, on its own thread.
That is the foundation for tools the fp lacks; the histogram is only the
smallest honest proof that every link in the chain works.

How it works. The payload bins the detection feed and paints bars straight into
the OSD layer's buffers. It never fills the layer and never presents. The
surface contract came from the shell itself: `display osd 1` prints
`addr 0x%08x (%04d,%04d)`, reporting three rotating buffers (`0xC66A9A00`,
`0xC6754200`, `0xC65FF200`) at 1024x682. Buffer spacing is exactly 1024*682, and
drawn glyph pixels read back as `0xB2`, so the layer is **one byte per pixel with
the surface width as its stride**.

Two ways in, both implemented. With no override the payload asks the drawable
for its backbuffer (`C03E5698`, `C03E56D8`, vtable `+0x10`, base at `+4`,
geometry at `+8`) and the host presents afterwards. With the buffer addresses
supplied it paints **all three directly and calls no firmware drawing code at
all**, which is what the resident thread uses: no display lock, so it cannot
deadlock against the camera's own UI. Everything is range-checked and a bad
value produces a status code, not a wild write. Each pass ends on `C000E91C`.

The thread is the firmware's own pool thread (`XT_CREATE` at priority 20,
`XT_ATTACH`), looping on `tk_dly_tsk` until a host-set stop flag lets it return
cleanly. Measured 5.5 to 12 passes per second painting all three buffers.

**Painting one buffer was a real bug, not a theory:** the plot vanished the
moment the camera's UI presented a different buffer. Only the UI decides which
one is on screen, so a resident pass has to paint all of them.

Verified run: pixels `0x4483C340`, 320x180, 57,600 samples, status 1, and
reading the presented buffer back, **all 256 bar heights equal the payload's own
bins** with the baseline row present.

![The histogram on the camera's own screen](docs/osd_histogram.png)

That is a read-back of the buffer the camera presented, not a mock-up. The
payload's full state and bins stay in the unpublished `analysis/` folder.

Honest limits: the detection feed has no frame lock, so a plot can straddle two
frames. The values are display-path grayscale codes, not calibrated raw
clipping. A mode change wipes the layer. There is no menu entry and no card
build, so it needs the USB shell and a debug card, and a battery pull removes
it entirely.

## Next

1. Copy the feed on the display task's frame boundary so a plot comes from one
   frame, instead of relying on a fast pass over a buffer nothing locks.
2. Check the feed's dimensions across camera states: upstream reports 320x240
   where this camera showed 320x180.
3. Give the overlay a card build and a menu entry, so it does not need a host.
4. Only then build a tool worth having, calibrated against what the camera's own
   display and exposure tools already show.

A module ABI remains a design proposal. Before exporting frame callbacks it needs
format/stride metadata, ownership and release rules, task-context restrictions and
failure behavior. Native menu return navigation also needs a real fix. Pixel
access is not the only missing piece, and a stable plugin SDK has not been built.
