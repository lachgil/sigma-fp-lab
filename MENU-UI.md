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
