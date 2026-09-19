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

## Toggling the user's existing button

The approach above requires the user to operate your menu. The alternative is
to leave the camera's own Custom Button Functions mapping alone and change what
the press and release do, so whichever button the user has already assigned to
False Color becomes a toggle: first press on, second press off. This works, and
was confirmed on a camera on 2026-09-19.

### Why the two methods are the right place

`0xC03722E8` and `0xC0372330` are the only implementations of these two
operations, and nothing reaches them directly. A search of the image for `B` or
`BL` to either address returns zero results; every call arrives through the
CameraIF vtable at `0xC0B9928C` (+0xCC and +0xD0). Patching the two method
bodies therefore covers every caller, whatever button is mapped and whichever
code path dispatches it.

Both are short, and the body is the same except for the event number:

```
0xC03722E8  push {r4, lr}            <- the displaced instruction, 4 bytes
            sub  sp, sp, #0xBC
            mov  r4, r0              <- the CameraIF object
            ...  zero the 0xBC-byte request with 0xC0015058
            mov  r1, #0x21           <- 0x22 in the stop method
            str  r1, [sp]            <- event id at request +0x00
            mov  r1, #1
            strb r1, [sp, #4]        <- flag byte at request +0x04
            ldr  r0, [r4, #4]        <- the rec-manager target
            mov  r1, sp
            bl   0xC03A0798          <- post
            add  sp, sp, #0xBC
            pop  {r4, pc}
```

That is the whole protocol: a zeroed 0xBC-byte request, the event id at +0x00,
`1` at +0x04, posted to `[object+4]`. A hook can post either event itself
without calling back into the patched methods, which avoids re-entrancy.

### Behaviour, confirmed on hardware 2026-09-19

- `0xC03722E8` (press): flip a flag of your own and post `0x21` when it becomes
  set, `0x22` when it becomes clear. One press on, one press off.
- `0xC0372330` (release): return without posting, so releasing the button no
  longer cancels the mode. The stock method returns the post's result and
  callers read it as a success flag, so return 1.

Both sites start with `push {r4, lr}`, so the displaced instruction is one word
and the hook is entered with a plain `B`.

Implementation in this repository: `src/fclatch.S`, built by
`tools/build_fclatch_autorun.py` into a standalone AutoRun with nothing else in
it. The ready-made file is `cards/AutoRun-fc-latch.txt`.

Checked in an emulator against the Ver.5.02 image before it was booted: six
alternating calls produced `0x21`, nothing, `0x22`, nothing, `0x21`, nothing,
and the posted request is byte-identical to the stock method's (same target
from `[object+4]`, event id at +0x00, `1` at +0x04, remaining 0xBC bytes zero).
Then confirmed on a camera: the assigned button toggles the mode on and off.

### What is still untested

Whether anything other than the button release calls +0xD0. If the firmware
also stops the mode when entering playback, opening the menu, starting a
recording or going into power save, a hook that swallows every stop can leave
the mode latched when the camera expects it off. Toggling itself is confirmed;
these transitions are not.

This is answerable without guessing. Hook `0xC0372330` so that it records the
caller's `lr` and then performs the stock post, and drive the camera through
those transitions with False Color active:

- press and release the mapped button
- enter and leave playback
- open and close the menu
- start and stop a recording
- let it idle into power save

If every entry in the log carries the same `lr`, the release path is the only
caller and swallowing it unconditionally is safe. If other `lr` values appear,
the hook should swallow only the button path and let the rest run, which the
same log will have identified by address.

### Notes

- The mapping itself does not need to be read. The hook sits below the
  dispatcher, so it does not matter which button or which function slot the
  user chose.
- The same hook covers EL Zone, for the reason given above: the style selects
  the scale, not the code path.
- Reading the mapping is still useful for a state display, since a toggle that
  shows its state has to know the mode exists on this body's configuration.
  `MenuItemFalseColorType` (`0xC073F114`) and the Custom Button items are the
  place to start.
- The interaction with recording is already known: the request is accepted and
  nothing is drawn, so a press during a take will flip the flag and appear to do
  nothing.
