# The GUI resource system, and what it means for a native FP LAB entry

Sources: the supplied `fp-re-handoff-2026-09-14` tree, its FP3K technical
handoff sources, and our offline fp 5.02 native-code experiment below.
Historical observations from different resource families must not be treated
as a universal GUI lifecycle contract.

## Why our "native menus are blocked" conclusion was too broad

Packed NBU chunks prevent growing a record **in place**. They do not prevent
adding rows through parser substitution or a correctly rebuilt scene.
Earlier statements that new rows were impossible were unsupported.

Value lists and scene rows are distinct: extending the Resolution CSV does
not construct an additional Record Settings row.

| Layer | Where | Relevant contract |
|---|---|---|
| NBU string pool | `0xC18C0474` | Existing names use big-endian pool offsets |
| Serialized scene records | Per-scene NBU chunk streams | Allocation header precedes declarations and components |
| Runtime objects | Allocated by the native interpreter | Parent links, ID table, components and lifetime must agree |

Records reference strings by **big-endian pool offset**,
`offset = string address - 0xC18C0474`.

## The result that matters most

**The enum→state pair table at `0xC2E4020C` is patchable at runtime and takes
effect on the next draw.** `MenuValueConverterBase__v21` (`0xC06BDA30`) returns a
hard-coded count of 3, and the table is:

```
0xC2E4020C  {3, 0}   UHD
0xC2E40214  {2, 1}   FHD
0xC2E4021C  {4, 2}   a third state SIGMA already defines
```

Verified in our own image: `[(3,0), (2,1), (4,2)]`. The third mapping is
stock, but does not make enum 4 a safe recording selection. FP3K's working
third choice also changes list/range data and intercepts selection and
readback paths. Its extra artwork covers Settings, QS and HUD surfaces.

The handoff's resolution-artwork observations have narrower scope:

| Data | Read when | Runtime patch |
|---|---|---|
| pair table `0xC2E4020C` | every evaluation | **works, immediately** |
| tested serialized resolution-artwork arrays | not rebuilt in tested transitions | editing these bytes did not update those arrays |
| parsed element objects | tied to owner lifetime | a one-time mutation need not survive reconstruction |

## Icons are LZ4, and encodable

2005 XCI blobs live at `0xC1240014` – `0xC151C348`, in writable DRAM at runtime.
16 bpp, little-endian u16 per pixel, low byte luminance, high byte alpha. Their
`tools/xci.py` round-trips pixel-exact. Two spare families the fp never draws:

```
M_resolution_dci4K  @0xC1359724   2896 bytes
L_resolution_dci4K  @0xC12CA138   3472 bytes
```

## Instruction state must be checked at each hook

The NBU interpreter at `0xC05E6400` is Thumb-2; the enum mapping getter at
`0xC06BDA30` is ARM. Do not infer instruction state from a broad GUI address
range. FP3K enters the Thumb parser through a branch/interworking wrapper,
executes the displaced prologue, and resumes at `0xC05E6405`.
Relevant branch rules:

- enter with **`B.W` (T4), not `BL`**, so `lr` still holds the original caller's
  return and the displaced instructions can run unchanged;
- return with `bx` to a Thumb address (bit 0 set);
- avoid `bl` between local labels: it emits `R_ARM_THM_CALL`, which the
  relocation handler in `armasm.py` does not implement.

## What the page actually is

Record Settings is `B2_5`, **not** `B1_2_5`. `B1_2_5` is Auto ISO Settings: its
four rows are ISO Lower Limit, ISO Upper Limit, Slowest Shutter Speed Limit and
Maximum Shutter Angle. Earlier notes here named it Record Settings and the row
work was built on it; that was wrong, and `tools/fplab_page.py inspect` now
proves the identity out of the firmware instead of asserting it:

| Scene | Object | Row | Key | English |
| --- | --- | --- | --- | --- |
| `B2_5` | 90 | `B2_5_1` | `0301` | Format |
| `B2_5` | 91 | `B2_5_2` | `0304` | Bit Depth |
| `B2_5` | 4361 | `B2_5_3` | `0309` | Compression |
| `B2_5` | 4253 | `B2_5_4` | `0312` | Resolution |
| `B2_5` | 4433 | `B2_5_5` | `0315` | Frame Rate |

