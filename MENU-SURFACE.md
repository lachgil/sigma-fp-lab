# The menu setter surface

Offline map of every named setting the firmware's own shell can drive, built for
the next stage: manipulating menu items rather than recording modes.

Generated into [`analysis/menu_setters.json`](analysis/menu_setters.json).
Nothing here has been written to a camera yet.

## The table

`0xC0BBB534`, stride **12**, one entry per setting:

    +0  pointer to the English setter name ("SetZebraHighlightLevel")
    +4  pointer to the Japanese menu label
    +8  the shell handler

**195 entries.** It ends at `0xC0BBBE58`.

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

So each setting reduces to one **property function**, and
`analysis/menu_setters.json` resolves **192 of 195** of them. The three that do
not follow the pattern are worth a look for that reason alone.

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
