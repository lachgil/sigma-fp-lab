# The native menu is data, not code

> **Read `gui-resources.md` first.** ijigen's handoff tree measured this
> subsystem properly on hardware, including the one result that decides the
> whole question: value lists are built **once at startup**, and the
> enum→state table at `0xC2E4020C` is the part that patches at runtime. The
> notes below are our own earlier, shallower look at the same area.

Credit where it is due: this came from **FP3K 0.3.5** (Vitaly). Its `AutoRun.txt`
ends with `mem save` lines that dump four regions to the card, and the addresses
are the point, not the dumps:

```
mem save \FP3KM2\RANGE.BIN 0xC1A7099C,,40
mem save \FP3KM2\BOOT.BIN  0xC0732500,,16
mem save \FP3KM2\MENU.BIN  0xC0732000,,64
mem save \FP3KM2\LIST.BIN  0xC0F8E7EC,,89
```

**`FP3KMENU.BIN` is the payload, not a dump** (I got this wrong at first: the
`mem save` lines are diagnostics that write the loaded code back out, and the
sizes match, which is misleading). The AutoRun is a loader: it opens the file,
reads `0x3D04` = 15620 bytes into `0xC072DE64`, sums `0xF41` words and requires
zero, then `blx`es it. Everything FP3K does lives in that binary.

## What FP3K's payload actually does

**It adds a real third entry to the native Resolution list** (UHD / FHD /
3000x2000), confirmed by photograph of the camera. The recipe is in the shipped
sources named on line 348 of his handoff, `tools/fp3k_native_menu.S` and
`tools/fp3k_native_ui.S`, not in `analysis/native-menu-integration.md`, which is
an earlier "not done yet" analysis and should not be read as the final word.

Three ingredients, all of them RAM writes at install time:

```asm
/* 1. widen the widget's range: two entries -> three */
addr r0, UI_RANGE          @ 0xC1A709BC
mov  r1, #UI_RANGE_THREE   @ 0x00000040   (stock reads 0x0000803F)
str  r1, [r0]

/* 2. replace the 89-byte list CSV with his own */
addr r0, 0xC0F8E7EC
self r1, csv_new
mov  r2, #89
5:  ldrb r3,[r1],#1 ; strb r3,[r0],#1 ; subs r2,r2,#1 ; bne 5b

/* 3. hook the resource pack load so the new entry has artwork */
ui_load: ... if the requested pack is 0xC0D22400 -> ensure_ui_pack
```

Both values verified in our own image: `0xC1A709BC` reads `0x803F`, and
`0xC0F8E7EC` is the 89-byte CSV with two rows. So `LIST.BIN` in his AutoRun dump
was the resolution list all along.

He also installs with a checksum over the payload, verifies every patched word
reads back, and rolls back if any does not. Worth copying, not just the patch.

For reference, what its payload does touch:

- `MV_Resolution`, `LV_Resolution` -- the GUI variables for the recording and
  live-view resolution lists.
- `FP3K_L.xci`, `FP3K_M.xci`, `FP3K_QS.xci`, `FP3K_SET.xci`, `FP3K_FONT.xci`,
  registered through `xcimage`: it ships **its own icon assets** so the new
  entry has artwork in every place the UI draws one.
- `0xC005C020` (`SetMovRecSize`) and `0xC005C0B8` (`SetMovFramerate`) -- the
  same property setters our menu already uses for the re-latch.

So the recipe for a native entry is: register assets, extend the resolution
list, and drive the existing setters. It does **not** appear to patch the CSV
tables below, so those remain a separate, unexplored lead.

Worth knowing before anyone repeats this: ijigen's measurements show the
resolution array is built once during the startup resource pass and is never
rebuilt, so any hook that adds an entry must be armed before that. Whether an
AutoRun runs early enough is an open, testable question -- see
`gui-resources.md`.

## What LIST is

`0xC0F8E7EC` is a UTF-8 CSV **with a byte-order mark**, sitting in the firmware
image:

```
NO,TEXT,Popup,IMAGE,Enabled,Enabled2
1,0313,Popup,blank,1,2
2,0314,Popup,blank,1,2
```

It is not the only one. **195 such tables** carry a `BOM + "NO,"` header, in
several shapes:

| Count | Header | Example |
|---|---|---|
| 137 | `NO,TEXT,Popup,IMAGE,Enabled,Enabled2` | `0xC0D56C5C` |
| 13 | `NO,TEXT,Popup,IMAGE,Enabled,Enabled2,Index` | `0xC0F89B98` |
| 10 | `NO,ID,IMAGE,KEY,memo` | `0xC0F930D4` |
| 9 | `NO,IMAGE,POPUP` | `0xC0F92AA4` |
| 8 | `NO,IMAGE,memo` | `0xC0F90EF8` |
| 5 | `NO,IMAGE,POPUP,EX1,Left,Right,FocusOn` | `0xC0F922AC` |

The columns mean what they say. `TEXT` and `KEY` are string ids, `IMAGE` is an
icon name that matches the `.xci` assets (`set_imgSize_fhd`), and `Enabled`
gates the row.

Two worth knowing by name:

- **`0xC0F89B98` — the image-size popup**, one row per entry with an `Index`
  the handler switches on:
  ```
  0,0249,Popup,set_imageSize_1,1,2,0
  4,1869,Popup,set_imgSize_9_5K,1,2,4
  8,1873,Popup,set_imgSize_fhd,1,2,8
  ```
- **`0xC0F930D4` and siblings — the quick-settings screens**, one table per
  camera mode, each row an `ID` the UI dispatches on:
  ```
  1,0,menu_mode,1093
  5,4,menu_iso,1098
  10,21,menu_cine_resolution,1116
  ```

## Why this matters to this project

Our menu is drawn by us, in our own layer, because reaching the camera's own
menu looked like it needed a handler contract we did not have (see `ui.md`, and
the Zebra-row experiments in `surface.md`). That conclusion was reached without
ever reading these tables. A row in SIGMA's own menu may be **a CSV row plus an
existing id**, not new code.

What is genuinely unknown, and must be established before claiming anything:

1. **When are these parsed?** If at screen build, patching before the UI runs is
   enough, and our AutoRun already runs first. If cached, the cache is the
   target instead.
2. **Is a new row's behaviour data or code?** Repointing `TEXT`/`IMAGE` on an
   existing row is clearly data. Adding a row with an id nothing dispatches is
   almost certainly not.
3. **Are there rows disabled by the `Enabled` columns** that the firmware still
   knows how to run? That is the cheapest possible win and worth grepping for
   before anything is written.

Nothing here is proven on hardware yet. It is an unexplored, promising path that
was in the FP3K package the whole time.
