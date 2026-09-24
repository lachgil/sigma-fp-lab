# SD-loaded extension architecture

The 2026-09-11 notes below describe the older local fpSup reference. The current
experiment uses a separate pinned checkout and implements a boot-resident
registry and service table. **The registry was camera-validated on 2026-09-23.**
The separate drawing/input provider is experimental. There is no native
custom-menu API, script engine, general hook broker or hot reload.
RAM-only does not mean zero risk or guarantee recovery. No flash work.

## Loading status: boot-resident, not on demand

The present implementation loads every packaged module from the combined boot
image, allocates it and calls its initializer at startup. A small AutoRun script
does not make that work lazy. Turning a loaded feature on/off is not loading its
code from SD on demand. No boot-time improvement has been measured or claimed.

The proposed workflow, boot only a small loader and load a selected module when
a user chooses a menu item, is **not implemented**. It needs a resident SD-file
loading service, serialized load requests outside display/button callbacks,
dependency and hook-conflict handling, failure cleanup and an actual menu
integration. The existing ABI and C compilation provide reusable building
blocks, not that complete workflow. Open-gate/downsampled-mode modules have not
been integrated into this registry. Unload/hot reload are separate problems.

### C hardware-test candidate, 2026-09-25

`builds/module-c-hardware/card/` combines the working False Color module `0x102`
with C example `0x201` and the debug shell. An offline run of this exact card
confirmed both records ready, C results171 then246 with a fixed modeled clock,
the expected False Color scale pixels and debug task startup. These numeric
results are not hardware expectations: the C example also measures elapsed time.
The C example adds no visible menu/tile; its initialization and calls are checked
through the debug registry. No camera installation is implied by this build.

Reproduce the candidate after building the False Color artifact:
```sh
.venv/bin/python -B tools/build_module.py src/module_c_example.c \
  --module-id 513 --upstream builds/module-upstream \
  --out builds/module-c-hardware/example.bin \
  --dependency builds/module-fcscale-proof/card/module_fcscale.bin \
  --card-out builds/module-c-hardware/card --debug
```

## Shared module registry and services, 2026-09-23

The earlier two-blob lifetime probe has been replaced, not kept as a second ABI.
Modules no longer contain firmware allocator/cache addresses or choose cave
locations. They carry a small header and position-independent code/data.

| File | Responsibility |
| --- | --- |
| `src/module_abi.h` | Version 1 constants, serialized layouts and ARM C types |
| `src/module_runtime.S` | Registry, allocation/lifetime, validation and services |
| `src/module_probe.S` | Example using shared clock/report services and private state |
| `tools/build_module_card.py` | Package independently built modules with upstream |
| `tools/verify_modules.py` | Execute boot chain and consume the public registry |