The scene starts at `0xC1A5C590`, declares 285 objects, and its `Menu` container
is object `0x59` at `0xC1A63E99` with room for six children (five rows plus
`Blind`). The movie-resolution bindings live here too: the `controlAppVariable`
records under `B2_5_5` name `MV_FrameRate`, `SYS_SubmenuIndex` and
`B2_n5_ListFocus4`, which is what a native row would have to drive.

## What is proven, and what is not

`tools/fplab_page.py verify` runs the **real** firmware record interpreter,
object factory, parent attachment, name assignment and sorted-ID insertion over
the enlarged scene:

- stock: 285 objects, six `Menu` children;
- extended: 330 objects, seven `Menu` children, from the 45 declarations of the
  Frame Rate row renumbered into `0xF000..0xF02C` with its root reparented to
  `0x59`;
- all 285 stock IDs and all 45 private IDs resolve;
- negative control: leaving the object count at 285 makes the native store at
  `0xC05E8290` write past the reserved object-pointer table.

That is object construction and nothing else. **It emits declarations only.**
Components, drawing, focus, key handling and actions are not built, so the
arena sizes exclude component storage and none of this is installable.

What a real native row still needs:

- **done: the per-component property schemas.** A component record is a
  presence bitmask over an ordered property table the firmware itself carries:
  each constructor registers `(count, table)` with `0xC05D5E30`, and the
  interpreter's reader at `0xC05E7B78` dispatches 15 property types
  (`0xC05E7BC0` is its jump table). `tools/nbu_components.py` locates those
  tables and decodes records against them: **206,836 component records across
  all 221 scenes consume exactly their own length**, so field offsets are read
  rather than guessed. Undecoded families are reported, not half-decoded:
  `fill` (240 records), layout records `0x10007` (211), `controlLayout` (4),
  `animationEvent` (2), `languageEvent` (2).
  That matters because object references are type-14 fields whose offset
  depends on which earlier properties are present. The Frame Rate row is 294
  records and 17,144 bytes, and `fplab_page.py inspect` now audits it: **73
  object references** -- 69 inside the row, 4 leaving it (objects `1` and `37`,
  the scene root and `Footer`) -- across `changePropertyByControl` (33),
  `controlAnimation` (18), `controlFocus` (15), `controlAppVariable` (6) and
  `drawScrollbar` (1).
- the animation-group header arithmetic. `B2_5` declares 214 `animationClip`
  entries for 190 group records, so the clip count is not one per record and the
  copied row's positional entries cannot be sliced out by counting records.
  `animationClip` and `list` records are the two forms in the row that
  `nbu_components.py` does not decode.
- a label, and actions. Every four-digit localization key in the pool is
  referenced; the withdrawn row borrowed key `1636` ("DCI 4K 4096x2160") by
  overwriting a stock English string, which is a hack, not a mechanism. A row
  that does anything also has to bind to a variable the way the stock rows bind
  `MV_FrameRate`, and nothing here does that yet.

### Connecting this to FP3K's parser hook

FP3K `tools/fp3k_native_ui.S` redirects a record at `0xC05E6400` using
reader `+36` (stream base) and `+4` (position), retaining `+20` (string pool).
After interpreting the replacement it restores the original base and resumes
after the original record. Its bounded synthetic-string resolver provides
private names without overwriting localization keys.

That establishes the mechanism, and `src/nbuinject.S` extends it: the 512-byte
scratch in FP3K's version is too small for this scene's allocation header, and
one substitution per record cannot add records, so ours carries its own buffers
and an injection mode.

## The row on a card

The card build is `--fplab-row`:

```sh
.venv/bin/python tools/fplab_page.py row          # build + verify the row
.venv/bin/python tools/verify_strhook.py          # the label mechanism
.venv/bin/python tools/verify_nbuinject.py        # the hook, fed the real table
.venv/bin/python tools/build_combined_card.py --debug --fplab-row \
    --out builds/fplab-row
```

What it installs, and what each piece is for:

| Piece | Where | What |
| --- | --- | --- |
| `src/strhook.S` | `0xC0793100`, 60 B | answers offsets above the pool out of its own blob, so the row can be NAMED |
| `src/nbuinject.S` | `0xC0793200`, 452 B | two guarded replacements and one injected run at `0xC05E6400` |
| enlarged header | pool `+0x54000`, 4,072 B | 285 -> 330 objects, one more `Menu` child |
| `Menu` declaration | pool `+0x56000`, 36 B | child capacity 6 -> 7 |
| the row | pool `+0x58000`, 10,792 B | 241 records copied from Frame Rate |

