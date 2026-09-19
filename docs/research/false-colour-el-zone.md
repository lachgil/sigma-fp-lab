# Latching False Color / EL Zone on the SIGMA fp (Ver.5.02)

**What this gives you:** False Color, or EL Zone, as an on/off switch instead of
a button you have to hold down. Works from any menu or payload of your own.
About ten instructions. Nothing is written to flash, nothing persistent is
written at all.

This is a complete recipe. You do not need anything else from this repo.

---

## 1. Why it cannot be done as a setting

False colour is **not a stored setting on this camera**. Turning it on moves
nothing:

- `pic_false_color_on` does not change
- `CM_FalseColor` does not change
- not one byte moves in a 128 KB diff of the settings store

The camera even tells you so. Localization key `2159` reads *"Assign False Color
to a button on the Custom Button Functions menu."* There is no switch to find,
which is why poking at RAM to find one is wasted effort.

What actually happens is that a function key **posts an event while held**:

| Action | Call | Posts |
|---|---|---|
| Key pressed | CameraIF vtable **+0xCC** = `0xC03722E8` | rec-manager event **0x21** |
| Key released | CameraIF vtable **+0xD0** = `0xC0372330` | rec-manager event **0x22** |

The request is built on the stack and posted through `0xC03A0798`. That is the
whole mechanism, and it is why nothing persists.

So: call those two methods yourself, and it becomes a latch. You keep the on/off
state, because the camera does not.

## 2. The code

Three addresses, firmware **Ver.5.02 only**:

```
0xC0370CF8   get the CameraIF singleton    (call it, returns the object in r0)
0xC03722E8   vtable +0xCC   turn it ON     (posts 0x21)
0xC0372330   vtable +0xD0   turn it OFF    (posts 0x22)
```

Both methods take the singleton in `r0` and nothing else.

ARM, self-contained. `r0` in = 1 for on, 0 for off:

```arm
false_colour:                       @ r0: 1 = on, 0 = off
    push    {r4, lr}
    movw    r4, #0x22E8             @ 0xC03722E8, start
    movt    r4, #0xC037
    cmp     r0, #0
    movweq  r4, #0x2330             @ 0xC0372330, stop
    movteq  r4, #0xC037
    movw    r0, #0x0CF8             @ 0xC0370CF8, the CameraIF singleton
    movt    r0, #0xC037
    blx     r0                      @ r0 = the singleton
    blx     r4                      @ start or stop, taking it
    pop     {r4, pc}
```

That is it. There is no argument, no handle to keep, nothing to free.

**Keep your own flag.** There is no camera state to read back, so your UI must
remember what it last asked for. In our menu that is one byte, flipped on each
press:

```arm
toggle_false:
    push {r4, r5, r6, lr}
    LDA  r6, ST                     @ our state block
    ldr  r0, [r6, #FC_ON]
    eor  r0, r0, #1                 @ flip our own byte
    str  r0, [r6, #FC_ON]
    cmp  r0, #0
    LDA  r4, 0xC03722E8             @ start
    LDA  r0, 0xC0372330             @ stop
    moveq r4, r0
    LDA  r0, 0xC0370CF8
    blx  r0                         @ the singleton
    blx  r4
    pop  {r4, r5, r6, pc}
```

(`LDA` is just `movw`/`movt` of a 32-bit constant.)

## 3. Where to call it from

**Call it from your key handler.** That is exactly where the camera's own
function key calls it, on the same thread, so there is nothing special about
doing it from a menu row.

It touches no picker cell, no timing entry and no geometry, so unlike a
recording-mode change it needs:

- no preset re-latch
- no busy/recording interlock to change modes safely
- no resource to release when you switch it off

Turning it off is the same call with the stop method. Cold boot clears it
anyway, because nothing was stored.

## 4. You get EL Zone for free

**Confirmed on hardware:** set the camera's own **False Color Style** to
**EL ZONE**, and the same call latches EL Zone instead. You do not implement EL
Zone, and you do not need a second code path.

Event 0x21 says *start the mode*. It never names a style. The scale that gets
drawn is chosen separately, at draw time, inside the UI:

- NBU scene **`B5_9`** holds a container the firmware calls **`StyleScale`**
  (object 33210, 1024x64 at y=301).
- It carries a `toggleVisible` whose `select-value` is driven by an
  `appVariableEvent` bound to the app variable **`CM_FalseColorAj`**.
- Its two children are the two scales:

| Child | Object | Ticks it draws |
|---|---|---|
| `FalseColorScale` | 33211 | `0`, `2.5`, `18`, `Gray`, `+1`, `Stop`, `99`, `100` — IRE |
| `ElZoneScale` | 33212 | `-6 … -1`, `-½`, `0`, `+½`, `+1 … +6` — **stops** |

One mode, two scales, selected by the user's style setting. That is the entire
reason the latch works for both.

Related symbols, if you want to drive the style yourself rather than asking the
user to set it in the camera menu:

```
0xC005DBA0  SetFalseColorType          picks the style; CANNOT switch the effect on
0xC073F114  MenuItemFalseColorType     the menu item
0xC0CD0088  MenuFalseColorHandler
0xC0D1BE3C  FalseColorBarDrawer
0xC0BCABB4  ShellUserSettingAccessor<eFalseColorStyle, 1U>
```

Localization keys: `2158` = `EL ZONE`, `2152`–`2157` = `False Color`.

The style byte itself is **probably `0xC3032DCE`** — the
`ShellUserSettingAccessor` descriptors carry a RAM address after their name
pointer, and across 52 of them those addresses run one byte per setting
(`eXC_ExpMode` `0xC3032D9B`, `eXC_IsoStep` `0xC3032D9D`, and so on). **This has
not been read on a camera.** One shell read settles it: read the byte, change
Style in the camera menu, read it again.

## 5. The one limit, measured

**During recording the request is accepted and does nothing.** Measured with a
resident watcher on the camera (the USB shell cannot be used mid-take):

- posting event 0x21 once a second from our own thread returns success **every
  time** and puts nothing on screen
- the rec state machine's state index does not change when recording starts, and
  the `+0x44 == 2` bail-out never fires, so nothing on the request path is
  refusing it
- stop recording and false colour comes back by itself

So it is a live-view/standby tool. If your UI shows a state, it will read "on"
during a take while nothing is displayed; that is the camera, not your code.

## 6. How this was established

- Event numbering is cross-checked, not assumed: the neighbouring custom-key
  entry (AP preview) emits `0x1F`/`0x20` from the adjacent vtable slots.
- `gui send INTR_START_FALSE_COLOR` returns OK and does nothing. Those strings
  are log-parser text with no code pointing at them. Do not chase them.
- **Confirmed on hardware:** posting `0x21` from our own code turned false
  colour on, with no key held. That is what makes it a latch.
- The scene bindings above are decoded from the firmware's own component
  property tables, not guessed. In this repo:
  `python tools/nbu_components.py dump B5_9 33210`.

Firmware **Ver.5.02** only. Every address here is from that image; on any other
version, find them again before calling them.
