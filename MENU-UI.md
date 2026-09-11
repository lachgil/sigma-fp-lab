# On-camera menu research

Reviewed 2026-09-11. **No functioning menu, renderer or integrated controller is
implemented.** The previous `src/menu_keyhook.S` draft was withdrawn, not shipped:
it used placeholder keys/state addresses, had no real sequence timeout or idle
interlock, silently discarded unimplemented feature requests, and rewrote live
instructions without a verified cache-maintenance path. Assembling it was not
proof of a usable feature.

## Established static evidence

At `0xC091EA30`, the observer-related record includes:

| Address | Value | Role supported by local disassembly |
| --- | --- | --- |
| `0xC091EA34` | `0xC0269B50` | Deleting destructor, not a key callback |
| `0xC091EA38` | `0xC0265800` | Candidate key handler pointer |
| `0xC091EA3C` | `0xC0726FB8` | Associated thread/base reference |
| `0xC091EA40` | `0xC091EA4C` | HybridKeyEventObserver name pointer |

`0xC0265800` begins `push {r4,r5,lr}; mov r5,r0; mov r4,r1`, examines IDs 6 and
109 specially, and returns 1. This supports r0=this and r1=event ID. It does
**not** prove that returning 1 consumes an event, that every key reaches this
handler, or that overwriting the descriptor pointer changes a live dispatcher.
`0xC0269B50` tests a deletion flag and conditionally frees its object. Do not
intercept that address to implement menu input.

The lower-level `KeyPressedMonitoring` candidate `0xC02DD6A8` reads GPIO and
debounce state. A shell key-injection command at `0xC03F5DC0` is not evidence
of an input interception API. Preserve normal camera controls during diagnostics.

## Rendering leads, not APIs

- The existing fpSup AutoRun uses the firmware `display text` / `display osd`
  commands for boot banners. That is not a persistent navigable menu.
- `0xC03E4620` is an OSD builder lead requiring its real context/ABI.
- `0xC0528300` and `XC_OnceDrawingObserver` are drawing-observer leads; a safe
  registration/lifetime contract has not been established.
- No verified per-frame display hook exists to host a menu repaint. The rejected
  green geometry-copy proposal cannot be used as if it were that hook.

## Prerequisites before implementing the menu

1. Observe dispatch into the candidate handler with real physical keys. Capture
   ID, press/release/repeat meaning, calling context and native event propagation.
2. Resolve return-value/consumption semantics and a safe open/close gesture with
   measured timeout units. No placeholder key IDs.
3. Establish a renderer context, coordinate system, repaint lifecycle and a clean
   unregister path. Verify LCD behavior on the actual camera.
4. Allocate code/state through one loader with overlap checks. Verify installed
   firmware/payload and cache coherency; do not assume an unused-looking cave is free.
5. Establish an authoritative recording-idle interlock before feature writes.
   Keep requested state separate from applied state; errors must remain visible.
6. Implement only verified feature operations, with complete restore paths. Do not
   clear unsupported requests as if applied. Profile122 gain affects stock
   FHD/29.97 too and must be restored explicitly.

For now, `toggle_opengate.py` is the host-side experimental control, not a menu.
It requires the cold-boot-installed matching hook and changes its ARMED data flag
rather than replacing live instructions. It still relies on the operator keeping
the camera idle. Hardware state changes must be supervised.

## CONFIRMED LIVE button->id map (2026-09-11, hardware)
Captured on the actual camera via a live descriptor-swap logger at 0xC091EA38
(-> logger at 0xC072E400 -> ring at 0xC072FB00), which proved the dispatcher does
NOT cache the handler pointer (option A works; no inline hook / cache flush needed).
Handler 0xC0265800, ABI r0=this, r1=keyid. Each physical tap logs press then
release; **release id = press id + 1**.

| Button | press id | release id |
|---|---|---|
| UP (rear) | 0x14 | 0x15 |
| DOWN | 0x18 | 0x19 |
| LEFT | 0x10 | 0x11 |
| RIGHT | 0x0C | 0x0D |
| OK / center | 0x1C | 0x1D |
| TONE (chosen menu-open key) | 0x2F | 0x30 |

