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

## THE DEBUG SURFACE (2026-09-12, live on the camera)

The manual diff settled where NOT to look: every name in the setter and
menu-item tables is documented in the fp manual, so **nothing is hidden in the
menu surface**. What is not in the manual is the shell's own 77 command
families, and those are factory tools. Confirmed working on the camera:

### `gui` — a named variable interface

    gui geti <name>      get an int by NAME      gui seti <name> <v>
    gui getf / gets                              gui setf / sets
    gui scr set <screen name>   switch to a screen by name
    gui key <eXC_GuiControlType> <eXC_GuiKeyType> <on_off>
    gui send <AppSyncReqName>   gui lang <0-16>   gui mem / gc / ver

Every `MV_*` / `ST_*` / `CM_*` name in the image is readable live:

    MV_Binning -> 0    MV_Resolution -> 1    MV_FrameRate -> 3
    MV_DNGQuality -> 2    ST_ISOBinningLimit -> 0    CM_ToneControl -> 0

**Reads work; writes and navigation are REJECTED.** This needs stating clearly
because the command output is misleading:

- `gui seti MV_FrameRate 2` replies `OK:MV_FrameRate <- 2`, then `geti` returns
  3 again.
- `gui scr set UicGuiMenuMovieRecord` replies `Scr Set:UicGuiMenuMovieRecord`
  and **the screen does not change** — confirmed by watching the camera.

The internal log is what settles it. With `gui scr log 1` and then `log out
GUI`, both attempts appear as **`ERR`** entries from `0xC055F8D4` and
`0xC055FA14`, including `te <- 2` / `te <- 3`, which are the two rejected
framerate writes. So `gui`'s replies are echoes of the request, not results.

**The lesson, now seen three times on this camera:** acknowledgement is not
effect. `setting set` accepted 3840 while the master block stayed 1920;
`menu SetIsoLowSensitivitySupport 1` returned OK and stayed 0; `gui seti` and
`gui scr set` reply cheerfully and do nothing. Verify by read-back, by the
internal log, or by the camera's own screen — never by the return string.

195 `UicGui*` screen names exist and are presumably valid targets for a caller
the firmware trusts; they are listed in the image, including a full
`UicGuiMenuDngDev*` family (the in-camera DNG development UI) and
`UicGuiMenuFactorySettingReset`.

### `tsd temp` — thermal telemetry

    body 42.5C   lcd 37.5C   cmos 43.5C   battery -20.0C (unpopulated)

Tenths of a degree. **CMOS sensor temperature, live** — the missing instrument
for whether a long full-readout take is thermally limited. `tsd threshold`
suggests the trip points are settable.

### `log` — the camera's own internal logger, already running

37 channels (INIT, UI, RECMGR, CAMERAMGR, IMGCTL, MOVIE, SIGPRO, AE, AF, MENU,
DRAW, POWER...), each a 256-entry ring, **all active by default with zero
overflows**. `log out <FLAG>` dumps timestamped entries with the logging call
site and its parameters:

    log info                    per-channel counts and overwrite counts
    log out RECMGR              46 lines of timestamped record-manager trace
    log act / inact <flag>      enable or disable a channel
    log expand                  grow the buffers
    log clear                   reset
    log level / time / analyzer

This removes the blindness that shaped the whole session: **arm the log, unplug
USB, record, plug back in and dump it.** Everything we inferred from DNG byte
arithmetic can be read directly instead.

### `imager` — sensor-level tools

    mode_now / mode_list / mode_check obvalue <mode_enum> <gain> <shutter>
    mode_check eval_af / eval_wb / eval_y <mode_enum>
    debug_out mode_change | still_info | still_raw_dump | all
    reg_add_flag <flag> <sensor mode>      add registers to a mode
    testpattern    createraw    fixed_shutter    fixed_gain    high_gain_off
    crmf_off   dclp_off   defect_collect_off   pdaf_cor_off   zero_cut_raw

`mode_check` takes a **mode_enum**, so a dormant mode can be evaluated without
recording it. `debug_out mode_change` logs mode changes — which is how to prove
what the camera actually selected, the exact question that cost three rounds of
guessing. The `*_off` group disables individual raw corrections (digital clamp,
defect correction, PDAF correction) and `zero_cut_raw` looks like the
below-black clip.

**Not touched, deliberately:** every `imager exe_*` (AGC/AWB/shading/defect
calibration) and `adj_init`, because `prom write` is non-volatile and those look
like the factory calibration path. Nothing in this repo should go near them
without a specific reason.

### Others worth knowing

| command | what it offers |
|---|---|
| `event send [event] [param]` | inject UI events — a native alternative to hijacking a key |
| `ui rload <file> <bank 0-6>` | load a settings `.bin` into a bank |
| `pic set (pattern)` / `pic tbsd` | image-pipeline test patterns |
| `detect set (face) (eye) (tracking)` | detection toggles including tracking |
| `dbg disp_mess` | toggle firmware debug messages **on screen** |
| `analyzer tskmon_start/out` | task monitor — where recording time actually goes |
| `qr dump_yuv` | dump YUV from the pipeline |
| `ts ram / nand` | RAM/NAND transfer with compressed sizes |
| `device rc / mic / evf` | force accessory states |

## The auto re-latch idea — tested, not yet solved

Enabling an option currently needs the preset switched away and back by hand.
Two candidate triggers were tested live, using our own hook's probe counter as a
re-latch detector (it increments whenever the camera rebuilds the geometry row,
so it detects a re-latch over USB with no recording):

| attempt | result |
|---|---|
| rewrite `SetMovFramerate` with the SAME value | no publish, probe static |
| write a different framerate, then back | value changed both times, probe static |
| `gui seti MV_FrameRate` | returns OK, value snaps back, logged as ERR |
| `gui scr set <recording screen>` | returns OK, screen does not change, logged as ERR |

So the property write moves the master block without telling the imaging
pipeline, and the `gui` write/navigate paths are refused outright. The re-latch
must come from the UI's own apply chain, reached the way the UI reaches it.

Remaining routes, in order of promise:

1. **Find the function the menu calls when a value changes** and call it from
   the payload. The GUI log gives the call sites to start from
   (`0xC055F8D4`, `0xC055FA14`, `0xC055FA30`), and `log act MENU` plus a real
   button press on the camera will show the successful path to compare against
   the rejected one. This is the principled route.
2. `gui key <eXC_GuiControlType> <eXC_GuiKeyType> <on_off>` — the enum values
   are not in the strings, so it needs either the enum definitions or a small
   brute force over low integers.
3. `event send [event] [param]` — untried.

Worth noting the manual re-latch is a ten-second inconvenience, not a blocker,
so this ranks below anything that changes what the camera can record.

## 6. A movie-state dump exists

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
