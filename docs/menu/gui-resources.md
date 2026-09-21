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
| the row | pool `+0x58000`, 17,144 B | 294 records copied from Frame Rate, animations intact |

The kilobyte buffers ride in the loader's DMA pool, the way the menu's own code
does, because no cave in the image is both free and that big. The injector's
mode field grew a second flag for that: bit 1 means the buffer address is a
pool offset, resolved from `0xC3757A7C` at parse time. A pool base of zero
leaves the scene stock, which is checked.

**This candidate still duplicates Frame Rate bindings.** Its copied records
name `MV_FrameRate`, not private FP LAB settings, so it reads and writes the
frame rate exactly as the stock row does. It is now grafted with its animation
groups and clips intact (see the clip-allocation resolution below), so focus
highlight and visibility travel with it. Earlier camera builds prevented Record
Settings from opening (the parser-status fault, since fixed offline), so drawing,
focus and opening are still not demonstrated on a camera. `installable=False`
because none of it has run on hardware; `--fplab-row` is an offline build.

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

### Application-state routing resolved, 2026-09-21

A row opens a page through a `controlAppState` record whose `sync-request` names
a destination **application state**, not a scene. `tools/native_navigation.py`
decodes a row's key groups and reports each destination and its return context:

```text
MainB5/B5_5   Enter -> SetupHelpMenu   frame present, ...
              Enter -> optionOn        NO frame, ...
```

`SetupHelpMenu` has a scene frame of that name; `optionOn`/`optionOff` do not.
Both are application states. This is the decisive point: **the state name is
resolved against a per-context state registry, not the NBU scene directory.**
Registering a private scene in the directory (the runtime descriptor above)
does not create a selectable state.

The path, all confirmed by disassembly:

- `controlAppState` schema is registered by its constructor `C05F1440`
  (`sync-request`, `app-sync-request`, `to-uic`, `skip-anim`,
  `send-all-shared-context`, `view-state-event`, `essential`,
  `to-all-shared-context`).
- action handler `C05F1398` reads those 8 properties, resolves the receiver via
  `C05D70E8`, then calls the request path `C05DC8E0` (no app-sync) or
  `C05DC9A0` (with `app-sync-request`/`to-uic`).
- the request walks the context's state subsystem at object `+0x198`; the state
  machine core is `C05DC178`. Name to state entry is `C05ED3B8` (FNV lookup);
  `C05ED248` inserts one.
- state tables are installed into every GUI context by
  `C055F8F0(contexts, count, table)`; call sites `C055FCB4`, `C0560470`,
  `C0562988`, `C0564014`, `C0564BDC`. Entries are fixed-size records, distinct
  from scene bytes. Per-context construct/teardown is `C05DA3D0`/`C05DA51E`.

So a private FP LAB page needs one of:

1. reuse an existing stock state name for the row's `sync-request`, and make its
   frame our private scene (simplest, but collides with the stock page), or
2. register a private application state whose name our row targets, through the
   same `C055F8F0`/`C05ED248` path a stock table uses, then point its frame at
   the private scene.

`MENU_ReturnScreen` is written by a sibling `controlAppVariable` in the same key
group and is how the opened page returns; a private row MUST write it too, which
is the return-context half the 2026-09-14 hardware retarget dropped. Focus
restoration, private variable registration and teardown are still unproven end
to end, and none of this has been on a camera.

### Intact animated donor: resolved, 2026-09-21

`nbu_scene.graft` already carries animation groups, clips, tracks and keys with
their header contributions, so keeping the Frame Rate row's animations is a
`_array_slice` question, not new machinery. The remaining blocker is now exact
numbers, not a vague "positional entries cannot be sliced".

The Frame Rate row (`4433`) subtree holds 6 `animationClip` group records and
**29** clip records. But its numbers do not agree three ways:

| source | clips |
| --- | --- |
| clip records in the subtree | 29 |
| the group records' own `+0x10` field, summed | 28 |
| the header `groups[i][2]` reservations, summed | 30 |