Menu design: open on TONE press (0x2F); while open, consume UP/DOWN (navigate),
OK (apply), TONE again or a timeout (close); pass everything else through by
tail-calling 0xC0265800 so the camera behaves normally when the menu is closed.
This replaces the earlier placeholder key ids in the withdrawn draft.

## PROVEN end-to-end (2026-09-11, hardware): resident button action
Installed a resident handler at cave 0xC072E440 via the descriptor swap and had
it, on TONE press (0x2F), flip the whole open-gate patch-set. Single-press result,
read back live: ARMED 1->0, picker7 0x75->0x6A, VMAX 0x41C70->0x40888, RWZM
0x400->0x640. So: intercept a physical button -> run our code -> change camera
state WORKS. This is the menu's core mechanism, confirmed.

Two confirmed constraints for the real menu:
1. RETURN VALUE DOES NOT CONSUME. Returning r0=1 does not stop the native action:
   pressing TONE still opens the camera's native Tone menu (a separate observer).
   A real menu must either use a trigger with no strong native action, mask the
   event earlier in the pipeline, or accept/hide the native panel. The per-key
   choke return is not a global consume.
2. Geometry-latched features (open gate, mode swaps) need a mode re-latch to show
   in a take; a toggle alone does not change an already-latched recording.

Also observed: TONE opens the native Tone menu on first press and closes it on the
next, so while that panel is open, keys may route to it rather than the global
observer. Trigger choice matters.

## PROVEN: resident on-camera text draw (2026-09-11, hardware)
Resident code CAN draw OSD text by calling the "display text" SUB-HANDLER
directly, bypassing the command dispatcher:
- **Draw:** 0xC03E4620(r0=ctx, r1=1, r2=argv) where argv[0]=message string, and
  ctx is a fake: a pointer to a word holding the address of a `bx lr` stub (the
  handler only uses [ctx] as a printf, so a no-op stub is safe). Confirmed on the
  LCD ("RESIDENT2" drew).
- **Do NOT** call the display DISPATCHER 0xC03E5510 from resident context: its
  sub-command name lookup (0xC03DB840 over table 0xC0BB1410) fails there and it
  no-ops. Call the leaf sub-handler directly.
- 0xC0698D80 (used inside 0xC03E4620) IGNORES its arg and returns a fixed display
  singleton (0xC3824F98 family), so the fake ctx is fine for the render too.
- **Composite:** after drawing, the OSD must be composited to show; the shell does
  `display osd 1`. From resident code, call the "osd" sub-handler the same way, or
  keep a shell composite during tethered testing.
- **Clear:** `display text` does NOT erase what it overwrites (leftover chars show,
  e.g. "RESIDENT2off"). Pad each menu line to a fixed width with trailing spaces,
  or fill the OSD (osd color 0) before drawing.
Text draws at a fixed top-left position; multi-line needs the lower renderer
0xC0529CB8 with a y offset. A single cycling status line works with 0xC03E4620.

## Proven two-item prototype (2026-09-11, hardware)
Single-press RIGHT/UP menu confirmed pleasant on the camera:
- Trigger keys must be NATIVE-FREE. On this body only RIGHT (0x0C) and UP (0x14)
  are free. RIGHT cycles the item, UP toggles it on/off. (OK/Tone have native
  actions that fight the menu - do not use them.)
- Draw: 0xC03E4620(fakectx,1,[str]) then composite 0xC03E3D00(fakectx,1,["1"]).
- Four text+composite passes per action fixed the observed three-press delay.
  Multi-buffering was a hypothesis, not a measured cause.
- Prototype state was 0xC072EF00 and code 0xC072E700. These conflict with the
  gyro layout and are not used by the combined card.
- Live prototype descriptor swap was 0xC091EA38 to 0xC072E700.

## Combined boot card (2026-09-11, offline verified)

`src/payloads/menu.S` is now the combined controller. Build:
`.venv/bin/python build_combined_card.py`; execute the packaged ARM:
`.venv/bin/python emulate_menu.py`. Installation is in README.md.

- `builds/combined-menu/{AutoRun.txt,VSHL.BIN}`: 32 KiB each, 16 VBIN sections.
  Synchronous upstream echo loader, no USB shell or endpoint patches.
