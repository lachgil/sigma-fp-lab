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

## A construction route, executed offline

`B1_2_5`, not MainB5, is Record Settings. Its stream begins at
`0xC196410F` with a 2,948-byte allocation header. That header declares
238 objects and component/supplemental sizing information. The native
interpreter's header path at `0xC05E679A` uses those counts before object
construction.

The `Menu` declaration at `0xC196D74B` has object ID `0x59` and capacity
for six children: four setting-row containers plus `shuttermode` and `Blind`.
An extra row needs another child slot. The native child vector can request
reallocation; increasing its declaration capacity avoids that extra request.

Run the local investigation fixture:

```sh
.venv/bin/python analysis/native_row_construction_proof.py
```

It executes the **real firmware** record interpreter, object factory,
parent attachment, name assignment, and sorted ID insertion/lookup:

- Stock: 238 objects, six Menu children.
- Extended: 249 objects, seven Menu children. The added declaration subtree
  comes from the 11-object `MainB5/B5_9` jump row, with private IDs
  `0xF000..0xF00A` and its root parent changed to `0x59`.
- All 238 original IDs and all 11 new IDs resolve.
- Negative control: leaving the scene object count at 238 causes the native
  store at `0xC05E8290` to write past the reserved object-pointer table.
  This demonstrates a failure mode, not a diagnosis of earlier camera failures.

The fixture models allocation, byte readers, and component-size lookup.
It parses **declarations only**, not the donor row's components/actions.
The arena-size numbers therefore exclude component storage. This is not a
working FP LAB UI, a rendering test, or an installable payload. Exact boundaries
and results are recorded in `analysis/native_row_construction_proof.json`.

### Connecting this to FP3K's parser hook

FP3K `tools/fp3k_native_ui.S` redirects a record at `0xC05E6400` using
reader `+36` (stream base) and `+4` (position), retaining `+20` (string pool).
After interpreting the replacement it restores the original base and resumes
after the original record. Its bounded synthetic-string resolver provides
private names without overwriting localization keys.

That establishes the mechanism. The 512-byte scratch in FP3K's version is too
small for this scene's 2,948-byte header, and one substitution per record
cannot add records, so `src/nbuinject.S` carries its own buffers and adds an
injection mode.

## What is built, and how to run it

```sh
.venv/bin/python tools/nbu_scene.py verify        # header codec, 221 scenes
.venv/bin/python tools/fplab_page.py verify       # the row, on real firmware
.venv/bin/python tools/verify_nbuinject.py        # the hook, at the real site
.venv/bin/python tools/build_combined_card.py --debug --fplab-page \
    --out builds/fplab-card
```

| Piece | What it is |
| --- | --- |
| `tools/nbu_scene.py` | Allocation-header codec and the graft that adds records with their exact header contribution |
| `tools/fplab_page.py` | Builds the new row out of stock row `B1_2_5_4` and checks it against the firmware |
| `tools/native_scene_vm.py` | Runs the firmware's interpreter, object factory and id table offline |
| `src/nbuinject.S` | 388-byte Thumb/ARM hook at `0xC05E6400`: two guarded replacements and one injected run |
| `tools/verify_nbuinject.py` | Executes that payload with the interpreter mocked one instruction in |

The card places the payload in caves checked empty in the stock image:
code `0xC0793100`, state `0xC0793400`, table `0xC0793420`, header
`0xC0793500`, Menu declaration `0xC0794100`, records `0xC07A4400`.
Each table entry carries the stock record's length and FNV-1a, so a record
that does not match byte for byte is left alone and counted at state `+0x0C`.

Current row: a copy of the last Record Settings row, 22 objects, 4,629 bytes
of records, asking for the next row slot down. Its value list is not copied,
so Right and OK are dropped with it rather than left pointing at a page that
does not exist. The focus highlight is missing for the same reason the clips
are: their header entries are positional per animation group and that mapping
is not proven.

What the first camera run has to answer: whether the row appears at all,
where it lands, whether Up/Down reach it, and whether the other four rows and
their pages still behave. Nothing here has been on a camera yet.

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
