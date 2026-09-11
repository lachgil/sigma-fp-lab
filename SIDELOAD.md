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