- Downloaded gyro_og_test gyro sections match the current gcsv source exactly,
  including the 10552-byte pool writer. The builder pins their aggregate hash.
- Absolute entry 0xC072F000 dispatches to menu code at pool+0x50000; gyro remains
  at its original addresses/pool+0x44000. Menu state is 0xC072FB00:
  cursor/+4 OG/+8 HFR/+12 gyro/+16 gyro-ready/+20 init-status/+32 saved level.
  Init-status 1 means installed; 2 means a firmware guard refused installation.
- Row hook 0xC072F800 and armed flag 0xC072FA10 retain selector175 isolation.
- Gyro initializes once. Between idle takes the menu restores/arms all four
  gyro instructions, flushes caches and restores/applies orientation behavior.
  Buffers remain allocated. No pointer-only pretend-disable.
- Stock clears OG/HFR/gyro; Gyro-Gate converges OG and gyro to the same state,
  leaving HFR independent. All features start off. RIGHT selects, UP toggles.
- Checks native recording/file-busy flags and gyro WANT/file/thread/mailbox/
  block ownership before a change. This is not an atomic RTOS transaction;
  concurrent start/toggle behavior remains a hardware risk.
- Firmware guards cover 17 stock words before gyro initialization. All eight
  sample allocations and the text allocation must exist before gyro is offered.
- Actual stage2, trampoline, gyro boot and menu machine code passed emulation:
  selection/toggles, mixed Gyro-Gate, Stock restoration, busy refusal, native
  passthrough, allocation failures and firmware mismatch. LCD calls preserve
  eight-byte stack alignment and repeat four times.

Cold-boot display, real GCSV/JSON writes and recordings in this combined card
are not yet hardware-verified. No camera writes were performed for this build.

## M98 options and the selector probe (2026-09-11, offline)

M98 is 3032x2012 12-bit like M117, but hmax 445 / vmax 2094 (77.27 fps table,
12.44 ms readout) against M117's 330 / 2184 (99.9 fps, 9.22 ms). Its timing
entry is **0xC0B59548**, stock `0x0004082E`; the table is `0xC0B59500 + n*0x20
+ 8`, which is the same relation that puts M117's at the long-known 0xC0B59A28.
VMAX scales inversely with rate, exactly as the open-gate patch already does
(M117 2184 -> 7280 for 29.97):

| option | picker cell | value | M98 timing entry | rate | throughput |
|---|---|---|---|---|---|
| M98 30P | FHD 29.97 (0xC0BE5888/5A28/5BC8) | 0x62 | 0x00041516 (5398) | 29.97 | ~274 MB/s |
| M98 60P | FHD 59.94 (0xC0BE5858/59F8/5B98) | 0x62 | 0x00040A8A (2698) | 59.94 | ~548 MB/s |

The 59.94 FieldAngle selector is NOT derivable offline. The hook site's caller
(0xC043A158) uses r5 as an index into per-format tables (`ldr r1, [0xC0BD05D4 +
r5*4]`), and the picker records interleave framerates within 0x30-byte structs,
so the measured 175 (29.97) and 180 (FHD/25) do not sit on a stride that can be
extrapolated. Guessing would rewrite geometry for whatever mode really owns that
selector, which is the corruption this hook exists to prevent.

So selectors became data: `SELS` at 0xC072FA20 holds the 29.97 slot and the
59.94 slot, a zero slot is off, and the hook rewrites only on a match. A probe
at 0xC072FA30 records the selector of every 1936x1090 row **whether armed or
not**, which is what makes an unknown framerate measurable with nothing
repointed. `M98 60P` refuses while its slot is unset and draws the probe value
in hex, so the number can be read off the LCD on a card that has no USB shell.
Bake it with `build_combined_card.py --og60-sel <value>`.

The RWZM unity cells and the hook's ARMED flag are now recomputed from all
three geometry flags (`apply_shared`), because switching one feature off must
not take cells another still needs -- the failure the emulator now pins.

Unverified beyond emulation: whether M98 images acceptably, whether the
59.94 path needs its own RWZM/profile cells (only the profile-122 pair is
known), and whether either M98 rate sustains to storage.
