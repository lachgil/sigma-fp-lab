# Latching False Color / EL Zone

SIGMA fp, firmware Ver.5.02.

On the stock camera, False Color is a momentary function: it is shown while an
assigned button is held, and it stops when the button is released. This note
describes how to turn it on and off from your own code, so it behaves as a
toggle.

## How the camera does it

There is no stored setting for it. The assigned button calls two methods on the
CameraIF object, which post rec-manager events:

| Action | Method | Event |
|---|---|---|
| Button pressed | CameraIF vtable +0xCC, `0xC03722E8` | `0x21` |
| Button released | CameraIF vtable +0xD0, `0xC0372330` | `0x22` |

The request is built on the stack and posted through `0xC03A0798`. Nothing is
written to the settings store, so there is no flag to set and no state to read
back.

Calling the same two methods directly gives a toggle.

## Addresses

```
0xC0370CF8   CameraIF singleton getter; call it, returns the object in r0
0xC03722E8   vtable +0xCC, start
0xC0372330   vtable +0xD0, stop
```

Both methods take the object in `r0` and no other arguments.

## Code

ARM. `r0` on entry: 1 to start, 0 to stop.

```arm
false_colour:
    push    {r4, lr}
    movw    r4, #0x22E8                 @ 0xC03722E8, start
    movt    r4, #0xC037
    cmp     r0, #0
    movweq  r4, #0x2330                 @ 0xC0372330, stop
    movteq  r4, #0xC037
    movw    r0, #0x0CF8                 @ 0xC0370CF8
    movt    r0, #0xC037
    blx     r0                          @ r0 = CameraIF
    blx     r4
    pop     {r4, pc}
```

As a toggle, holding the state in your own code, since the camera does not
store it:

```arm
toggle_false:
    push    {r4, r5, r6, lr}
    LDA     r6, ST                      @ your state block
    ldr     r0, [r6, #FC_ON]
    eor     r0, r0, #1
    str     r0, [r6, #FC_ON]
    cmp     r0, #0
    LDA     r4, 0xC03722E8
    LDA     r0, 0xC0372330
    moveq   r4, r0
    LDA     r0, 0xC0370CF8
    blx     r0
    blx     r4
    pop     {r4, r5, r6, pc}
```

`LDA` is a `movw`/`movt` pair loading a 32-bit constant.

## Calling it

Call it from a key handler. That is the context the stock function key uses, on
the same thread.

It changes no picker cell, timing entry or geometry, so it needs no preset
re-latch, no recording interlock for mode safety, and nothing to release when it
is switched off. A cold boot clears it, since nothing was stored.

## EL Zone

If the camera's False Color Style is set to EL ZONE, these calls latch EL Zone.
Confirmed on hardware. The same code covers both; no separate implementation is
needed.

Event `0x21` starts the mode without specifying a style. The scale is selected
in the UI at draw time:

- Scene `B5_9` contains a container named `StyleScale` (object 33210,
  1024x64 at y=301).
- It carries a `toggleVisible` whose `select-value` is driven by an
  `appVariableEvent` bound to the app variable `CM_FalseColorAj`.
- Its two children are the scales:

| Child | Object | Ticks |
|---|---|---|
| `FalseColorScale` | 33211 | `0`, `2.5`, `18`, `Gray`, `+1`, `Stop`, `99`, `100` (IRE) |
| `ElZoneScale` | 33212 | `-6` to `-1`, `-½`, `0`, `+½`, `+1` to `+6` (stops) |

Related symbols, for selecting the style from code rather than from the camera
menu:

```
0xC005DBA0  SetFalseColorType       selects the style; does not switch the mode on
0xC073F114  MenuItemFalseColorType
0xC0CD0088  MenuFalseColorHandler
0xC0D1BE3C  FalseColorBarDrawer
0xC0BCABB4  ShellUserSettingAccessor<eFalseColorStyle, 1U>
```

Localization keys: `2158` is `EL ZONE`, `2152`-`2157` are `False Color`.

The style byte is likely `0xC3032DCE`. `ShellUserSettingAccessor` descriptors
hold a RAM address after their name pointer, and across 52 of them those
addresses are consecutive, one byte per setting (`eXC_ExpMode` `0xC3032D9B`,
`eXC_IsoStep` `0xC3032D9D`, and so on). This has not been read on a camera. To
confirm: read the byte, change Style in the camera menu, read it again.

## Behaviour while recording

During recording the request is accepted and nothing is displayed. Measured with
a resident watcher, since the USB shell cannot be used during a take:

- event `0x21` posted once a second returns success each time, with nothing on
  screen
- the rec state machine's state index does not change when recording starts, and
  the `+0x44 == 2` bail-out does not fire, so the request path is not rejecting
  it
- display returns when recording stops

A toggle's state display will therefore read on during a take while nothing is
shown.

## Notes on the analysis

- Event numbering was checked against the adjacent custom-key entry (AP
  preview), which uses `0x1F` and `0x20` from the neighbouring vtable slots.
- `gui send INTR_START_FALSE_COLOR` returns OK and has no effect; those strings
  belong to the log parser and no code references them.
- Posting `0x21` from injected code turned False Color on with no button held.
  This was confirmed on hardware.
- The scene bindings above were decoded from the firmware's component property
  tables. In this repository:
  `python tools/nbu_components.py dump B5_9 33210`.

All addresses are from Ver.5.02 and should be located again on any other
firmware version.
