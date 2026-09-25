# The fp module platform

A way to put multiple tools on a Sigma fp from one SD card, allocate their
resident images, and let them find and call each other through a service table.
Modules can be written in [C](c-modules.md) or ARM assembly. Hardware-facing
modules still need firmware bindings. The runtime now arbitrates their ARM entry
hooks and restores owned patches at shutdown.

**Status:** the registry, C support and False Color behavior were camera-tested
before this lifecycle revision. The shared shutdown implementation dated
2026-09-26 is **offline-verified, not yet camera-validated**. The drawing/input
provider is experimental. There is no on-demand loading, native menu API, script
engine, hot reload or flash work. RAM-only is not a zero-risk guarantee.

## Why this system

The fixed-address FP3K loader documented in `sideload.md` is a concrete example
of a single-purpose image. Combining independently written patches also requires
memory ownership, hook-conflict handling and shutdown coordination; merely
placing their bytes on one card does not provide those contracts.

This platform is structured differently:

- **Many modules, one card.** Up to eight modules load from a single boot image,
  each in its own memory, each with a stable ID.
- **A shared service table.** Modules reach the camera and each other through a
  small versioned API (`lookup`, `call`, `report`, `ticks`, `install_hooks`).
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
| `src/module_hooks.S` | Shared hook transactions and cave-resident shutdown cleanup |
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
| `tools/verify_module_hooks.py` | Shutdown, rollback, ownership and warm-reload regression scenarios |

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
1. Reserves a 468-byte shared cave block containing the directory, cleanup and
   eight hook records. Registers cleanup in both native shutdown lists before
   allocating the resident runtime; both registrations must succeed.
2. Allocates and copies its own resident registry/code, then publishes D/I caches.
3. Validates catalog bounds and module headers, creates ordered status records,
   and allocates/copies/publishes each compatible module into its own USER block.
4. Calls `initialize(api, record)` once. A failure records its result, detaches
   owned hooks and clears callable fields. An image that never exposed hooks is
   freed; one that exposed hooks stays allocated until reset because a callback
   may still be in flight. Its retired veneers also remain intact.
5. Publishes the directory's runtime pointer and ready status after initialization.

All code and retained data outlive upstream's temporary staging allocation.
Duplicate IDs are rejected: the first occurrence reserves the ID even if it
failed. Failed records remain inspectable. This is failure isolation for
reported errors, not protection against arbitrary code crashing or hanging.
Modules are trusted, initialization and API calls are serialized, and successful
allocations last for the boot. Hook rollback is supported, but arbitrary module
side effects, task lifecycles and explicit unload are not.

### Shared hook and shutdown contract

The service table is now 40 bytes, with `install_hooks` appended at offset 36;
module ABI1/header and the preceding four service offsets remain unchanged.
Rebuild hook-owning modules against the current header and runtime.

`install_hooks(api, record, hooks, count)` accepts 1..8 `struct fp_hook` rows:
`{site, original, target, veneer}`. Targets must be aligned ARM instructions
inside the owner's image. The runtime validates the whole batch, including
ownership, duplicate sites, expected stock words and branch reach, before writes.
On success it publishes all veneers, then all entry branches, then the output
veneer addresses. Failure leaves the batch and outputs unchanged.

The transaction excludes local IRQ/FIQ dispatch and restores the incoming mask.
This is not a cross-core lock or protection against arbitrary external patchers.
The native singleton getter and allocator are not called with interrupts masked.

Cleanup code, callback object and ownership metadata live entirely in the cave,
not in USER memory. Ordinary and forced shutdown both close the runtime, revoke
the directory and restore each site only if it still contains our installed word.
Repeated cleanup is safe. Code that already entered a module is not freed.

A detached slot cannot be reused that boot: its executable veneer and target
remain valid for in-flight control flow. Eight is the boot's total exposure
budget, not eight reusable slots. An init failure after exposure consumes slots
and retains that image until reset, while the failed module is uncallable.

The next boot may reclaim only an exact known CLOSED cave implementation with
valid retired rows and no retained callback references in the reset native lists.
Unknown, active or modified cave contents are refused, not adopted or zeroed.
This does not repair old cards that left unmanaged hooks behind; perform a clean
power removal before installing this revision.

Sixteen offline lifecycle scenarios cover this contract, including both partial
registration directions, closure during startup, init failure after shutdown,
all interrupt-mask combinations, heap removal before cleanup, and a new-heap
warm reload. Run:

```sh
.venv/bin/python -B tools/verify_module_hooks.py --upstream builds/module-upstream
```

Actual registration instructions and cleanup execute in the ARM model. Native
scheduling, physical RAM retention and hardware cache effects remain unverified.

| Service | Contract |
| --- | --- |
| `lookup(api, id)` | First matching record, including failures; NULL if missing |
| `call(api, id, argument)` | Invoke only ready modules; status plus result |
| `report(api, id, value)` | Update the public diagnostic value during init or when ready |
| `ticks(api)` | Firmware microsecond counter, wrapping uint32 |
| `install_hooks(api, record, hooks, count)` | Atomic owned ARM entry hooks, detached at shutdown |

