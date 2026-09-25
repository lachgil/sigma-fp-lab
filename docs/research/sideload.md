# SD-loaded extension architecture (older research)

This file holds the earlier SD-loading reverse-engineering: the upstream boot
mechanism, the decoded FP3K reference loader, the components a combined extension
would need, and the recovery/flash boundary. RAM-only does not mean zero risk or
guaranteed recovery. No flash work.

The current module system that grew out of this research now lives in its own
docs:

- `module-platform.md`: the boot-resident module registry, shared services, the
  drawing/input provider and the False Color module, with offline and on-camera
  evidence.
- `c-modules.md`: writing fp modules in C, with a minimal walkthrough.

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
5. RAM-only does not imply switch-off clears it. See the warm-start findings below.

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

## Warm-start investigation (2026-09-25)

Sources and evidence must be kept separate:

- User: the C native toggle works on the camera. Bei reports a roughly
  half-second load; its implementation has not been established by this work.
- [Upstream warm-restart findings, commit 11ae41d](https://github.com/ijigen/fpSup/blob/11ae41df562399d4f8d8c7cd9da975eae268ceee/HOOKS_AT_POWER_OFF.md):
  three measured restarts retained the patched firmware image/cave, cleared
  firmware BSS and reset allocator ownership. This is upstream hardware evidence,
  not a new test on our camera, nor a guarantee for every power-loss condition.
- Our offline experiment reproduced the consequence for the earlier C toggle:
  retain image/cave, discard the previous heap, then dispatch its press method.
  The emitted ARM branches into the old allocation and faults on instruction
  fetch. Reloading the module instead refuses the old hook with init result
  `-8` (registry status `-5`), leaving that old hook in place.

Historical reproduction script: `builds/boot-lifecycle-investigation.py`.
It requires the earlier artifact, not the now-fixed build. Saved evidence:
`builds/boot-lifecycle-report.json`. Module SHA-256:
`3adf685b70cbd94b7129ca9153953e92fab001d900b3afe082b0785ffb189176`.
The modeled fault was `0x4511621c`, within the previous `0x45116000..0x451163d4`
module allocation. This proves a dangling-target path under the modeled lifetime
split, not that a particular camera boot necessarily dispatches that method.

**Implemented offline:** the shared runtime now owns hook transactions and a
cave-resident shutdown callback registered in both PowerOffMgr lists. It revokes
heap roots, restores owned entry words and checks retained cave ownership before
the next load. Sixteen lifecycle scenarios pass, including cleanup after heap
unmapping and a warm reload at a different allocation base. Cold-boot success
still is not hardware warm-restart validation.
See [the current contract](module-platform.md#shared-hook-and-shutdown-contract).

### Low-risk speed comparison

The pinned upstream already supports `FPSUP_NO_BAR=1`. Building the same C toggle
with that environment value reduced executable AutoRun commands from 103 to 82,
and display commands from 31 to 10. All non-display commands and `fpSup.BIN`
were byte-identical. The resulting package booted in the ARM model and posted
native events `0x21`, `0x22` on alternating presses with no release event.

Local package/report: `builds/loader-research-no-bar/`. This is a script-work
reduction, **not a measured camera-time saving**. The final banner remains.
Neither this package nor the warm-start experiment was installed on the card.

### Settings storage is not an execution trigger

Fast Start's existing `store_boot.S` checks the build tag at `0xC3075264`, copies
the cached loader from the flash-backed CommonSave area into the cave, publishes
caches, then branches. AutoRun still has to call that bootstrap through `echo`.
The builder bounds the cached body to 468 bytes, not an extra general-purpose
module heap. No new settings slot has been claimed or written here.

The tag identifies a loader build; it is not a checksum recomputed over the
stored body. A retained-code bootstrap would need to establish both byte
integrity and current-boot ownership before executing anything.

Do not extend the DRAM-retention timer as a shortcut to owned memory. The
upstream `--retain-ram` option explicitly keeps self-refresh powered longer,
costing battery, and it cannot prevent the allocator from reassigning a buffer.

### Native startup entry candidates

Targeted disassembly of the verified 5.02 image, followed by execution of the
stock ARM dispatch with modeled subsystem/file-reader boundaries, establishes:

1. Startup calls `0xC0020B08` between log strings `S File` and `E File`, then
   calls `0xC0020B70` at `0xC0021190`.
2. `0xC0020B70` calls shell initialization `0xC03DA178`, then `0xC03DA440`.
3. `0xC03DA440` initializes the AutoRun observer at `0xC3756940`, obtains the
   subject for selector 1, registers the observer, and **immediately calls**
   its vtable slot `+0xC`. That slot is `0xC03DA3F8`, not only an eventual callback.
4. `0xC03DA3F8` tests the bitwise intersection of subject-state bytes `+0` and
   `+3`. Only a nonzero intersection calls `0xC03DA758` at `0xC03DA420`, with
   `r0 = "\\AutoRun.txt"` and `r1 = 1`.

The offline run exercised all four 0/1 input combinations; only `(1,1)` reached
the reader. Actual SD readiness semantics and time since power-on were not
modeled or measured. Reproduction:
`.venv/bin/python -B builds/startup-dispatch-investigation.py`.
Evidence: `builds/startup-dispatch-report.json`, `builds/startup-disassembly.txt`.

**Best first experiment:** a wholly cave-resident, marker-only hook at the
initialization boundary, compared with the reader boundary. Preserve the native
gate, original behavior and return convention; do no file I/O or allocation in
the first trial. This can establish execution order across warm boots without
ever trusting a previous heap pointer. No marker was installed on hardware.

There are two different goals: entering before the AutoRun **script is parsed**,
and entering materially earlier in the camera's boot. The native reader boundary
is a concrete candidate for bypassing script bootstrap work on warm boots; it
is not proof of the second goal. `S File`/`E File` labels alone do not prove the
card is ready for arbitrary loader I/O at the earlier initialization site.
Any retained loader needs fresh allocations, an explicit card opt-in, invalid-code
fallback, and a coordinated cave map. Cold boot would still need AutoRun unless
an independent persistent trigger is found.

The CommonSave load routine `0xC00222E8` copies `0x410` bytes through
`0xC0014DAC`, using data obtained at `0xC00224F0`. That is evidence of loading
settings, not execution of the cached loader. A complete indirect-consumer audit
is still open. Our SQLite map cannot settle it: `arefs` excludes BSS addresses,
and `calls` is a BL-word scan, not a complete code or indirect-call graph.

### Shared shutdown registration edge case

The actual native routine `0xC0024118(mgr, object, forced)` scans ten slots in
each selected list (`mgr+0x0C` ordinary, `mgr+0x34` forced). An offline execution
with an empty ordinary list and full forced list returned `1`, then `0`, leaving
the ordinary registration installed. Both results matter; partial registration
does not permit freeing or reusing the callback object.

At upstream commit `11ae41d`,
[`s_poff` lines 1499-1515](https://github.com/ijigen/fpSup/blob/11ae41df562399d4f8d8c7cd9da975eae268ceee/gyro/writer_core.inc.S#L1499-L1515)
saves the ordinary result in `r5`, calls forced registration, then returns `r5`
without checking that second result. That is a source-confirmed edge case, not
an observed upstream camera failure. Do not copy it into our shared lifecycle.

The current runtime checks both results and keeps partially registered callback
storage valid and inert. It never frees code merely because entry hooks have
been detached: an already-entered callback may still be executing.

### Isolated startup marker probe

`src/boot_probe.S` and `tools/build_boot_probe.py` implement the marker-only
experiment. It owns the **entire** `0xC072EC60..0xC072EFB4` arena and must not be
combined with registry modules or any other cave user. It does not implement an
earlier loader, perform early file I/O, alter settings or extend RAM retention.

```sh
.venv/bin/python -B tools/verify_boot_probe.py \
  --upstream builds/module-upstream --out builds/boot-probe
```

This produces separate `builds/boot-probe/install/` and
`builds/boot-probe/remove/` cards, each containing `AutoRun.txt` and `fpSup.BIN`.
The default cards have no USB shell. For supervised inspection, build separately:

```sh
.venv/bin/python -B tools/build_boot_probe.py \
  --upstream builds/module-upstream --mode install --debug \
  --out builds/boot-probe-debug/install
.venv/bin/python -B tools/build_boot_probe.py \
  --upstream builds/module-upstream --mode remove --debug \
  --out builds/boot-probe-debug/remove
```

The optional upstream shell has its own heap allocation and is outside the
marker-only verification. A banner is not proof that installation succeeded:
upstream stage2 does not display the probe entry's result.

The probe instruments BL callsites `0xC0020B78` (observer initialization) and
`0xC03DA420` (the gated reader). It preserves registers, flags and incoming stack
alignment, then forwards to the original native target. Four uint32 markers at
`0xC072ED60` are `init_visits`, `boot_sequence`, `reader_visits`, `last_sequence`.
The sequence counts initialization visits, not proven physical boots.

First installation occurs after those native startup boundaries have already
run, so zero counters are expected then. A same-build warm reinstall verifies
the exact retained code and hook pair without resetting counters. Unknown,
partially installed or altered images are refused without mutation. Removal
restores only known owned calls, including a known partial removal, and leaves
the cave intact so an in-flight reader can return safely. Reinstallation after
removal requires a genuine cold boot.

The ARM verifier executes both native dispatch boundaries, all four readiness
inputs, callbacks with incoming SP modulo 8 of 0 and 4, all IRQ/FIQ masks, twenty
ownership/refusal/removal cases, retained warm installation and cleanup after
temporary heap unmapping. Report: `builds/boot-probe/report.json`.

Hardware procedure, still unperformed: begin with a clean power removal
(including USB), use only the isolated probe card, inspect the hooks and marker
words read-only, then compare after ordinary warm restarts. Use the matching
removal card and inspect restored stock calls before changing experiments.
No physical boot timing, recording safety, retention or cache-coherency claim
follows from the offline results.
