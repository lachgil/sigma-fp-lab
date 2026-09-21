# The menu setter surface

Offline map of every named setting the firmware's own shell can drive, built for
the next stage: manipulating menu items rather than recording modes.

Generated into `analysis/menu_setters.json` (generated locally, not published).
The original setter analysis below is distinct from native UI resources.
Some setters and text writes have now been exercised on hardware; read the
dated evidence before treating a stored value as an observable UI change.

## Native menu resources, 2026-09-12

`menu_resources.py` is an offline, read-only inspector for the extracted fp 5.02
MAIN image. No firmware patch, card rebuild, or native menu insertion is claimed.

```
.venv/bin/python menu_resources.py list MainMenu
.venv/bin/python menu_resources.py show ../MainMenu/data/ListMenu/B1_4.cvm
.venv/bin/python menu_resources.py text 0711
.venv/bin/python menu_resources.py scene MainB5 --filter text=
```

### Formats verified against the complete image

- NBR at `0xC0D22400`: chunk 1 starts at base+12, has BE32 type 1 and
  size `0x2B5A4`. This is a **chunk size**, not the archive size.
- Chunk 2 at `0xC0D4D9B0`: type 2, size `0xB452C8`, 2,259 entries.
  Directory starts at `0xC0D4D9BC`; each 16-byte BE record contains
  `{FNV1a(path), name_offset, payload_offset, size}`.
  Names are relative to `0xC0D22414`; payloads to `0xC0D22400`.
  All hashes, unique names, and payload bounds verified. Sorted by payload
  address, every resource follows the preceding resource at align4(size).
- 195 `.cvm` payloads decode as UTF-8 CSV, including `TEXT`, `Popup`, `IMAGE`
  and enable-state columns. These are actual payloads, not just path strings.
  Their column names do not establish runtime mutation semantics.
- English localization is `English.nloc` at `0xC0D900AC`, size 112,724:
  little-endian NDB header, count at +8, 8-byte entries at +12
  `{FNV1a(key), packed}`. Record offset is packed>>8; low byte is zero.
  Record starts with relative key/value offsets. Verified all 2,728 entries
  in each of all 17 locales.
- NBU UI definitions at `0xC18C0460`, ending `0xC2D8CEF8`. The name chunk is
  followed by a type `0x10001` directory of 221 32-byte scene records.
  Fields +0/+4 are FNV1a(name)/name-pool offset; +12 is scene offset relative
  to NBU base. Every scene starts with a `0x10002` chunk.
  All 36,379 object declarations decode across 221 scenes.
  **NBU chunks are not word-aligned**: advance by the exact BE size.
  An aligned-word search misses real bindings.

### The user's exact Shoot page 5

Scene `MainB5` spans `0xC2075687..0xC2094712`. Its decoded title bindings
match the user's reported CINE page:

| Row/title | Localization key |
|---|---|
| Frame Guide | `0588_C` |
| Director's Viewfinder | `0602_C` |
| Brightness Level Monitor | `0706_C` |
| Zebra Pattern | `0711` |
| False Color | `2154` |

### Hardware result: the scene binding DID rename the native item

Confirmed by the user on 2026-09-12. Changing the packaged English string had no
visible effect; changing the scene's `drawText` key offset did.

    0xC2091858   0x01CC1600 -> 0x01D84500      (key 0711 -> 2154)

The Zebra row displayed **False Color** after the menu was closed and reopened,
giving two False Color entries. Restored to `0x01CC1600` and read back.

Two consequences:

- The NBU scene data is **live**, not a boot-time-only copy. The page is rebuilt
  when it is entered, so a word poke is enough and no card rebuild is needed.
- The earlier `English.nloc` edit failing is now explained: it is the *source*
  the binding points at, and the binding is what the row reads.

### What a row is made of

A row such as `B5_8` is a `MenuItem_Jump` object plus records that are all data:

| record | meaning |
|---|---|
| `drawText` on its `Name` child | the label, via a localization key |
| `keyEvent Right` -> `controlAppState` | which page it opens |
| `keyEvent Up`/`Down` -> `controlFocus` | cursor movement |
| `focusEvent` -> `controlAnimation` | highlight |
| `animationClip` on `is-visible` | whether the row appears |

