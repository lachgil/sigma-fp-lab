# Firmware exploration: leads worth chasing

Open-ended dig through Ver.5.02, looking for capability we did not know exists.
Offline string and table analysis only — **nothing here has been written to a
camera**, and several items are guesses that say so.

Ranked by what they would be worth if true.

## 1. Binning is NOT a setting — TESTED AND CLOSED (2026-09-12, hardware)

**Result: `SetMovBiningSupport` has no consumer in the FHD CinemaDNG record
path.** Two clips of the same scene, FHD 23.976, flag off then on, recorded from
the camera itself so the flag was live at record start (USB cannot be attached
while recording, which is why this needed a menu option rather than a shell
command):

| | noise (std) | fine detail (HF) |
|---|---|---|
| binning OFF | 28.35 | 0.430 |
| binning ON | 28.59 | 0.432 |
| difference | **+0.9%** | **+0.5%** |

Noise is the decisive measure, not detail: 2×2 binning averages four
photosites, so switching it off would raise the noise floor by roughly 2× and
visibly change the high-frequency content. Under half a percent is
frame-to-frame variation. The property *does* hold the value written — it reads
back — but nothing downstream consults it when the mode is chosen.

So the picker cells really are the only route to a full-readout mode, and the
stack built for M130 is not redundant after all.

The menu option added to run this test was removed again once it was proven
inert. The mechanism it proved is kept below, because it is reusable.

### The reusable part: resident code can drive any of the 195 settings

The shell's own handler shape, minus the argument parsing, works from the
payload:

    bl 0xC0057AE8        ; fetch the settings object -> r0
    mov r1, <value>
    mov r2, #1
    blx <property_fn>    ; from analysis/menu_setters.json

Verified in emulation against the packaged binary and run on the camera without
incident, from the key-handler task rather than the shell task — the commit
function guards re-entrancy at `object+0x10`, which is what makes that safe.
Adding a setting is one row from `analysis/menu_setters.json`.

## 1b. What the original lead was, and why it looked good

`MV_Binning` appears in the movie-settings group, immediately beside the things
that obviously decide the recording mode:

    MV_DNGQuality   MV_MOVQuality   MV_Resolution
    MV_RecordingFormat   MV_FrameRate   MV_Binning

and the record path has a debug printer for it:

    [Mov]PixelBinning            %02X
    [Mov]ShutterAngle            %d / %d
    [Mov]ManGain                 %d %d
    [Mov]FrameRate               %d / %d

There is a menu item and a setter for it:

| | |
|---|---|
| `MenuItemMovieBiningSupport` | `0xC074234C` |
| `SetMovBiningSupport` | handler `0xC03FDB90`, property fn `0xC005C188`, vtable slot `+0x1A8` |

**Why this matters.** Everything we built this session forces a full-readout
sensor mode by rewriting picker cells, retuning a timing entry, publishing a
canvas and poking the raw_zoom scaler. If the mode picker takes binning as an
*input*, then turning movie binning off may make the camera choose a
full-readout mode **natively** — correct scaler, correct monitor, no hook.

That would also explain the live-view green we cannot fix: our modes are
geometrically valid but arrived at by a route the monitor path never expects.

That reasoning still looks sound; it simply is not how this firmware works.

## 2. Two facts about the setter surface, measured

These came out of the binning test and apply to all 195:

- **`Support` flags are real read/write state, not capability bits.** Read back
  changed and persist: `SetAudioRecSupport` = 1, `SetTouchOperationSupport` = 1,
  `SetMovBiningSupport` = 0 at boot and writable to 1.
- **The property system validates silently.** `SetIsoLowSensitivitySupport`
  returned `OK` and stayed `0`. A bare `OK` means nothing — **always read back.**
  This is the same trap as `setting set`, which accepted 3840 while the master
  block stayed 1920.

## 3. ISO limits look extendable

    ST_ISOBinningLimit        ST_ISOBinningExtension
    ST_ISOExtensionLowSense   ST_ISOHighestLimit
    ST_ISOLowestLimit         ST_ISOHighestLimitIndex
    MenuItemIsoBinningSupport          SetIsoBinningSupport
    MenuItemIsoHighSensitivitySupport  SetIsoHighSensitivitySupport
    MenuItemIsoLowSensitivitySupport