Pinned upstream:
[`3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da`](https://github.com/ijigen/fpSup/tree/3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da).
The existing `reference/fpSup` and feature builds are unchanged. The isolated
registry debug card was installed and exercised on 2026-09-23.
The packager gives one runtime/catalog artifact to upstream's unchanged
`--boot-bin` loader. Upstream initializes the USB worker first in debug builds.
Only the runtime understands the module catalog; upstream VBIN is unchanged.

### Developer entry point for Bei

**C modules are supported.** Start with `src/module_c_example.c`; no assembly,
firmware addresses or hand-written module header are required in that source.
Install Clang and LLD (Arch: `sudo pacman -S clang lld`; Debian/Ubuntu:
`sudo apt install clang lld`). Use the pinned upstream checkout described below.

```sh
.venv/bin/python -B tools/build_module.py src/module_c_example.c \
  --module-id 513 --upstream builds/module-upstream \
  --out builds/my-c-tool.bin --card-out builds/my-c-card --debug
```

Write these two callbacks, including `module_abi.h`:
```c
int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record);
uint32_t fp_module_invoke(const struct fp_api *api,
                          const struct fp_record *record, uint32_t argument);
```

Initialization returns zero or an error. Invocation returns the tool's value;
the runtime wraps it in its status/value convention. Use `api->report`,
`api->ticks`, `api->lookup` and `api->call` for shared services.
The example demonstrates initialized and zero-initialized globals, a const
table/string, data pointers and a function pointer. The builder supplies the
header and bootstrap, links PIC ARM code, checks every dynamic relocation,
materializes zero data, and relocates pointers once before C initialization.
The runtime ABI itself is unchanged.

This is freestanding C11, one translation unit, ARM soft-float. No libc,
allocator, compiler support library, constructors, TLS, C++ or unwinder is
supplied. Missing helpers/imports fail the build instead of becoming fake stubs.
Only checked `R_ARM_RELATIVE` data/GOT relocations are supported; unsupported
sections/relocations and alignment requirements above eight bytes are rejected.
Additional source can be included into the translation unit. Repeated `-D`
sets preprocessor definitions. `--compiler`/`--linker` or `FP_MODULE_CLANG`/
`FP_MODULE_LLD` select tool paths. A local `builds/toolchain/bin/ld.lld` fallback
is supported but is not distributed in the repository; install LLD normally.

```sh
.venv/bin/python -B tools/verify_module_c.py --upstream builds/module-upstream
```

Six C scenarios passed through the real emitted ARM loader/runtime code:
two heap bases produced identical values178,260,350,448,514 after staging was
unmapped, with fresh heap memory poisoned. Repeated initialization did not
relocate pointers twice. Init failure preserved -77 and freed the failed module
while its assembly sibling remained callable. Missing callbacks and unresolved
imports were rejected before publishing binaries. All18 existing registry
scenarios still pass. Report: `builds/module-c-proof/report.json`.
**C modules have not yet been tested on the physical camera.**

The original assembly workflow remains available:
```sh
.venv/bin/python -B tools/build_module.py src/module_probe.S \
  --upstream builds/module-upstream --out builds/my-tool.bin \
  -D MODULE_ID=513 -D INITIAL_VALUE=10 -D STEP=1 \
  --card-out builds/my-card --debug
```

Use repeated `--dependency path/to/already-built.bin` to load existing modules
before the newly compiled module, in command-line order. Dependencies are not
rebuilt. Without `--card-out`, only the module binary is produced. The command
checks magic/version, exact image size, nonzero and unique IDs, entry alignment
and bounds, and capacity. It prints IDs/load order, offsets, bytes and SHA-256.
Header validation does not prove PIC correctness, hook compatibility or safety.
This command never accesses the camera or installs files on an SD card.

Smoke evidence: a module built through this CLI executed in the ARM model via
the public registry and returned 13 then16. A second independently compiled
module returned109 alongside it; duplicate IDs were rejected before output.
Use the relevant behavioral verifier before deployment. For the specialized
False Color module, use `tools/build_module_fcscale.py` instead: it also
generates the native scale assets and supplies its required firmware defines.

### Reproduce and package

Requirements: original fp 5.02 MAIN image, Clang, and the existing Unicorn Python
environment. The verifier checks the image's SHA-256 before executing.

```sh
# Create this checkout once, if it does not already exist.
git clone https://github.com/ijigen/fpSup.git builds/module-upstream
git -C builds/module-upstream checkout 3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da
.venv/bin/python tools/verify_modules.py --upstream builds/module-upstream
```

This independently builds `builds/module-proof/{a-v1,b,a-v2}.bin`, packages the
scenarios, and writes `report.json`. B is built once; replacing A repackages the
same B bytes. Runtime/loader assembly still occurs during packaging. This is
not SD directory scanning or loading a changed module without a reboot.

Package existing artifacts through the developer CLI:

```sh
.venv/bin/python tools/build_module_card.py \
  --upstream builds/module-upstream --out builds/module-card \
  --module builds/module-proof/a-v1.bin \
  --module builds/module-proof/b.bin
```

`--debug` includes upstream's USB shell for a supervised hardware experiment.
It does not enable the flash-backed settings bootstrap. The verifier creates
the debug candidate at `builds/module-proof/debug/`; it is **not a release**.

### Version 1 contract

The module header is six little-endian words: `FMOD`, ABI version, nonzero ID,
exact image byte length, initialize offset, invoke offset. Both offsets are
word-aligned ARM entries inside the image, past the 24-byte header. No ELF
relocation or external import resolution is provided; code/data must move
together without absolute self-addresses. Up to eight modules are supported.

The runtime:
1. Reserves a 16-byte directory through upstream's checked cave bump arena.
2. Allocates and copies its own resident registry/code, then publishes D/I caches.
3. Validates catalog bounds and module headers, creates ordered status records,
   and allocates/copies/publishes each compatible module into its own USER block.
4. Calls `initialize(api, record)` once. Success is zero. Failure records the
   original return code, clears callback/image pointers, frees the owned block,
   and continues with the next module.
5. Publishes the directory's runtime pointer and ready status after initialization.

All code and retained data outlive upstream's temporary staging allocation.
Duplicate IDs are rejected: the first occurrence reserves the ID even if it
failed. Failed records remain inspectable. This is failure isolation for
reported errors, not protection against arbitrary code crashing or hanging.
Modules are trusted, initialization and API calls are serialized, and successful
allocations last for the boot. There is no unload or side-effect rollback.

| Service | Contract |
| --- | --- |
| `lookup(api, id)` | First matching record, including failures; NULL if missing |
| `call(api, id, argument)` | Invoke only ready modules; status plus result |
| `report(api, id, value)` | Update the public diagnostic value during init or when ready |
| `ticks(api)` | Firmware microsecond counter, wrapping uint32 |

`module_abi.h` exposes ARM C types. The `call` result is a `uint64_t`: low 32
bits are status (`r0`), high 32 bits are value (`r1`) under ARM AAPCS.
Use `fp_call_status(result)` and `fp_call_value(result)`, checking status first.
The module callback itself returns a single uint32 value. Read-only registry
records include ID, state, image extent, callbacks, diagnostic value and original
initialization result. Modules use services rather than modifying those records.

Status values: `0` ready, `1` initializing, `-1` no memory, `-2` ABI mismatch,
`-3` malformed image/catalog, `-4` duplicate ID, `-5` init failed, `-6` unknown ID,
`-7` not ready. A module failure does not make the whole directory fail.

### Executed evidence

Eighteen scenarios passed using emitted ARM instructions for the upstream loader,
runtime, service dispatch and modules. File I/O, USER allocation, cache routines,
clock and task creation/start are modeled. Staging and failed allocations are
poisoned and **unmapped**, so dangling references fault instead of accidentally
working. Discovery uses the public directory/API, not allocator introspection.

- Baseline: A returns 13 then 16; B returns 109 then 118, with argument 2.
- Rebuild only A: A returns 1005 then 1010; unchanged B still returns 109 then 118.
- Reverse order; refuse either module allocation; refuse the runtime allocation.
- Reject duplicate IDs, including reuse after a failed initializer.
- Reject module ABI mismatch and incompatible service-table version.
- Failed init retains `-77`, frees its image and leaves the sibling callable.
- Reject invalid callback, short header and overflowing catalog entry extent.
- Eight modules work; host over-capacity and corrupted runtime count are rejected.
- Debug boot creates/starts the modeled shell task and leaves both modules callable.
  **The USB worker loop and USB transport are not exercised.**

A throwaway 124-byte Clang-compiled ARM C consumer also executed against the
actual service table after staging was freed: A=13, B=109, missing ID=-6.
This checked the C/AAPCS return contract, not just header compilation.

### Registry hardware evidence, 2026-09-23

The original Sigma fp 5.02 booted `builds/module-proof/debug/`. Its debug USB
shell answered, and the directory at `0xC072EC60` published a ready runtime with
both records ready. Actual calls through the resident service table advanced
module A's diagnostic from 10 to 13 to 16 and module B's from 100 to 109.
An unknown ID returned `-6`; the shared microsecond-clock service returned
143102581. USB remained responsive after those calls.

The previous card boot files were backed up to
`builds/card-backup-20260923-165245/` before replacement. No footage was touched.
Those older FP LAB files are experimental, not a stock recovery image.

For the isolated boot the runtime is the first cave-arena consumer, so the
directory occupies `0xC072EC60..0xC072EC6F`: magic `FPRD`, runtime pointer,
resident byte count and status. Discover API/record addresses from the current
boot's pointer; heap addresses from a previous boot are not reusable.
The user has authorized relevant memory reads without per-address prompts.
Never send shell commands while recording. A spare SD card is optional:
preserve media and back up any boot files before changing the current card.

This establishes the registry's real allocation, boot lifetime, service calls
and debug transport on that boot. It does not establish recording safety,
concurrent API calls, UI hooks or native menus. Catalog checks trust the declared
total length; upstream supplies no independent input-buffer length. This is not
a sandbox or a safe parser for untrusted/truncated card files. Do not combine
experimental cards or overwrite occupied code/caves.

Separately, direct fp 5.02 disassembly identifies `ctrl mediaIn`'s handler at
`0xC040BF78`, loading its filename argument and calling `0xC03DA758` at
`0xC040BFA0`. AutoRun calls the same reader at `0xC03DA420`. The reader's setup
opens a named file and registers callbacks through `0xC0420CC8`. That is a
concrete native script-input lead, **not** a hardware-tested synchronous script
API or permission to run arbitrary scripts while recording.

## Optional drawing and custom-button provider, 2026-09-24

This adds a real module-to-module dependency without changing the 36-byte
runtime API or 48-byte registry records:

| File | Responsibility |
| --- | --- |
| `src/module_ui.h` | Versioned six-word request protocol and limitations |
| `src/module_ui.S` | Provider ID `0x100`, canvas, buffer ownership and firmware hooks |
| `src/module_ui_demo.S` | Independent consumer ID `0x101`, draws call/button counters |
| `tools/verify_module_ui.py` | Execute ARM modules, hook wrappers and bounded LCD scenarios |

```sh
.venv/bin/python tools/verify_module_ui.py --upstream builds/module-upstream
```

Outputs are `builds/module-ui-proof/module_ui.bin`, `module_ui_demo.bin`,
`card/{AutoRun.txt,fpSup.BIN}`, `report.json` and the rendered `demo.ppm`.
The card includes the debug shell. Both modules initialize passively: **booting
this candidate does not install UI hooks or display the tile**.

Load the provider before the demo. Invoke demo ID `0x101` through the runtime's
`call(api, id, argument)`: argument `0` draws the next call count and latest
observed button count, then requests presentation; argument `1` requests hide.
Successful update returns the wrapping call count, hide returns zero. Check
the runtime dispatch status separately from the module's returned result.
Counters are polled when the demo is invoked; a physical press alone does not
rerun the demo or redraw its number.

Other consumers use provider ID `0x100` with an aligned pointer to
`struct fp_ui_request { op, a, b, c, d, e }`. QUERY returns service version 1;
FILL clips a packed-16-bit rectangle into the shared 96x32 canvas; PRESENT
enables it; HIDE disables it; BUTTONS reads the wrapping press count; STATUS
reads the last render/installation error. The tile is at LCD `(464,560)`.
Use white `0xFFFF` or black `0xF000` until color-channel order is confirmed.
This is one shared canvas, not separate per-consumer surfaces.

The provider owns the firmware-specific code. PRESENT checks stock prologues
before installing two checked cave veneers: display submit `0xC02E8A08` and
assigned FalseColor custom-function press `0xC03722E8`. The press hook observes
and forwards unchanged; release is not hooked. **This is not general key input
or a replacement for the camera's FalseColor behavior.** An occupied site or
exhausted cave fails without partial installation. No flash or settings writes.

Rendering uses a separate 43,136-byte USER allocation, keeping the code artifact
within upstream's fixed 32-KiB packed-binary limit without modifying upstream.
Frames allocate nothing and call no consumer code. A nonblocking lock protects
the canvas and three tracked buffer identities. One FILL is atomic against
rendering; a sequence of FILL calls is not a drawing transaction.

### UI evidence and remaining hardware limits

Thirteen scenarios passed with emitted ARM code through the unchanged boot loader
and registry, then through both actual patched entry wrappers. They check exact
tile writes and untouched surrounding pixels across three buffers, hide and
native pixel changes, unsigned clipping, menu/playback and surface gates,
fourth-buffer/changed-identity refusal, stock argument/return preservation,
hook conflict and cave exhaustion, and the independent demo's changed pixels.
Allocation refusal leaves the provider/dependent unavailable without hooks;
malformed owned storage is freed before initialization failure. The existing
18 registry scenarios also pass, and the ARM C request layout compiles.
The rendered preview was inspected: `C 2`, `B 3`. Provider code is 2,072 bytes,
the independent demo is 984 bytes, and the debug card remains 32,768 bytes.

Firmware stock continuations, allocation and cache effects are modeled in the
offline verifier. Subsequent hardware testing on the fp 5.02 confirmed both
modules ready, their code matching the built artifacts, and successful demo
invocation through the registry. All three 1024x682 buffers were marked painted
with service error zero; the user confirmed the physical `C 1 / B 0` tile.
After using the assigned FalseColor button, the user confirmed normal native
behavior. The provider recorded five press callbacks; a second demo invocation
succeeded and drew call count 2 with observed count 5, again with error zero.
The user also confirmed the updated physical tile shows `C 2 / B 5`.
Five callbacks are not evidence of five distinct physical presses.
USB remained responsive.
Capture: `builds/module-ui-proof/hardware-first-show.json`.

PRESENT/HIDE wait for the next eligible native display submission and do not
force a refresh. Only main-layer format 1, width 1024, height 592..2048 is
eligible; drawing requires live-view state 2. Hide/menu restoration uses only
the currently submitted buffer, never cached framebuffer pointers.

Restoration preserves native pixels that differ from the last overlay value.
A native write identical to that value is indistinguishable. A painted buffer
whose exact identity never returns cannot be restored by this policy; a tile
can therefore remain visible after HIDE until a matching submission. Hardware
buffer lifetime/address reuse, forced-refresh behavior, scheduling under load,
hide/menu transitions and recording interactions remain unverified. This boot
demonstrates basic rendering/cache visibility, not general cache correctness.
Hooks and allocations persist
until reboot. This is a bounded experimental drawing service, not a general
compositor, autonomous UI loop, native menu API or release-ready camera plugin.

## Autonomous False Color module card, 2026-09-24

`src/module_fcscale.S` wraps the existing `fcscale.S` toggle/scale implementation
as ABI1 module ID `0x102`. Its initializer automatically installs three guarded
press/release/display hooks through 24 bytes of checked cave veneers. Code,
native scale data and state remain in the module's USER allocation. It loads
alone with the registry and debug shell, not alongside the competing UI provider
or counter demo. This is a working tool packaged as a module, not yet a migration
of its renderer to the separate generic UI service.

Build and execute offline proof:
```sh
.venv/bin/python -B tools/verify_module_fcscale.py \
  --upstream builds/module-upstream --out builds/module-fcscale-proof
```

Seven scenario groups pass: automatic boot/lifetime, press/hold/release and
native pixel drawing, display gates/lock, state transitions/clock wrap,
stock/cave conflict refusal, allocation failure/relocation and exact cave
capacity. The existing standalone verifier also passes 35 checks. Module size
is 3,916 bytes; native-scale preview inspected. After installation, the user
confirmed the tool working on camera without host activation. This establishes
basic autonomous tool operation through the module loader. The confirmation
does not establish exhaustive menu-transition or recording safety; modeled USB
task startup is still not a transport test of this card.

Installed `builds/module-fcscale-proof/card/{AutoRun.txt,fpSup.BIN}` to SD UUID
`1749-D0A5`, verified byte-for-byte, synced, unmounted and powered off reader.
Previous UI boot files: `builds/card-backup-20260924-164139/`. Media untouched.
Installed fpSup.BIN SHA-256:
`4edee9525e875bd5ea436fa08430441db2f2e9172bf3196bb3c1a55cfe22b6d3`.

Cold boot with a battery removal to clear old RAM hooks/veneers. With a button
assigned to False Color: short press toggles mode, hold for at least 500 ms
controls the scale. No host activation is required. The existing renderer's
buffer-clearing assumptions remain experimental; recording safety is untested.

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
