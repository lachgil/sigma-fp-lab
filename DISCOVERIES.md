# Firmware exploration: leads worth chasing

Open-ended dig through Ver.5.02, looking for capability we did not know exists.
Offline string and table analysis only — **nothing here has been written to a
camera**, and several items are guesses that say so.

Ranked by what they would be worth if true.

## 1. Binning may be a *setting*, not a fact

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

**The test is one command and costs nothing:** read `menu SetMovBiningSupport`
for a baseline, set the other value, then ask `imager mode_now` whether the
chosen mode changed, and record a clip. Read-back and reversible.

**Caveat, stated plainly:** "Support" in these names often means "is this
feature offered", not "is it on" — several `Set*Support` settings in the table
read like capability flags. It may do nothing, or refuse. Unproven.

## 2. ISO limits look extendable

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

## 3. Settings with no menu item, and menu items with no setter

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

## 4. A movie-state dump exists

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

1. `menu SetMovBiningSupport` (read), then flip it and ask `imager mode_now`.
   Highest possible payoff, one command, reversible.
2. Read every `Set*Support` getter to learn whether "Support" means offered or
   enabled. That single answer tells us how much of this table is actionable.
3. Read the ISO limit/extension values.
4. Try `SetFunctionKey*` to free a button for the menu properly.
5. Find the `[Mov]` printer by instruction scan; it is the cheapest observability
   win left.
