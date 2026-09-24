# The fp module platform

A way to put multiple tools on a Sigma fp from one SD card, allocate their
resident images, and let them find and call each other through a service table.
Modules can be written in [C](c-modules.md) or ARM assembly. Hardware-facing
modules still need firmware bindings and must avoid competing for the same hooks;
the runtime does not arbitrate hook ownership.

**Status:** the registry was camera-validated on 2026-09-23, a compiled C example
on 2026-09-25, and an autonomous False Color tool loads as a module and works on
the camera. The drawing/input provider is experimental. There is no on-demand
loading, native menu API, script engine, hot reload or flash work yet. RAM-only
does not mean zero risk or guaranteed recovery.

## Why this system

Most community fp boot tools are a single fixed blob that patches the firmware
directly (the FP3K loader in `sideload.md` is the clearest example: one image,
display-only, addresses baked in). That works for one feature but does not
compose: two such tools cannot easily share a card, share code, or be built by
different people without colliding.

This platform is structured differently:

- **Many modules, one card.** Up to eight modules load from a single boot image,
  each in its own memory, each with a stable ID.
- **A shared service table.** Modules reach the camera and each other through a
  small versioned API (`lookup`, `call`, `report`, `ticks`), not by copying
  firmware addresses into every tool.
- **Position independent.** A module runs wherever the camera allocates it, so
  there are no per-build address maps to maintain.
- **Modules call modules.** One tool can use another's service by ID (`api->call`),
  so a drawing provider or math helper written once is reusable by everyone.
- **C or assembly.** Contributors who know C do not need to learn ARM.
- **Checked builds and failure isolation.** Bad headers, duplicate IDs and
  undefined calls are rejected before deployment; a module whose init fails is
  recorded and skipped while its siblings keep working.

That is a genuinely different shape from a single-purpose blob: a small platform
others can build on, rather than one hard-coded feature. It is early and trusted
(a crashing module can still hang the camera), not a finished product.

## Map

| File | Role |
| --- | --- |
| `src/module_abi.h` | The ABI: header layout, callbacks, services, C types |
| `src/module_runtime.S` | Loader/registry: allocation, validation, services |
| `src/module_probe.S` | Minimal assembly example using services + private state |
| `src/module_c_example.c` | C example (see `c-modules.md`) |
| `src/module_ui.{h,S}` | Experimental drawing + button-observe provider |
| `src/module_ui_demo.S` | Consumer that draws counters via the provider |
| `src/module_fcscale.S` | False Color tool packaged as a module |
| [`src/module_fclatch.c`](../../src/module_fclatch.c) | C native False Color on/off toggle, no scale |
| `tools/build_module.py` | Build one C or assembly module, optionally a card |
| `tools/build_module_card.py` | Package prebuilt modules into a boot card |
| `tools/verify_modules.py` | Execute the boot chain and consume the registry |
| `tools/verify_module_c.py` | Execute compiled C modules |
| `tools/verify_module_ui.py` | Execute the drawing/input provider |
| `tools/verify_module_fcscale.py` | Execute the False Color module |
| `tools/verify_module_fclatch.py` | Execute C native toggle and hook-conflict scenarios |