The header total is authoritative for the whole scene (its per-group counts sum
to the 214 `animationClip` entries that equal `clip_tracks`' length), so the
grafted header must reserve 30. What is unresolved is which clip slots the 29
records fill and why the group record's own count is 28: records fewer than
reservations implies default-filled slots, but that mapping is not proven, and
emitting a header whose clip reservation disagrees with the interpreter's own
per-group fill would corrupt the scene arena. `_array_slice` now raises this as
a precise numeric error rather than a blind slice.

**Resolved.** `clip_tracks` is reserved per animation GROUP, not per clip
record: for the whole scene its length equals the group reservation total (214
for B2_5), not the clip-record count (190). So records fewer than reservations
is the stock norm, and clips bind to their owner by id (`C05E6FAA`), never
positionally. The row's 6 groups are contiguous in group order (indices 42-47),
so their reservation slices cleanly: **30 `clip_tracks`, 95 `track_keys`**. The
grafted header therefore adds exactly those, matching how the stock scene is
laid out. `_array_slice` now enforces the real invariants (contiguous groups,
clip records <= reserved) instead of a false equality, and `build_row` keeps
every record. A stale-reference scan of the whole renumbered subtree finds zero
leftover stock ids, and `verify_row` passes 15/15 with the animation groups,
clips and keys carried across. The row body is the full 17,144 bytes / 294
records. Still offline only: drawing, focus and the camera remain unproven.

### First hardware result for the intact row, 2026-09-21

On the camera (SD `1749-D0A5`, intact build): **the FP LAB row appears**, named
correctly, in its own slot under Frame Rate. That confirms end to end on
hardware: the parser-status fix (Record Settings opens), the intact graft, the
private string resolver (label), the enlarged header, and the y=324 slot. This
is the first time any added row has drawn on the camera.

Remaining: the row **does not highlight and cannot be entered**. It is a proper
Menu child (7 children, verified), visible at its slot, but the cursor never
lands on it. Row-to-row navigation is NOT in the rows' own records: each row's
Up/Down `controlFocus` is empty, and the cursor is driven by an index/ListFocus
bound that lives outside the copied subtree, so our identical copy of Frame Rate
is not in the focus ring.

Leads for the cursor bound (each row carries `controlValue` controllers with a
`max-value` and `terminal-type`): the four stock rows read `max-value 7`, while
Frame Rate (the last row) reads `max-value 8` on its two focus controllers
(`0x1152`, `0x1156`) and `max-value 9` on `0x1158`. That per-row step is the
likely cursor-range encoding, analogous to FP3K's resolution-list `max-value`
bump (`C1A709BC` 1->2) that made its third choice reachable. The next step is to
identify which controller bounds row travel and whether the stock rows' bound
must grow by one for a sixth row, before flashing again. Do not flash a guessed
bump: confirm the controller first.

### Focus-ring diagnosis, 2026-09-21 (camera)

User at the camera: pressing Down on Frame Rate, the highlight LEAVES Frame Rate
but FP LAB never lights, and it never highlights on a full up/down scroll. So
focus advances off the last stock row and wraps WITHOUT visiting ours: the row
is a Menu child (7 children verified) with a full `MenuItem_Select` focus subtree
copied intact, yet it is not a member of the native focus ring.

What is now ruled out as the discriminator:
- `tab-index`: every row's `MenuItem_Select` reads `tab-index 0x1` (identical),
  so it is not a per-row focus order key.
- `layout-item-index`: per value-choice inside a row (0..7/8), not per row.
- `SYS_SubmenuIndex`/`SubmenuFocus`: the row's selected VALUE, sourced from the
  row's value controller, not a row cursor.
- child membership: ours is a proper Menu child at its slot and DRAWS.

The highlight is each row's own `MenuItem_Select` `focusEvent` -> `controlAnimation`
`Main_Focus`/`Focus_On`; the shared `Cursor`/`Cursor01..03` bars (objects
`0x39`/`0x34..0x36`) are positioned by the focus system to the focused item. The
rows themselves carry NO Up/Down `controlFocus` (that navigation is native), so
the focus ring is assembled by firmware, and it excludes a sixth item. The ring
is therefore a fixed-size or count-bounded native structure sized to the stock
row count. Next: reverse the native focus-list builder (who enumerates the
MenuItem_Select children and how the ring is bounded) or read it live over the
USB shell with the menu open. Do NOT flash another guess.

Native enumerator traced. The row Up/Down is `controlFocus` do-loop, handler
`C05F2020..`, no-target branch at `C05F20A4`: it reads the parent, walks its
children by index (`C05D1760` count, `C05D1780` get-by-index), and for each
calls the child's `vtable+8` type, skipping type `7`, taking the first focusable.
This is DYNAMIC, so a constructed child should be found. That contradicts the
symptom, so the cause is one of two runtime facts static analysis cannot settle:

1. our row's constructed object reports type `7` (skip) to `vtable+8`, so the
   enumerator passes it, or
2. focus does land on our row but its `MenuItem_Select` `Main_Focus`/`Focus_On`
   highlight is the empty animation slot (the 29-vs-30 clip), so it is
   invisibly focused (which would also explain "can't enter" if Right was not
   tried while invisibly on it).

Deciding needs the live camera: read the constructed row's type and the focus
state with the menu open. The USB shell times out while the menu is open
(`moved=0/64`), so a reliable live read, or a targeted A/B (move the row far and
watch where any highlight lands), is the next step. Not another blind flash.

Structure decoded further, same day. The `animationClip` group record
(`0x1000B`) is: tag, size, `word2` (a small count: 3,2,2,1,2,2 for the six
groups, equal to the header entry's first field), `0`, `word4` (3,2,19,1,2,1,
summing 28), owner object at `+0x14`, then a target object and a pool offset.
The header `groups[i]` triple is `(word2, 0, reserved_clips)` with
`reserved_clips` = 4,2,19,2,2,1 (30). Crucially the **clip records are owned by
ordinary objects** (`0x1152`, `0x1155`, `0x1157`..`0x1175`, and one at
`0x7446`), not by the group records, so `subtree` gathers them by object
ownership. The 29/28/30 spread is therefore three different fields, not one
miscount: 29 clip records owned by the row's objects, 28 summed from the group
records' `word4`, 30 reserved by the header groups. `_array_slice`'s model that
clips are "created by the group records, groups[i][2] of them each, in stream
order" is the assumption that breaks here.

The one runtime capture that settles it is how many `clip_tracks` slots the
scene loader actually consumes per group when `B2_5` loads, versus per owning
object. That is a whole-scene load, which `native_scene_vm` does not model
(it substitutes single records against a prebuilt state), so it is the concrete
next instrument to build, not another offline guess.

Clip binding decoded, same day. The record dispatcher's clip case is
`C05E6FAA` and its group case is `C05E7274` (from the tag jump table at
`C05E6464`). The clip handler resolves the clip's owning object **by id**:
`C05D9E80` looks the owner up by name and `C05E82A0` (the sorted id table at
reader `+0x48`) resolves the target, then `C05D5968` attaches the clip. So a
clip is **self-identifying by owner id, not positional** in the stream. The
group handler `C05E7274` builds the group through `C05E2988` from its counts.

That means a grafted clip whose owner id we renumber will bind correctly
regardless of stream order, which removes the "positional slice" fear. What it
does NOT settle is the header's per-group `clip_tracks` reservation: the header
reserves clips per animation group (`groups[i][2]`), while records attach per
object, and the row's 6 groups reserve 30 while only 29 records are owned inside
the subtree. The correct grafted reservation is still unproven, because the
group-to-clip-track accounting is indirect. This is the number the whole-scene
load emulator must produce; the binding mechanism above is settled, the
reservation count is not.

## Corrections to our own notes

- The compression engine's throughput, unknown in earlier notes, was **measured
  at 169.7 Mpix/s**: enough for FHD compressed recording, not UHD. Calling it
  cold fails (power/clock domain requests refused) and damages still compression
  until restart.
- Sensor mode numbering: a single sample at either end is untrustworthy; take
  the mode that persists longest across a GFM6 history.