`module_abi.h` exposes ARM C types. The `call` result is a `uint64_t`: low 32
bits are status (`r0`), high 32 bits are value (`r1`) under ARM AAPCS.
Use `fp_call_status(result)` and `fp_call_value(result)`, checking status first.
The module callback itself returns a single uint32 value. Read-only registry
records include ID, state, image extent, callbacks, diagnostic value and original
initialization result. Modules use services rather than modifying those records.

Status values: `0` ready, `1` initializing, `-1` no memory, `-2` ABI mismatch,
`-3` malformed image/catalog, `-4` duplicate ID, `-5` init failed, `-6` unknown ID,
`-7` not ready, `-8` hook conflict, `-9` hook/cave capacity, `-10` shutdown
registration refused. Ordinary module failure does not fail the directory;
shutdown-registration failure prevents runtime startup.

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
The packager now uses `FPSUP_NO_BAR=1`: the final banner remains, but progress
updates are omitted. It does not enable the flash-backed settings bootstrap.
No camera boot-time improvement has yet been measured.

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

`module_ui.{h,S}` provide a module-to-module drawing/input dependency using the
40-byte runtime API and unchanged 48-byte records. Provider ID `0x100` owns a shared 96x32
canvas at LCD `(464,560)`; consumers send a six-word request
`{op,a,b,c,d,e}`: QUERY (version 1), FILL (clipped 16-bit rectangle), PRESENT,
HIDE, BUTTONS (wrapping press count), STATUS (last error). `module_ui_demo.S`
(ID `0x101`) draws call/button counters through it.

The provider owns the firmware-specific drawing code. PRESENT requests two
runtime-managed hooks: display submit `0xC02E8A08` and assigned FalseColor press
`0xC03722E8`. The press hook observes
and forwards unchanged; release is not hooked. **This is not general key input or
a replacement for the camera's FalseColor behavior.** Rendering uses a separate
43,136-byte USER allocation, keeping the code artifact within upstream's fixed
32-KiB packed-binary limit. Frames allocate nothing and call no consumer code; a
nonblocking lock protects the canvas and three tracked buffer identities.

```sh
.venv/bin/python tools/verify_module_ui.py --upstream builds/module-upstream
```

Fourteen offline scenarios pass, including exact tile writes, clipping, native
buffer ownership, unchanged button forwarding, complete-batch capacity refusal,
allocation errors and shutdown revocation. In the earlier implementation the
user confirmed the physical `C 1 / B 0` tile, normal FalseColor button behavior
after use, and an updated `C 2 / B 5` tile after a second invocation. Capture:
`builds/module-ui-proof/hardware-first-show.json`.

Limits: PRESENT/HIDE wait for the next eligible native submission (main layer,
format 1, width 1024, height 592..2048, live-view state 2); they do not force a
refresh. Restoration preserves native pixels that differ from the last overlay
value; a native write equal to it is indistinguishable, and a painted buffer
whose exact identity never returns cannot be restored. Buffer lifetime/reuse,
refresh behavior, scheduling under load, hide/menu transitions and recording
interactions are unverified. Owned hooks are detached at shutdown; allocated
state remains until reset. This is not a compositor or native menu API.

## Autonomous False Color module

`src/module_fcscale.S` wraps the existing `fcscale.S` toggle/scale as ABI1 module
`0x102`. Its initializer requests three runtime-managed press/release/display
hooks; code, native scale data and state live in its USER allocation. It needs
no generic UI provider. The earlier revision ran alongside the C example.

```sh
.venv/bin/python -B tools/verify_module_fcscale.py \
  --upstream builds/module-upstream --out builds/module-fcscale-proof
```

Seven scenario groups pass: boot ownership, native drawing, surface gates,
state transitions, stock conflicts, allocation/relocation, and shared shutdown
in both callback orders. Current module size is 3,628 bytes. On the camera the
user confirmed the earlier revision's short-press toggle and 500 ms scale hold.
The new shared lifecycle is not yet hardware-tested. This packages the existing
drawing tool; it has not been migrated onto the generic drawing provider.

### Plain native toggle in C

[`module_fclatch.c`](../../src/module_fclatch.c), module `0x103`, ports the plain
native on/off latch to C. It requests two runtime-managed hooks, with no display
hook or custom renderer. Do not combine it with `0x102` or an active UI button
hook. Current image: 844 bytes. Four offline scenario groups include both heap
bases, shutdown and fresh-heap warm reload. Its earlier 980-byte revision was
camera-confirmed on 2026-09-25; the new lifecycle awaits hardware validation.
[C tool walkthrough](c-modules.md#a-real-tool-native-false-color-toggle) for
build, verification, installation, and behavior.

## See also

- `c-modules.md` for the C workflow and a minimal walkthrough.
- `sideload.md` for the older SD-loading research, the FP3K reference loader, and
  the recovery/flash boundary.
