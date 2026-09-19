# False colour and EL Zone are one mode with two scales



The row posts rec-manager event **0x21** through CameraIF vtable +0xCC
(`0xC03722E8`), and `0x22` through +0xD0 (`0xC0372330`) to stop it. See
[docs/overlay/README.md](../overlay/README.md), "False colour, latched instead
of held". That request says *start the mode*. It says nothing about which scale
is drawn.

The scale is chosen by the UI, from the user's stored style:

| Piece | Where |
|---|---|
| Style setting | `ShellUserSettingAccessor<eFalseColorStyle, 1U>`, name at `0xC0BCABB4`, descriptor at `0xC0BCABA8` |
| Menu item | `MenuItemFalseColorType` (`0xC073F114`), `MenuItem<eXC_MenuFalseColorType>` (`0xC0738498`) |
| Handler | `MenuFalseColorHandler` (`0xC0CD0088`), `UicGuiMenuFalseColor` (`0xC0D1BDE8`) |
| Bar drawer | `FalseColorBarDrawer` (`0xC0D1BE3C`) |
| Palette setter | `SetFalseColorType` (`0xC005DBA0`) -- the style, not the switch |

Localization keys, `English.nloc`:

| Key | Text |
|---|---|
| `2158` | `EL ZONE` |
| `2152`, `2153`, `2154`, `2155`, `2157` | `False Color` |
| `2159` | `Assign False Color to a button on the Custom Button Functions menu.` |

Key `2159` is the camera telling you the mode has no stored on/off switch, which
is the same fact that made our row necessary.

## The scene proves the selection

Both scales are objects in NBU scene **`B5_9`**, under one container the
firmware itself calls **`StyleScale`** (object 33210, parent `Menu` 89, at
(0, 301), 1024x64):

```
33210 StyleScale         toggleVisible + appVariableEvent
 +-- 33211 FalseColorScale  child 0, objectBase is-visible = false
 +-- 33212 ElZoneScale      child 1
```

Decoded with `tools/nbu_components.py` against the firmware's own property
tables:

| Record | Component | Properties |
|---|---|---|
| `0xC1CDF7A7` | `toggleVisible` | `select-value = 0` |
| `0xC1CDF7CB` | `appVariableEvent` | `variable-name = 'CM_FalseColorAj'`, `property-name = 'select-value'`, `component-id = 1` |

So the app variable `CM_FalseColorAj` drives `toggleVisible.select-value`, which
picks child 0 or child 1. One mode, two scales, chosen at draw time.

The children are exactly what you would expect of each system, which is a
second, independent confirmation that this is the right pair of objects:

| Scale | Ticks |
|---|---|
| `FalseColorScale` | `Scale_0`, `Scale_2pt5`, `Scale_18`, `Scale_Gray`, `Scale_Plus1`, `Scale_Stop`, `Scale_99`, `Scale_100` -- IRE |
| `ElZoneScale` | `Scale_Minus6` .. `Scale_Minus1`, `Scale_MinusHalf`, `Scale_0`, `Scale_PlusHalf`, `Scale_Plus1` .. `Scale_Plus6` -- **stops** |

Reproduce, no camera:

```sh
.venv/bin/python tools/nbu_components.py dump B5_9 33210
.venv/bin/python tools/nbu_components.py schema toggleVisible
```

## Where the style byte probably lives

`ShellUserSettingAccessor` descriptors carry a RAM address after their name
pointer, and consecutive accessors take consecutive bytes:

```
eXC_ExpMode          0xC3032D9B
unsigned int         0xC3032D9C
eXC_IsoStep          0xC3032D9D
...
eFalseColorStyle     0xC3032DCE
```

**[INFERENCE]** `0xC3032DCE` is the False Color Style byte. The ascending
one-byte-per-setting pattern across 52 accessors is strong, and the region is
the same settings block as `0xC3033A44`, the screen-state word we measured on
hardware. It has **not** been read on a camera.

Settling it costs one shell read, no writes:

```sh
host/fpsh mem get 0xC3032DCE 1      # with the style on False Color
# change Menu -> False Color Style -> EL ZONE, then read again
```

If it moves, that is the byte, and a menu row can offer "FALSE COL / EL ZONE"
directly instead of asking you to set it in the camera's menu first.

## What this changes for us

- **Nothing in the row.** `FALSE COL` already gives you whichever style the
  camera is set to. The label is now wrong rather than the behaviour: it says
  FALSE COL when it may be latching EL Zone.
- **The limit is unchanged.** Measured 2026-09-14: during a take the request is
  accepted and produces nothing on screen, for either style. Stop recording and
  it comes back by itself.
- **A style row is cheap** once the byte above is confirmed, and it would be the
  first thing in the card that offers EL Zone as a latch, which the camera does
  not.
