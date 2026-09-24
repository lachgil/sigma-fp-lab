# Writing fp modules in C

You can now write a camera module in plain C instead of ARM assembly, build it
with one command, and run it on the fp through the module loader. This doc is a
short, practical walkthrough. For the loader/registry internals it sits on top
of, see `module-platform.md`; for the older SD-loading research, see `sideload.md`.

## What "C support" means here

- You write two ordinary C functions. No assembly, no firmware addresses, no
  hand-written module header.
- `tools/build_module.py` compiles them (Clang + LLD), adds the module header
  and a tiny bootstrap, and produces one `.bin` the loader accepts.
- The build makes the code position independent: it works wherever the camera
  puts it in memory, so you never hard-code an address.
- It is **freestanding C11**: the language and your own code, nothing else. There
  is no `printf`, no `malloc`, no standard library. You talk to the camera only
  through the small service table the loader hands you.

This is real and camera-tested (2026-09-25), but it is a foundation, not a full
SDK. It does not by itself draw to the screen, read buttons, or add menu items;
those need the drawing/input modules described in `module-platform.md`.

## The smallest possible module

Two functions, including `module_abi.h`. `init` runs once at load; `invoke` runs
each time something calls your module. Note `init` returns `int32_t`, `invoke`
returns `uint32_t`; the builder rejects the wrong signature.

```c
#include "module_abi.h"

int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record)
{
    (void)api;
    (void)record;
    return FP_OK;            /* zero = loaded successfully */
}

uint32_t fp_module_invoke(const struct fp_api *api,
                          const struct fp_record *record, uint32_t argument)
{
    (void)api;
    (void)record;
    return argument + 1;     /* whatever this tool computes */
}
```

Build it (every module needs a unique nonzero ID):

```sh
.venv/bin/python -B tools/build_module.py hello.c \
  --module-id 0x300 --upstream builds/module-upstream --out hello.bin
```

That prints the module's ID, size and SHA-256. This 15-line source compiled to a
244-byte module. Run through the offline ARM model it returned, for real:

```
invoke 0  -> 1
invoke 41 -> 42
invoke 99 -> 100
```

So the C you wrote actually executed on emulated fp hardware and returned
`argument + 1`. The same shape ran on the physical camera on 2026-09-25.

## Talking to the camera: the service table

The `api` argument is how your C reaches the camera. Four services exist today:

| Call | What it does |
| --- | --- |
| `api->ticks(api)` | Read the firmware microsecond clock (for timing) |
| `api->report(api, id, value)` | Publish a diagnostic value others can read back |
| `api->lookup(api, id)` | Find another loaded module by ID |
| `api->call(api, id, arg)` | Call another loaded module and get its result |

A module that counts how many times it was called and publishes the count:

```c
#include "module_abi.h"

static uint32_t calls;      /* zero-initialized; the loader guarantees this */

int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record)
{
    api->report(api, record->id, calls);   /* publish the starting value */
    return FP_OK;
}

uint32_t fp_module_invoke(const struct fp_api *api,
                          const struct fp_record *record, uint32_t argument)
{
    calls += 1 + argument;
    api->report(api, record->id, calls);   /* others can read this back */
    return calls;
}
```

`api->call` is how one module builds on another. That is exactly how the drawing
demo works: it calls the drawing-provider module by ID to put pixels on screen,
without knowing anything about the provider's internals. A new C tool can do the
same once a provider module is on the card.

## Why this is useful

- **Lower barrier.** A contributor who knows C but not ARM assembly can write a
  working module. `src/module_c_example.c` is a complete starting point.
- **Composable.** Modules find and call each other by ID through `api->call`, so
  one person's tool can use another's service (drawing, math, sensor helpers)
  without sharing source or addresses.
- **Safer builds.** The builder refuses common mistakes before anything reaches
  the camera: wrong callback signatures, missing callbacks, and any function your
  code calls that is not defined in your own source (no silent stubs).
- **Portable code.** Because the output is position independent, the same module
  binary loads regardless of where the camera allocates it.

## Build and package options

```sh
# Just the module binary:
tools/build_module.py my.c --module-id 0x301 --upstream builds/module-upstream --out my.bin

# Module plus a bootable debug card (adds the USB shell for testing):
tools/build_module.py my.c --module-id 0x301 --upstream builds/module-upstream \
  --out my.bin --card-out my-card --debug

# Load existing modules before yours (e.g. a drawing provider), in order:
tools/build_module.py my.c --module-id 0x301 --upstream builds/module-upstream \
  --out my.bin --dependency provider.bin --card-out my-card --debug
```

- `-D NAME=VALUE` passes preprocessor definitions to the compiler.
- `--compiler` / `--linker` (or `FP_MODULE_CLANG` / `FP_MODULE_LLD`) pick tool
  paths. Install the toolchain normally: Arch `sudo pacman -S clang lld`,
  Debian/Ubuntu `sudo apt install clang lld`.
- The `.S` assembly path still works for modules that need it.

## What it is NOT (limits worth knowing)

- **No standard library.** No `printf`/`malloc`/string functions unless you write
  them into your own source. Missing helpers fail the build, not silently stub.
- **One translation unit, ARM soft-float.** No C++, TLS, constructors/destructors
  or exceptions. Only checked `R_ARM_RELATIVE` data/GOT relocations are supported;
  anything else is rejected.
- **No screen or buttons by itself.** Drawing and input come from separate
  modules (`module_platform.md`), reached via `api->call`.
- **No on-demand loading yet.** Modules load at boot, not when a menu item is
  chosen. That loader-on-demand workflow is described, and not built, in
  `module-platform.md`.
- **Trusted code.** A module that crashes or hangs can still take down the
  camera; the loader isolates reported init failures, not arbitrary bugs.

## Evidence

- `tools/verify_module_c.py` runs the emitted ARM C through the real loader and
  registry. Six scenarios pass: identical results at two different memory bases
  (proving relocation), zero-initialized data, const tables/strings, data and
  function pointers, a repeated-init guard, init-failure cleanup, and rejection
  of missing callbacks and unresolved external calls. Report:
  `builds/module-c-proof/report.json`.
- Camera, 2026-09-25: a C example module (`0x201`) loaded alongside the False
  Color module (`0x102`), initialized, and answered three live calls over USB
  while False Color kept working. Capture: `builds/module-c-hardware/hardware.json`.
- The minimal `argument + 1` module above built to 244 bytes and returned
  1/42/100 for inputs 0/41/99 through the ARM model.

Offline model results are not proof of any specific on-camera timing, and neither
run establishes recording-time safety.