A limit and an *extension* are separate names, and both high- and
low-sensitivity extension have their own support flags. The fp's menu offers a
fixed ISO span; these look like the machinery that decides that span. Worth a
getter read to see what the current values are before assuming anything.

Setting all three ISO flags to 1 changed the menu's top end to 102400, but the
fp already offers extended ISO to 102400 as stock, so that observation does not
separate the two cases. The A/B (flags back to 0, look again) is still
outstanding.

## 4. Settings with no menu item, and menu items with no setter

195 setters, 285 `MenuItem*` names, and **136 menu items have no matching
setter** — so a large part of the UI is driven some other way. The suggestive
orphans:

| item | why interesting |
|---|---|
| `MenuItemShutterAngle`, `MenuItemShutterAngleLimitLow` | shutter angle as a first-class setting |
| `MenuItemFocusBracketNum/Amount/Order/Support` | focus bracketing, which the fp does not advertise |
| `MenuItemIntervalRecMin/Sec/Num` | interval recording |
| `MenuItemFocusPosition`, `MenuItemFocusLimitter` | absolute focus position and a limiter |
| `MenuItemMfRingAngleSetting`, `MenuItemIsMfRingAngleFixed` | MF ring throw |
| `MenuItemHdmiOutputZoomRatio` | zoom on the HDMI path specifically |
| `MenuItemAeBracket*`, `MenuItemWbBracket*` | AE and WB bracketing sets |

And from the setter side, things we had not looked at: `SetDvfSupport/Brand/
Model/Setting/SurroundView` (Director's Viewfinder, including a surround view),
`SetCropMode`, `SetScaleType`, `SetZoomRatio/UpperLimit/Step`,
`SetHdmiOutputSize/Format/Framerate/IsManual`, `SetTimeCode*` including
`SetTimeCodeOutputSignalOnHdmi`, and the whole `SetFunctionKey*` family.

**`SetFunctionKey*` is immediately useful to us:** we hijacked RIGHT and UP by
swapping a key descriptor, because those were the only buttons with no native
action. There is an official remapping surface — `SetFunctionKeyUp/Down/Left/
Right/Tone/Color/Mode`, `SetFunctionRec/Shutter/AEL`, and the front/rear dial
functions per exposure mode. Driving those may free a button properly instead of
fighting one.

## 5. A movie-state dump exists

The `[Mov]…` format strings are a complete record-configuration dump —
quality, pixel binning, shutter angle, manual gain, frame rate. If a shell
command reaches the function that prints them, it is a free read-out of exactly
the state we have been inferring from DNG byte arithmetic all session.

Not yet located: the strings are loaded with `movw`/`movt`, so they have no
32-bit literal references, and neither `fwmap xref` nor `af_dis xref` found the
printer. Finding it needs an instruction scan for the `movw`/`movt` immediate
pair rather than a pointer search.

## Corrections to earlier guesses in this repo

Recorded because each one nearly became a wrong turn:

- **`MenuItemFactory` is not a service menu.** It is the C++ object-factory
  pattern for building menu items — see `MenuSelectorFactory` and
  `XC_MenuSelectorFactoryInterface` beside it. `MenuFactorySettingResetHandler`
  is the ordinary "reset to factory settings" command. There is no evidence of a
  hidden engineering menu.
- **`0xC0910000..0xC0918000` is not a symbol table.** Its third word is a class
  name and its fourth increments by one per entry: class type ids for debug
  logging, not variable addresses.
- **`0xC032C720` is not the live sensor mode.** It returns 8 at every framerate.

## Suggested order

1. ~~Binning~~ — done, negative, see above.
2. ~~Does `Support` mean offered or enabled?~~ — enabled, and writes validate
   silently.
3. Finish the ISO A/B: with the flags back at 0, does the menu still reach
   102400? If it drops to 25600 the flag is the gate and is worth wiring in at
   boot.
4. Try `SetFunctionKey*` to free a button for the menu properly.
5. Find the `[Mov]` printer by instruction scan; it is the cheapest observability
   win left.