The kilobyte buffers ride in the loader's DMA pool, the way the menu's own code
does, because no cave in the image is both free and that big. The injector's
mode field grew a second flag for that: bit 1 means the buffer address is a
pool offset, resolved from `0xC3757A7C` at parse time. A pool base of zero
leaves the scene stock, which is checked.

**This candidate still duplicates Frame Rate bindings.** Its copied records
name `MV_FrameRate`, not private FP LAB settings. Earlier camera builds prevented
Record Settings from opening, so drawing, focus, opening and setting changes
were not demonstrated. `installable=False` describes this unfinished candidate;
the explicit `--fplab-row` option remains an offline experimental build.

The earlier `--fplab-page` option and `cards/fp-fplab-row-card.zip` are
withdrawn: they grafted a copy of an Auto ISO limit row, with its list,
activation and animation records dropped, into the wrong page.

The camera reported Record Settings would not open, while the camera itself
remained responsive. The parser-status fault identified below is fixed offline,
not yet on the camera. Row placement, label fallback, Up/Down, Right/OK, back,
stock-row behavior and full component allocation remain unverified.

| Piece | What it is |
| --- | --- |
| `tools/nbu_scene.py` | Allocation-header codec and the graft that adds records with their exact header contribution |
| `tools/nbu_components.py` | Component property tables read out of the firmware; decodes, audits and locates every serialized field |
| `tools/fplab_page.py` | Record Settings identity, the row build, and the declaration-only construction experiment |
| `tools/native_scene_vm.py` | Runs the firmware's interpreter, object factory and id table offline |
| `src/strhook.S`, `tools/verify_strhook.py` | Private names, and the equivalence test against the firmware's own resolver |
| `src/nbuinject.S`, `tools/verify_nbuinject.py` | Injector and fourteen hook-contract cases, including parser status and early termination |

`src/nbuinject.S` keeps its own table: each entry carries the stock record's
length and FNV-1a, so a record that does not match byte for byte is left alone
and counted at state `+0x0C`. Our caves start at `0xC0793100`, which is worth
knowing because ijigen's OG3K UI pack occupies `0xC0793060..0xC079585C`: the
two cannot be installed together as they stand.

## What the generic boot append probe does not prove

`src/uiprobe.S` observes calls to `0xC05E2CA8` across element families.
A nonzero total alone does not identify resolution-array construction or prove
its timing relative to AutoRun. The observed count of 382 and first eight
names must not be used as that proof. FP3K's current native UI sources also
support late resource-pack registration during NBU interpretation.

## Native menu feasibility review, 2026-09-21

