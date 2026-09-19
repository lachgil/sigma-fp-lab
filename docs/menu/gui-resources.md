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
| `src/nbuinject.S` | `0xC0793200`, 420 B | two guarded replacements and one injected run at `0xC05E6400` |
| enlarged header | pool `+0x54000`, 4,072 B | 285 -> 330 objects, one more `Menu` child |
| `Menu` declaration | pool `+0x56000`, 36 B | child capacity 6 -> 7 |
| the row | pool `+0x58000`, 10,792 B | 241 records copied from Frame Rate |

The kilobyte buffers ride in the loader's DMA pool, the way the menu's own code
does, because no cave in the image is both free and that big. The injector's
mode field grew a second flag for that: bit 1 means the buffer address is a
pool offset, resolved from `0xC3757A7C` at parse time. A pool base of zero
leaves the scene stock, which is checked.

**v1 duplicates Frame Rate.** The row keeps the donor's bindings, so it reads
and writes `MV_FrameRate`: opening it and picking a value changes the frame
rate, exactly as the stock row does. That is deliberate for the first camera
run -- it means every part of a working row is exercised (it draws, focuses,
opens a page and acts) with no new binding to be wrong at the same time.
Rebinding it to our own state is the next step, not this one.

The earlier `--fplab-page` option and `cards/fp-fplab-row-card.zip` are
withdrawn: they grafted a copy of an Auto ISO limit row, with its list,
activation and animation records dropped, into the wrong page.

What the first camera run has to answer: whether the row appears at all,
whether it lands at y=324 under Frame Rate, whether it reads `FP LAB` (the
literal-key fallback is the one link in the label chain that no emulator can
settle), whether Up/Down reach it, whether Right opens its page, and whether
the five stock rows still behave. **Nothing here has been on a camera.**

| Piece | What it is |
| --- | --- |
| `tools/nbu_scene.py` | Allocation-header codec and the graft that adds records with their exact header contribution |
| `tools/nbu_components.py` | Component property tables read out of the firmware; decodes, audits and locates every serialized field |
| `tools/fplab_page.py` | Record Settings identity, the row build, and the declaration-only construction experiment |
| `tools/native_scene_vm.py` | Runs the firmware's interpreter, object factory and id table offline |
| `src/strhook.S`, `tools/verify_strhook.py` | Private names, and the equivalence test against the firmware's own resolver |
| `src/nbuinject.S`, `tools/verify_nbuinject.py` | The injector, and ten cases at the real hook site |

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

## Corrections to our own notes

- The compression engine's throughput, unknown in earlier notes, was **measured
  at 169.7 Mpix/s**: enough for FHD compressed recording, not UHD. Calling it
  cold fails (power/clock domain requests refused) and damages still compression
  until restart.
- Sensor mode numbering: a single sample at either end is untrustworthy; take
  the mode that persists longest across a GFM6 history.
