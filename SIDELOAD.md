# SD-loaded extension architecture

Reviewed 2026-09-11. This describes a direction based on fpSup's existing loader.
**Our integrated Stage-3 loader, resident controller and custom menu do not exist
yet.** RAM-only does not mean zero risk or guarantee recovery. No flash work.

## Existing upstream mechanism

The camera's shell reads `AutoRun.txt` at boot. The pinned fpSup gyro base build
uses a small loader near `0xC072DE64`, a USER pool allocation through
`memmgr bufmem get 0 1048576`, and compiled `VSHL.BIN` sections. Its stage2 calls
cache-maintenance entry `0xC000E91C` before executing copied code.

Relevant upstream files, under local `reference/fpSup`:
- `fp_usb_shell/templates/loader.S`
- `fp_usb_shell/templates/stage2.S`
- `fp_usb_shell/build_autorun.py`
- `gyro/build_base_card.py`

VBIN uses a magic/count/entry/data-size header, destination/length records and
section data. Upstream distinguishes pool-relative and absolute destinations.
Use its actual packer/loader contract, not this summary as a new file-format spec.
The gyro and USB-shell boot mechanisms are not interchangeable by filename alone.

## CONFIRMED reference: FP3K 0.3.5 loader (decoded 2026-09-11)

FP3K 0.3.5 (community "preview display-fit" tool) is a working `*.BIN` loader we
disassembled offline. It is the cleanest concrete pattern for "one blob instead
of hundreds of `mem set` lines", though FP3K itself is DISPLAY-only (it never
changes the recording mode), which is why it does not affect sensor output. We do
not redistribute `FP3KMENU.BIN` (third-party binary); this is our RE of its shape.

Boot flow (from its `AutoRun.txt` + the blob):
1. AutoRun writes a ~0x2C0-byte loader stub at `0xC0732100` via `mem set`.
2. The stub opens the card file `\FP3KMENU.BIN` and reads it VERBATIM to
   `0xC072DE64` (raw code image; base is fixed, no relocation header).
3. Bootstrap trigger: it hijacks the echo handler pointer `0xC0BAC2F8` -> the
   stub, issues `echo`, then restores `0xC0BAC2F8` to `0xC03D99A0`. (Same one-shot
   handler-hijack fpSup's gyro uses.)
4. The blob entry self-checksums: it sums `0xF41` words (0xF41*4 = 15620 = the
   whole file) from `0xC072DE64`; if the sum is nonzero it aborts. So the image is
   padded to a **sum-zero** 32-bit checksum. It then masks interrupts and installs
   its hooks.
5. Revert is a power cycle (RAM only).

In-blob hook table (installed by the entry), 16 trampolines of the form
`{target, stock_first_word, branch_into_blob}`:
- Display/menu targets include `0xC0437E98` (the green display accessor; stock
  `0xE92D4030`), plus 15 others (menu/OSD, some Thumb). NONE touch picker/VMAX/RWZM
  or the record path, hence "recording mode unchanged".
- Each trampoline replaces the target's first word with `B <blob handler>` and the
  handler replays the displaced instruction and returns to `target+4`.

State block at `0xC0732000` (its ARMED-equivalent flag is `+0x10`; MENU/BOOT
debug regions saved from `0xC0732000`/`0xC0732500`). The green handler lives at
blob `0xC0731790` and is byte-faithful to our `build_greenfix` handler EXCEPT it
reads that FP3K flag at `0xC0732000+0x10` instead of our `0xC072FA10`. Same objects
`0xC375EB68`/`0xC375ED3C`, sub-object `0xC375E934`, same FNV-1a fingerprint gate.

To make a blob that DOES change recording mode, add our sensor half (picker slot
id + mode-117/target VMAX + RWZM) and the record-geometry hook to the payload;
FP3K omits all of that by design. Memory: FP3K uses `0xC0732100` (stub) and
`0xC072DE64..0xC0731B68` (blob) and `0xC0732000` (state) - these OVERLAP fpSup's
gyro loader region and our `0xC072F800` record cave sits inside the blob span, so
a combined blob must own one coherent map, not layer FP3K over our caves.

## Components required for our combined extension

- One loader/allocator with a complete ownership and section-overlap map.
- A version/payload guard before hooks are armed; local MAIN hash verification
  alone does not verify the camera into which a card is inserted.
- A controller with requested/applied states and an authoritative recording-idle
  interlock, checked writes and failure/restore handling.
- Existing selector-gated record hook, integrated only with compatible code/state.
- Green: the STANDBY canvas fix exists (accessor 0xC0437E98, FP3K-verified,
  `build_greenfix`); the RECORD monitor still needs a record-start buffer hook,
  not the per-frame 0xC04376E0 accessor. The old rec-to-live copy proposal is
  withdrawn. See GREEN-HOOK.md and FIRMWARE-DECODE.txt section 2.
- Real key dispatch and rendering lifecycle from MENU-UI.md. Candidate event
  handler `0xC0265800`; **not destructor `0xC0269B50`**. No menu assembly remains
  deployable in this repository.

## Known reserved areas and hazards

The gated record payload occupies `0xC072F800..0xC072F908`, with telemetry at
`0xC072FA00..0xC072FA10` and ARMED at `0xC072FA10` (end `0xC072FA14`).
USB helper operations such as getfile/putfile/inject/callfn can reuse this area.
Do not use them while that hook is active. The shell worker, gyro producer and
loader occupy additional nearby regions; inspect the exact built layout before
adding code. Pool offsets from different fpSup versions cannot be mixed safely.

Do not replace live instructions using mem set plus readback as a substitute for
instruction-cache maintenance. The reviewed host toggle leaves the verified
cold-boot branch installed and changes only data. This is not an integrated
resident state controller and does not solve recording-time concurrency.

## Recovery and flash boundary

Remove boot payloads and fully power-cycle to stop reloading them. This has been
an effective recovery method in earlier sessions, not a guarantee against every
hardware/state/storage failure. RAM changes can still spoil files or affect
firmware operations that persist settings.

Checksums and lack of recognizable crypto strings do not prove a modified update
will be accepted, boot correctly or recover. Earlier authentication stages and
recovery mechanisms have not been mapped. A replacement image is out of scope;
no flash tool or zero-brick-risk claim is provided.