Upstream now publishes a [Lossless RAW fourth-row candidate](https://github.com/ijigen/fpSup/blob/main/lossless/menu/README.md):
a private MainB2 record stream with a copied native two-choice widget, typed
reference remapping, private strings and animation budgets. It explicitly remains
`BLOCKED_NOT_DEPLOYABLE`: variable registration, callback lifetime, navigation,
page loading/rebuild timing, rendering and allocator capacity are not verified.
This is evidence for constructing native menu data, not a working custom page.
Its referenced `research/ui/tools/native_ui_audit.py` is absent from the public
main tree inspected today, so its complete builder was not run here.

The upstream allocation finding changes the priority of our earlier blocker.
An offline probe through our real firmware header interpreter, using B2_5,
requested 225,336 bytes both with stock budget order and with `groups`,
`clip_tracks`, `track_keys` and `list_items` individually reversed. Increasing
one clip-track budget by one requested 225,372 bytes. Component registry sizes
were modelled as zero, as in `native_scene_vm.py`; this checks header arithmetic,
not component construction or sufficient allocation for a working page.
Record order is therefore not a demonstrated requirement for this allocation
calculation. The older positional-budget restriction above needs re-evaluation.

Our existing row candidate still passes its 15 offline checks, but removes 18
`controlAnimation` records, 29 clips and six groups. It is not a demonstrated
fully functional native menu. The next useful proof is an intact donor page with
its navigation and animations preserved, entered from a native row and exited
through Menu/back, before adding private settings or feature callbacks.
Retaining a whole donor page is a proposed way to reduce cross-page references,
not an established page-registration or navigation API.

## Firmware loader investigation and status fix, 2026-09-21

### What the shared tools actually demonstrate

The supplied FP3K handoff's `analysis/engineering-progress.md:283-285` and
`analysis/fullheight-preview.md:1-4` record the successful v0.3.2 Settings
summary, HUD and QS result. This follows the failures in
`analysis/native-menu-integration.md`, rather than contradicting them.

The working mechanism adds a choice to the existing 89-byte Resolution CSV at
`0xC0F8E7EC`, raises its independent controller maximum at `0xC1A709BC`,
intercepts selection/readback, and extends native resource animations.
`tools/fp3k_native_menu.S` keeps private 3K state separate from stock capture
enum 3. `tools/fp3k_native_ui.S` substitutes existing NBU records and loads a
private NBR asset pack. Its replacement path preserves the parser's return
value. These are working native UI techniques, not a demonstrated new page or
additional Record Settings row. The second supplied handoff offers the same
mechanism; no complete private-page registration implementation was located.

### Why our scene stopped loading

Disassembly of the actual scene loader, `0xC05E82F0` Thumb:

```text
C05E8344  ldr r0, [r6, #8]       ; cached scene stream offset
C05E8346  str r0, [r4, #4]       ; reader position
C05E8348  mov r0, r4
C05E834A  bl  C05E6400          ; interpret one record
C05E834E  cmp r0, #0
C05E8350  beq C05E8348          ; continue only on zero
C05E8352  ldr r0, [r4, #0x50]  ; examine constructed objects
```

Our replacement path overwrote native `r0` with `INJECT_STATE` while updating
its counter. Thus even a successfully parsed allocation header ended this
loop. The injected-run path also ignored intermediate nonzero results.
FP3K restores the reader position using `r1`, leaving `r0` intact.

Reproduced with the real firmware header parser: direct enlarged-header
interpretation returned `0`; through the old hook it returned `0x10210000`
(the experiment's state address). The fixed hook returns `0`. Both requested
239,728 arena bytes with component sizes modelled as zero.

A second experiment executed the actual `C05E8348` caller loop and header
parser. Reintroducing the old return-value corruption exited at `C05E8352`
after `C1A5C590`; the fixed hook continued to `C1A5D574`, the next record.
This is an offline causal reproduction, not a full GUI or camera success.

The real interpreter also consumed the eight-byte `FFFFFFFF` terminator at
`C1A792F3` and returned `1`. Earlier claims that the terminator or last record
was never dispatched were unsupported by aggregate counters. The original
terminator anchor is restored; the undeployed Menu-anchored mode was removed
because it changed record ordering without addressing this return contract.
The injector now preserves replacement status and stops a run on the first
nonzero result, restoring the reader before returning it.

Three new status regressions failed before the fix and passed afterward.
The verifier now checks fourteen cases, including the terminator's `1`.
Build `builds/fplab-status-fix-offline` passed the existing 30 firmware guards
and reservation-overlap checks. No camera, card or live USB writes were made.

### The missing layer for a full page

Native directory construction is now exercised, not inferred from identical
page copies. Parsing the stock directory at `C18EB48C` with the real interpreter
constructed 221 runtime entries. Serialized entries are 32 bytes; runtime
entries are **44 bytes**, held at reader `+0xAC`, count at `+0xA8`.
The `B2_5` runtime entry contains name pointer `C18C9FD4` and stream offset
`0x19C130`, resolving to `C1A5C590`.

Instructions `C05E66C6..C05E6746` resolve the name, reject an existing name via
`C05E04E8`, construct a 100-byte scene object with `C05D9080`, register it via
`C05E0CC8` (context vector `+0x7C`), and copy the descriptor. The loader later
reads this runtime copy, not the serialized directory word. Bounded allocator,
reader callbacks and preallocated context-vector storage were modelled; the
native registry insertion and descriptor construction executed. Rendering and
application-state routing were not exercised.

This gives a concrete next route: preserve an intact donor's components and
animations, register a private scene through the native directory path, then
resolve its application-state entry and return context together. A scene name
in the resource registry is not by itself a selectable application state.
`controlAppState`, Right/OK, `MENU_ReturnScreen`, focus restoration, private
variable registration and teardown still need an end-to-end proof. Do not
replace them with a root-only Blank/Test scene, reuse donor setting bindings as
private controls, or infer sufficient camera allocation from the zero-size
component model.

## Corrections to our own notes

- The compression engine's throughput, unknown in earlier notes, was **measured
  at 169.7 Mpix/s**: enough for FHD compressed recording, not UHD. Calling it
  cold fails (power/clock domain requests refused) and damages still compression
  until restart.
- Sensor mode numbering: a single sample at either end is untrustworthy; take
  the mode that persists longest across a GFM6 history.