Pinned upstream:
[`3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da`](https://github.com/ijigen/fpSup/tree/3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da).
The existing `reference/fpSup` and feature builds are unchanged. The packager
gives one runtime/catalog artifact to upstream's unchanged `--boot-bin` loader;
only the runtime understands the module catalog, and upstream VBIN is unchanged.

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

## Version 1 contract

The module header is six little-endian words: `FMOD`, ABI version, nonzero ID,
exact image byte length, initialize offset, invoke offset. Both offsets are
word-aligned ARM entries inside the image, past the 24-byte header. No ELF
relocation or external import resolution is provided at the ABI level; code/data
must move together without absolute self-addresses. Up to eight modules load.

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

## Building and packaging

Requirements: original fp 5.02 MAIN image, Clang, and the existing Unicorn Python
environment. Verifiers check the image's SHA-256 before executing.

```sh
# Create the pinned upstream checkout once, if it does not already exist.
git clone https://github.com/ijigen/fpSup.git builds/module-upstream
git -C builds/module-upstream checkout 3a87d8238fbb89e2836d5b2df6f4d70fbd82c7da

# Build one module (C or .S) and optionally a bootable debug card:
.venv/bin/python -B tools/build_module.py src/module_c_example.c \
  --module-id 0x201 --upstream builds/module-upstream \
  --out builds/my.bin --card-out builds/my-card --debug

# Package already-built modules into one card, in load order:
.venv/bin/python tools/build_module_card.py \
  --upstream builds/module-upstream --out builds/module-card \
  --module builds/a.bin --module builds/b.bin
```

`build_module.py` checks magic/version, exact image size, nonzero and unique IDs,
entry alignment and bounds, and capacity, and prints IDs/load order, offsets,
bytes and SHA-256. `--dependency prebuilt.bin` loads existing modules before the
new one. Header validation does not prove hook compatibility or runtime safety;
run the relevant verifier before deployment. These commands never touch the
camera or write an SD card.

`--debug` includes upstream's USB shell for a supervised hardware experiment.
It does not enable the flash-backed settings bootstrap and is not a release.

## Executed evidence

### Registry, offline

Eighteen scenarios pass using emitted ARM instructions for the upstream loader,
runtime, service dispatch and modules. File I/O, USER allocation, cache routines,
clock and task creation/start are modeled. Staging and failed allocations are
poisoned and **unmapped**, so dangling references fault instead of silently
working. Discovery uses the public directory/API, not allocator introspection.

- Baseline: A returns 13 then 16; B returns 109 then 118 (argument 2).
- Rebuild only A: A returns 1005 then 1010; unchanged B still returns 109/118.
- Reverse order; refuse either module allocation; refuse the runtime allocation.
- Reject duplicate IDs, including reuse after a failed initializer.
- Reject module ABI mismatch and incompatible service-table version.
- Failed init retains `-77`, frees its image, leaves the sibling callable.
- Reject invalid callback, short header and overflowing catalog entry extent.
- Eight modules work; host over-capacity and corrupted runtime count are rejected.
- Debug boot creates/starts the modeled shell task; both modules stay callable.
  **The USB worker loop and USB transport are not exercised offline.**

A 124-byte Clang-compiled ARM C consumer also executed against the actual service
table after staging was freed: A=13, B=109, missing ID=-6, checking the C/AAPCS
return contract, not just header compilation.

### Registry, camera (2026-09-23)

The original Sigma fp 5.02 booted `builds/module-proof/debug/`. Its debug USB
shell answered; the directory at `0xC072EC60` published a ready runtime with both
records ready. Live calls through the resident service table advanced module A's
diagnostic 10 -> 13 -> 16 and module B's to 109; an unknown ID returned `-6`; the
shared clock returned 143102581. USB stayed responsive. Prior boot files were
backed up before replacement; no footage was touched. Discover API/record
addresses from the current boot's directory pointer; a previous boot's heap
addresses are not reusable.

This established real allocation, boot lifetime, service calls and debug
transport. It did not establish recording safety, concurrent API calls, UI hooks
or native menus. Catalog checks trust the declared total length; this is not a
sandbox or a safe parser for untrusted/truncated card files.

## Drawing and custom-button provider (experimental)

`module_ui.{h,S}` add a real module-to-module dependency without changing the
36-byte runtime API or 48-byte records. Provider ID `0x100` owns a shared 96x32
canvas at LCD `(464,560)`; consumers send a six-word request
`{op,a,b,c,d,e}`: QUERY (version 1), FILL (clipped 16-bit rectangle), PRESENT,
HIDE, BUTTONS (wrapping press count), STATUS (last error). `module_ui_demo.S`
(ID `0x101`) draws call/button counters through it.

The provider owns the firmware-specific code. PRESENT checks stock prologues
before installing two checked cave veneers: display submit `0xC02E8A08` and the
assigned FalseColor custom-function press `0xC03722E8`. The press hook observes
and forwards unchanged; release is not hooked. **This is not general key input or
a replacement for the camera's FalseColor behavior.** Rendering uses a separate
43,136-byte USER allocation, keeping the code artifact within upstream's fixed
32-KiB packed-binary limit. Frames allocate nothing and call no consumer code; a
nonblocking lock protects the canvas and three tracked buffer identities.

```sh
.venv/bin/python tools/verify_module_ui.py --upstream builds/module-upstream
```

Thirteen offline scenarios pass (exact tile writes, untouched surrounding pixels
across three buffers, hide and native-pixel changes, clipping, menu/playback and
surface gates, fourth-buffer/identity refusal, argument/return preservation, hook
conflict, cave exhaustion, and the demo's changed pixels). On the fp 5.02 the
user confirmed the physical `C 1 / B 0` tile, normal FalseColor button behavior
after use, and an updated `C 2 / B 5` tile after a second invocation. Capture:
`builds/module-ui-proof/hardware-first-show.json`.

Limits: PRESENT/HIDE wait for the next eligible native submission (main layer,
format 1, width 1024, height 592..2048, live-view state 2); they do not force a
refresh. Restoration preserves native pixels that differ from the last overlay
value; a native write equal to it is indistinguishable, and a painted buffer
whose exact identity never returns cannot be restored. Buffer lifetime/reuse,
refresh behavior, scheduling under load, hide/menu transitions and recording
interactions are unverified. Hooks and allocations persist until reboot. This is
a bounded experimental drawing service, not a compositor or native menu API.

## Autonomous False Color module

`src/module_fcscale.S` wraps the existing `fcscale.S` toggle/scale as ABI1 module
`0x102`. Its initializer installs three guarded press/release/display hooks
through 24 bytes of checked cave veneers; code, native scale data and state live
in the module's USER allocation. It needs no generic UI provider and has also
run alongside the C example.

```sh
.venv/bin/python -B tools/verify_module_fcscale.py \
  --upstream builds/module-upstream --out builds/module-fcscale-proof
```

Seven scenario groups pass (automatic boot/lifetime, press/hold/release and
native pixel drawing, display gates/lock, state transitions/clock wrap,
stock/cave conflict refusal, allocation failure/relocation, exact cave capacity);
the standalone verifier also passes 35 checks. Module size 3,916 bytes. On the
camera the user confirmed the tool working with no host activation: short press
toggles the mode, hold about 500 ms controls the scale. This establishes basic
autonomous tool operation through the loader, not exhaustive menu-transition or
recording safety. This packages an existing tool as a module; it has not been
migrated onto the generic drawing provider.

### Plain native toggle in C

[`module_fclatch.c`](../../src/module_fclatch.c), module `0x103`, ports the plain
native on/off latch to C. It installs two guarded hooks and uses 16 cave bytes,
with no display hook or custom renderer. Do not combine it with `0x102` or an
active UI provider button hook. Its 980-byte image passes eight offline ARM
scenarios and was confirmed working on the camera on 2026-09-25. See the
[C tool walkthrough](c-modules.md#a-real-tool-native-false-color-toggle) for
build, verification, installation, and behavior.

## See also

- `c-modules.md` for the C workflow and a minimal walkthrough.
- `sideload.md` for the older SD-loading research, the FP3K reference loader, and
  the recovery/flash boundary.