Still-only rows (`B5_1`, `B5_3`) carry a 0x44-byte `is-visible` clip with one
entry; rows visible in CINE (`B5_4`, `B5_7_CINE`, `B5_8`) carry 0x62–0x6C bytes
with two. That is the still/cine gate, and it is why the user's CINE page shows
exactly `B5_4`, `B5_5`, `B5_7_CINE`, `B5_8`, `B5_9` out of the nine defined rows.

### Adding a row: what is actually possible

All 303,600 NBU chunks are **byte-packed with zero gaps** across the whole
0x14CCA98-byte span, and each carries its own size. So there is no slack to
insert a row into, and growing one chunk would require rewriting everything
after it. **Adding a brand-new row by live word pokes is not possible.**

What is possible, and needs no new bytes:

- **Repurpose an existing row** — relabel it and retarget its jump. Both halves
  are single-word edits; the label half is confirmed on hardware.
- Parser-time substitution can add records without moving the stock stream.
  Whole-scene relocation also needs the runtime resource descriptor, not just
  its serialized directory entry. See
  [the loader investigation](gui-resources.md#firmware-loader-investigation-and-status-fix-2026-09-21).

Repurposing an existing row is one experiment, not a limit on the user's
requested full native menu.

### Cautions specific to this area

- Scenes `Blank` and `Test` contain a `Root` node and nothing else — no
  `keyEvent`, so no way back out. Do not retarget a row at them.
- `Test_001` is a fully built dormant screen with its own header, footer, key
  events and `changePropertyByControl` records. Reachable in principle, but what
  it writes is unknown, so it needs consent before being opened.

### The item-id table: a repointable function pointer per menu item

`0xC00B81E8` is the item dispatcher. It bounds-checks an item id against
**0x127 (295)** and then calls straight out of a table:

    movw ip, #0x61dc ; movt ip, #0xC074      ; table at 0xC07461DC
    ldr  r3, [ip, r4, lsl #2]                ; r4 = item id
    blx  r3

273 of the 295 entries are populated. Each target is a small accessor of one
shape -- two calls to reach the settings object, then a fixed offset:

    bl 0xC008ACF0 ; bl 0xC00897E8 ; movw sb,#<offset> ; add r2,r2,sb ; return r2

163 entries decode to a literal offset that way, spanning `+0x199..+0x29FC`.
Zebra Pattern is item id **0x9C**, entry `0xC074644C`, accessor `0xC00B67F8`,
offset **+0x524**. The neighbouring accessor returns `+0x528`.

Around those offsets the object holds 0x1C-byte records --
`{owner, vtable, value, pointer, type, id, 0}` -- and the accessor returns the
address of the **value** field, i.e. record+8.

**Why this matters more than the scene edits.** The binding between a menu row
and the value it edits is a *function pointer in writable data*. Repointing one
entry re-binds that row to a different value, and an accessor of our own in the
cave would bind a row to a word we choose -- which is the mechanism a native
"OPEN GATE" toggle needs, with no key hijack and no OSD.

### What is NOT yet established here

- **The live object base is unknown.** `0xC008ACF0` initialises a singleton
  around `0xC31AE3A8`/`0xC31AE3B0`, but `0xC31AE3B0` reads as 0, so the base is
  computed, not stored there. Offsets are solid; the base is not.
- **`menu <Setter>` cannot be used as the oracle for it.** Writing
  `0xC31AC59C+0x524` left `SetZebraPattern` unchanged, and writing the one live
  word holding 90 (`0xC31ACA78`) left `SetZebraHighlightLevel` reading 90. The
  getter tracks the property mirror, not these slots. Both writes were reverted
  and re-read as stock.
- So the next step needs a **real oracle**: either the screen, or a payload that
  does the whole `0xC008ACF0`/`0xC00897E8` walk in one routine and reports the
  base. A host-side pointer walk is exactly the trap the fp-usb-shell notes
  describe, and should not be attempted across separate `mem get` calls.

### Also learned on hardware: a row's jump is not the whole action

Retargeting only the `Right` record left **OK** still opening the stock page,
and after both were retargeted the **Menu/back key stopped working inside the
opened page**. The `Right` record group also writes `MENU_ReturnScreen` and two
other app variables, which is how the opened page finds its way home. A correct
retarget has to rewrite the jump *and* its return context together; `menu_row.py`
does not do that yet, which is why the row was restored to stock.

### Resolved: the settings store base, and the item name -> id -> slot map

The accessor chain is computable offline and agrees with the camera:

    0xC008ACF0  returns 0xC31AE3B0 (the singleton's address, not a pointer)
    0xC00897E8  reads the flag byte at 0xC31B954C; 0 -> +0x4F0C, else +0xA7D4
    base       = 0xC31AE3B0 + 0x4F0C = 0xC31B32BC

`menu dump` independently reports `addr=C31B32BC , size=2496`, which is the same
address, and Bei's note reaches it from the other direction.

**Values are bytes, not words** -- odd offsets such as `+0x601` are normal.

Two accessor encodings appear at function+0x18, and decoding only the first
loses 39 items:

    movw sb, #imm16     large offsets (Zebra Pattern +0x524)
    add  r2, r2, #imm   small offsets (Slideshow Repeat +0x28)

With both, **202 of 295** ids resolve to a slot.

The id is also recoverable from the RTTI-style record that follows each
`MenuItem*` name string in `0xC0730000..0xC0760000`: the word at name+20 is
`0xC3032515 + id`. That names **73** ids, and it is self-checking --
`0x9C` comes out as `MenuItemZebraPattern` and `0x9D` as
`MenuItemZebraHighlightLevel`.

Verified against the live camera, which is what makes the map trustworthy:

| id | name | slot | live |
|---|---|---|---|
| `0x9C` | `MenuItemZebraPattern` | `0xC31B37E0` | 0 |
| `0x9D` | `MenuItemZebraHighlightLevel` | `0xC31B37E4` | **90** |
| `0x20` | `MenuItemSlideshowDisplayTime` | `0xC31B38B8` | 0 |
| `0x21` | `MenuItemSlideshowRepeat` | `0xC31B32E4` | 0 |

`0x9D` reading exactly the 90 that `SetZebraHighlightLevel` reports, uniquely
among 163 items, is the evidence the base and the offsets are both right.

`menu_row.py slots [--only toggles]` prints the whole map with live values.

### The chosen switch for a native feature toggle

`MenuItemSlideshowRepeat` (`0xC31B32E4`), reached by
**Playback -> Slideshow -> Repeat**, row `R1_6` of scene `MainR1`, label key
`0832`. It is an On/Off the firmware itself stores, it lives in a playback menu
so nothing is lost while shooting, and the payload can poll one byte instead of
hijacking RIGHT/UP.

Still unverified: that the UI *writes* that byte when the row is toggled. Reads
are confirmed; the write direction needs one look at the camera.

### The property path does NOT write the store (measured)

`menu SetSlideshowRepeat 1` moved the getter 0 -> 1 while the byte at
`0xC31B32E4` stayed `00`, and reverting moved the getter back. Likewise
`SetSlideshowDisplayTime` reads **2** while its slot `+0x5FC` reads **0**.

So there are two stores, and they are not kept in step:

| | written by | read by |
|---|---|---|
| property object | `menu <Setter>`, and the getter | the subsystems |
| this store (`0xC31B32BC`) | ? -- not the property path | the menu items, through the id table |

That is consistent with everything else seen this session: the earlier attempt
to use `menu <Setter>` as an oracle for a slot failed in exactly this way, in
both directions.

It does not sink the plan -- a byte the **UI** writes is precisely what a native
toggle needs -- but it does mean the write direction cannot be proven from the
host. The remaining question is a single one: does toggling
**Playback -> Slideshow -> Repeat** on the body change `0xC31B32E4`?

- If yes: the payload polls that byte and Open Gate becomes a native item.
- If no: the store is a display cache, the item table binds reads only, and the
  switch has to come from the property object instead -- in which case the
  target is the property function for `SetSlideshowRepeat` (`0xC005ECF8`),
  already resolved in `analysis/menu_setters.json`.

Both outcomes are useful; neither is guessable from here.



### Finding the base without running code

The payload is resident on this camera -- `ST` at `0xC072FB00` is populated,
the rowpatch cave at `0xC072F800` holds code, and telemetry at `0xC072FA00` is
live -- so the usual borrow-a-command probe would land on our own scratch. The
cheap alternative is to let the camera write the value for us:

1. snapshot a window of the settings region over the shell,
2. change one setting **on the body**, choosing a distinctive value,
3. re-read and diff: the word that moved is the live slot.

That gives `base = slot - offset` for an item whose accessor offset is already
decoded, with no code execution and nothing to restore. Zebra Highlight Level is
a good choice because it is numeric, display-only and reversible.


### Correcting the record on the metadata registry

A note circulated separately describes patching per-item **value metadata** in
`0xC0700000..0xC0760000` (e.g. `MenuItemMovieRecSize` at `0xC0742204`: type tag
22, then `{2: 1920x1080, 3: 3840x2160}`, then an inline `Invalid MovSize`).
Verified against this image and correct as written, though its
`MenuItemHdmiOutputFormat` listing omits `1280x720` and its field offsets shift
with name length.

That mechanism and this one are **different layers**: metadata changes what a
setter accepts and computes, the scene changes what the row displays and opens.
Neither adds an item. Its stated blocker -- master block moved but the DNGs
stayed 1936x1090 -- is the part this project already solved through the picker
cells and the per-profile `raw_zoom` scaler, which is what produced sustained
3968x2640 files.

Worth adopting from it: `menu dump` (2496 bytes at `0xC31B32BC`) diffed across a
menu change locates a setting's storage far more cheaply than inferring it from
DNG bytes.

### The earlier negative result, kept because it is instructive

Four English `English.nloc` copies were changed to `fpLAB ZEBRA` and read back,
and the native label did not change. The scene decode later showed `0711` was
among them, so the copies were right and the *layer* was wrong.

`menu_text.py` now bounds writes to original UTF-8 bytes plus one NUL, preserves
neighboring words, preflights all copies, checks every write and supports
`restore ORIGINAL REPLACEMENT`. Following zeros are not spare capacity.
Live restore/reapply matched all four 20-byte windows exactly. After the
negative LCD report, all four copies were restored to `Zebra Pattern`.
Five boundary/error regression tests pass. It edits the localization source,
which is one layer below the binding that the row actually reads.

### Native C++ objects, separate from the shell setter registry

Offline ctor at `0xC007CE28..0xC007CE58` initializes Zebra model ID `0x9C`
at factory+`0x1074`, with vptr `0xC073EF10` at model+12.
Live read at `0xC31AD610` returned
`{0x9C, 0, 0xC31AC540, 0xC073EF10, 0, 0}`.
Screen singleton `[0xC37B76F0]` read `0xC3784C08`.
These establish actual runtime objects, not permission to invoke their methods
from the shell or key-handler task. No native handler has been redirected.

Prior claims that logger return sites prove a callable live-view stop/start
pair, that Ricoh font filenames identify the whole UI toolkit, or that every
Support flag enables a hidden feature remain unsupported.

## The table

The original partial scan covered `0xC0BBB534`, stride **12**:

    +0  pointer to the English setter name ("SetZebraHighlightLevel")
    +4  pointer to the Japanese menu label
    +8  the shell handler

That scan covered 195 entries through `0xC0BBBE58`, not the complete table.
The later generated map has 281 candidates, 274 resolved property functions;
its first candidate is a descriptive string, so do not equate the candidate
count with a verified count of settings.

## Every handler has the same shape

Disassembling any of them gives the same five steps, which is what makes the
table mechanically useful:

```
push {r4-r7, lr}
mov  r4,r0 / r5,r1 / r6,r2     ; ctx, argc, argv
cmp  r5, #1                    ; with an argument it SETS, without it GETS
ldr  r0, [r6]
bl   0xC01F5898                ; strtol(argv[0], 0, 10)
bl   0xC0057AE8                ; fetch the settings object
bl   <property_fn>             ; (object, value, 1)
```

The original partial scan resolved 192 of 195 entries. See the generated map
for the expanded candidate set; unresolved entries require individual review.

Two consequences:

- `menu <Setter>` with no argument is a **getter**; with one it **sets**. Both
  go through the property system, unlike `setting set`, which writes only a
  mirror. (Measured previously: `cam_movie_imagesize.h` accepted 3840 and read
  back 3840 while the master block stayed 1920.)
- Resident code can call a property function **directly**, with values the UI
  never offers. That is the same trick that made the on-screen menu possible:
  call the leaf, not the dispatcher.

## The highlight-monitor group

This is the cluster to work on, and it is all one mechanism:

| setting | handler | property fn |
|---|---|---|
| `SetToneControlMode` | `0xC03FE460` | `0xC005CDA8` |
| `SetToneManualHighlight` | `0xC03FE4D0` | `0xC005CE40` |
| `SetToneManualShadow` | `0xC03FE540` | `0xC005CED8` |
| `SetFalseColorType` | `0xC0402280` | `0xC005DBA0` |
| `SetZebraPattern` | `0xC03FF998` | `0xC005E708` |
| `SetZebraHighlightLevel` | `0xC03FFA08` | `0xC005E7A0` |
| `SetZebraHighlightColor` | `0xC03FFA78` | `0xC005E838` |
| `SetZebraExpLevel` | `0xC03FFAE8` | `0xC005E8D0` |
| `SetZebraExpRange` | `0xC03FFB58` | `0xC005E968` |
| `SetZebraExpColor` | `0xC03FFBC8` | `0xC005EA00` |
| `SetDisplayZebraPattern` | `0xC0402480` | `0xC00624D8` |

Zebra **level** and **range** are numeric, so the first question is whether they
accept values outside the menu's choices — that alone may be the difference
between a usable highlight monitor and the current one.

## Tone curve

There is a separate DNG-development tone path, which is the likely home of a
"make the built-in curve linear" change:

    0xC0CC66FC  MenuDngDevToneControlHandler
    0xC0D15AFC  CM_DNGDEV_ToneControl
    0xC0D1654C  UicGuiMenuDngDevToneControl
    0xC0D1123C  CM_ToneControl          (and several CM_CQSS_ToneControl)

`CM_` looks like a camera-mode/config prefix and `CQSS` like a quality/still
subset, so the same control appears per capture path. Worth separating which one
the CinemaDNG monitor actually consults before changing anything.

## What this does NOT establish

- The `0xC0910000..0xC0918000` region looks like a symbol table but is not one:
  its third word is a class name and its fourth increments by 1 per entry, so
  those are **class type ids for debug logging, not variable addresses**. An
  earlier reading of it as "name -> live RAM address" was wrong.
- No value ranges are known yet. Each property function needs disassembling, or
  a getter read on a live camera, before anything is written.
- Nothing about whether the highlight monitor's thresholds reflect the raw
  clipping point in the unlocked modes, which is the actual complaint.

## What the property path does with a value (offline)

Each property function is the same three steps, and the only thing that differs
between settings is a **vtable slot offset**:

| setting | slot | commit fn |
|---|---|---|
| `SetToneControlMode` | `+0x1F8` | `0xC00649D0` |
| `SetZebraHighlightLevel` | `+0x298` | `0xC0064580` |
| `SetZebraExpRange` | `+0x2A4` | `0xC0064580` |

    ldr r0,[r4] / ldr r3,[r0,#4] / add r3,#<slot> / ldr r3,[r3] / blx r3
    mov r2,#0xFFFF ; mov r1,<value> ; bl <commit>

Both commit functions are byte-identical in shape: a re-entrancy guard at
`obj+0x10`, a vtable call at `[obj+0xC]+0x1C`, then `0xC00913F8` with
`r3 = 0x80000002`. **Neither contains a numeric clamp** in its prologue -- the
value is carried through untouched.

That is the interesting part for a highlight monitor: the **menu** restricts
which values you can pick, but this path may not. If so, zebra level and range
can be set to values the UI never offers, which is a one-command experiment
rather than a patch. Unproven -- a validator may live behind the vtable call at
`[obj+0xC]+0x1C`, and the consumer may clamp instead.

## The DNG-dev menu is NOT the recording tone curve

`MenuDngDev*` is the **in-camera DNG development** feature -- the same
neighbourhood holds `MenuDngDevExposureCompensationHandler`,
`WhiteBalanceHandler`, `WbColorTempHandler`, `ImageQualityHandler`,
`ImageSizeHandler`, `AspectRatioHandler`, `ColorModeHandler`,
`ColorSpaceHandler`. These are class-name registry entries (name pointer plus a
type id), so they describe the develop-a-DNG-in-camera UI, not what happens
while recording.

Which matters for the goal: **CinemaDNG is raw, so no tone curve is baked into
the recording at all.** A "built-in tone curve set to linear" therefore acts on
the **monitor** (and MOV), which is precisely why it would help highlight
judgement -- you cannot see clipping through a display contrast curve. So the
target is the monitor/display curve or `SetToneControlMode`, not the DNG-dev
path, and the two should not be confused while bisecting.

## Suggested order

1. Read the current values of the eleven settings above with `menu <Setter>` on
   a live camera, so there is a baseline to return to.
2. Disassemble `0xC005E7A0` (zebra level) and `0xC005E968` (range) for their
   clamp, to learn whether out-of-menu values are accepted or rejected.
3. Try an out-of-menu value for `SetZebraHighlightLevel` and read it back: the
   commit path shows no clamp, so this is the cheapest test of the whole idea.
4. Only then write more, one setting at a time, checking the screen each time.
5. For the tone curve, establish first whether the change is to the monitor
   path or `SetToneControlMode` -- asking whoever did it is faster than
   bisecting, since CinemaDNG itself carries no curve.

## Adding a native row: what was tried on hardware, 2026-09-14

Four routes were tested on the camera. **None of them put a new row on screen**,
and the negative results are worth more than the attempts.

**Changing the serialized directory word did not relocate the displayed page.**
Each scene's payload offset is one big-endian word in the directory (MainB5 at
`0xC18EC768`, MainB1 at `0xC18EC7E8`, offset from the NBU base). A byte-identical
127 KB copy of MainB5 was assembled in our own RAM with an on-camera memcpy and
the word repointed at it: every Shooting page still rendered normally. That
looked like success and was not -- an identical copy renders the same whether it
is read or ignored. The decisive test came later: with a page relocated, an
edit *inside our copy* changed nothing on screen. The camera is still drawing
from the original. Do not repeat the identical-copy test and call it proof.

**Rebuilt page bytes were verified in camera memory, not demonstrated as loaded.**
MainB5 was rebuilt with 226 objects (up from 215), MainB1 with 231 (up from 215).
Container child count, header object count and the per-record census were
updated. Absence of a loader complaint does not establish structural acceptance
when the relocated bytes were not shown to be consumed.

**Pages are six fixed slots, not a list.** Row objects carry an absolute y in
their `0x10004` objectBase: 0, 81, 162, 243, 324, 405. `_STILL` and `_CINE` rows
are ALTERNATES for one slot, chosen by condition, which is why a page with nine
children shows four to six rows depending on the mode. A cloned row inherits the
y of whatever it was cloned from and lands exactly on top of it, invisible.

**An in-place scene edit did not show either.** With everything back at stock
addresses, `B1_5`'s drawText key at `0xC202C19C` was changed to another string,
read back correct, and the menu closed and reopened: the row kept its old name.
That contradicts the 2026-09-12 Zebra rename, which is recorded here as having
worked on `MainB5`. Something differs between those two cases -- scene, record,
or what is cached when -- and it is NOT established which.

**Follow-up, 2026-09-21:** the `.cvm` layer supplies choices within existing
widgets, as FP3K's working third Resolution choice demonstrates. It does not
replace native scene registration for a new page.

Real directory parsing now demonstrates the separate runtime descriptor:
44-byte entries at reader `+0xAC`, count at `+0xA8`, with the copied scene offset
at entry `+8`. Scene loader `C05E82F0` uses this copy. A boot-time serialized
directory edit is therefore not the only possible intervention. The native
constructor/registry path and its unverified application-state navigation
boundary are documented in
[gui-resources.md](gui-resources.md#firmware-loader-investigation-and-status-fix-2026-09-21).
