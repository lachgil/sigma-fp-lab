# Stage 3 — On-camera OSD menu + physical-button input (design)

SIGMA fp Ver.5.02, MAIN base `0xC0000000`. Offline reverse-engineering + code
draft; **no hardware test this session**. Every address below is either
CONFIRMED (string + xref + capstone disasm on the hash-verified MAIN) or clearly
marked INFERRED / TBD-from-hardware. Companion draft: `src/menu_keyhook.S`.

This supersedes the "MENU: HONEST STATUS = blocked" note in `MENU-AND-MODES.txt`
for the *input* half: a concrete, register-grounded interception point now
exists. The *render-persistence* half is still a lead (DrawingObserver), coded as
a state-flag + per-frame paint so it can ride Main's display hook.

--------------------------------------------------------------------------------
## 1. Key-event ABI and the interception point

### 1.1 What the firmware structure actually is (CONFIRMED)
`fwmap str HybridKeyEventObserver` -> name string `0xC091EA4C`. It is pointed to
by a compact **observer descriptor record** at `0xC091EA30`:

| off | addr | word | meaning |
|-----|------|------|---------|
| +0x04 | `0xC091EA34` | `0xC0269B50` | dtor slot (deleting destructor) |
| +0x08 | `0xC091EA38` | `0xC0265800` | **key handler slot** (the real entry) |
| +0x0C | `0xC091EA3C` | `0xC0726FB8` | owner thread base |
| +0x10 | `0xC091EA40` | `0xC091EA4C` | -> name string |

The exact same shape holds for `KeyPressedMonitoringProcess` (record `0xC0B4DE08`:
dtor `0xC02DDCD8`, process `0xC02DD6A8`, owner `0xC0726FB8`). So the two "virtuals"
FIRMWARE-DECODE listed are really **{destructor, method}** pairs:

- `0xC0269B50` is the **destructor**, not the update — disasm is the classic
  `push{r4,r5,lr}; mov r4,r0; mov r5,r1; tst r5,#1; if set bl free(this,4); pop`.
  **Do not hook here.**
- `0xC0265800` is the **actual per-key handler.** This is the correct choke point.

### 1.2 Handler `0xC0265800` ABI (CONFIRMED by disasm)
```
0xC0265800 e92d4030  push {r4,r5,lr}
0xC0265804 e1a05000  mov  r5,r0        ; r0 = this (observer object)
0xC0265808 e1a04001  mov  r4,r1        ; r1 = key/event id (int)
0xC026580C ...        cmp r4,#6 ; cmpne r4,#0x6D   ; special-cases ids 6 and 109
           ...        (sets RAM flags via 0xC0013090 test / byte @0xC3206F9A=1)
           ...        mov r0,#1 ; pop {r4,r5,pc}   ; returns 1 in EVERY path
```
- **r0 = observer `this`, r1 = key id (small int).** Return **r0 = 1**.
- Ids are small integers; **6** and **109 (0x6D)** are two of them (CONFIRMED).
- The full physical-button → id map is **not in the image** (INFERRED it exists
  only as GPIO masks in the monitor descriptor table). Enumerate it on hardware
  with the LOG ring in `menu_keyhook.S` — the same technique that pinned selector
  `r5==175` for the geometry hook. **[INFERRED, needs 1 capture]** that `0xC0265800`
  is invoked for *every* key id (it returns 1 unconditionally and only branches on
  6/109, which reads as an interest filter on an all-keys callback).

### 1.3 Low-level poller (context only — do NOT hook for a menu)
`0xC02DD6A8` (KeyPressedMonitoring process) is the per-key **debouncer**, ABI
`r0 = per-key descriptor`; it reads a GPIO bitmask via `blx 0xC0122710`, ANDs the
descriptor mask `[desc+8]→[0]`, runs a debounce timer `[desc+0xC]` (−0xE/tick),
state `[desc+0x10]`, hold-count `[desc+0x1E]`, and returns `r0 = 0 none / 1 press
edge / 2 repeat`. It is per-hardware-line and pre-decode, so it is the right place
only if you need a **simultaneous** chord (read the raw mask); for a serial
menu it is too low-level.

### 1.4 Interception hook — address, original word, install
Two installs; **A is primary** (no instruction displacement, trivially reversible):

- **A. Descriptor-pointer swap.** Write `MENU_ENTRY` into `0xC091EA38`
  (stock word `0xC0265800`). The framework then issues a normal AAPCS
  `BL MENU_ENTRY(this, keyid)`. Revert = write `0xC0265800` back. If a trace shows
  the dispatcher caches the pointer (menu never sees keys), use B.
- **B. Inline entry hook.** Overwrite the first word of the handler:
  - site `0xC0265800`, **original word `0xE92D4030`** (`push {r4,r5,lr}`)
  - new word `B MENU_ENTRY` = `0xEA000000 | (((MENU_ENTRY-(0xC0265800+8))>>2)&0xFFFFFF)`
  - pass-through must replay the 3 displaced words and re-enter at
    `0xC026580C` (label `menu_pass_inline` in the .S).

**ARM hook draft:** full source in `src/menu_keyhook.S` (`menu_entry`). Shape:
```
menu_entry:                     ; r0=this, r1=keyid  (AAPCS)
    push {r4,r5,r6,lr}
    mov  r4,r0 ; mov r5,r1
    LDA  r6, 0xC072EF00          ; MENU_STATE
    <log r1 into ring>           ; hw id enumeration
    ldr  r0,[r6,#ST_OPEN]
    cmp  r0,#0
    bne  menu_is_open            ; menu owns the D-pad -> mutate + consume
    <match open-sequence>        ; else fall to pass_through
pass_through:                    ; option A: tail-call untouched original
    mov r0,r4 ; mov r1,r5
    pop {r4,r5,r6,lr}
    LDA ip,0xC0265800 ; bx ip
consume:
    mov r0,#1                    ; swallow key from the native UI
    pop {r4,r5,r6,pc}
```
**Cave/state map** (reserve in loader; must not overlap `0xC072DE64` loader stub,
`0xC072F800` geometry cave, `0xC072FA00` telemetry/ARMED):
`0xC072E800` = MENU_ENTRY code, `0xC072EF00` = MENU_STATE (layout in the .S).

### 1.5 The "chord": serial sequence, not simultaneous
Because `0xC0265800` delivers **one key id per call** (edge events, serial), an
open trigger must be a **short sequence within a timeout** (e.g. a rarely-used key
tapped 3× inside `SEQ_WINDOW` ticks), *not* a held combo. Avoid ids 6/109 (the
original special-cases them). While the menu is open, our hook **consumes** the
D-pad so the native UI never sees it. If a true simultaneous chord is wanted,
hook the monitor mask at the `0xC02DD6A8` level instead and read the raw GPIO
bitmask from `blx 0xC0122710` — heavier and per-line, documented here as the
alternative, not the recommendation.
Nav ids `K_UP/K_DOWN/K_SET/K_BACK` are **TBD-from-hardware** placeholders in the
.S; fill from the LOG ring after one press-each-button capture.

--------------------------------------------------------------------------------
## 2. OSD menu renderer

### 2.1 Proven primitives (from ui-sup; addresses from the ticket)
- OSD text builder function entry **`0xC03E4620`** (contains the layout writes the
  ticket calls out: x/y/width/size — `0xC03E4698` y, `0xC03E469C` width,
  `0xC03E46A0` x, constants `0x180`=384 px and `0x20`=32; disasm CONFIRMED these
  are stack-built draw-command fields inside `0xC03E4620`, not standalone setters).
- Color/opacity constant cell **`0xC0BB1208` = `0xFFFFFFFF`** (CONFIRMED white/opaque).
- The boot **progress-bar path already proves** "build text → present 3×"; reuse
  that exact call path as the primitive rather than reconstructing `0xC03E4620`'s
  vtable-dispatched ABI (it needs a live OSD-context `this`).

### 2.2 Multi-line menu despite the 24-char / no-space limit
- **One `display text` call per menu line**, each at a distinct y (the y field the
  builder writes near `0xC03E4698`); stack N lines by stepping y per row.
- **≤24 chars, no spaces:** pad/separate with a non-space glyph (`_` or the font's
  separator), and mark the cursor with a leading glyph, e.g.
  `>OPENGATE___ON` / `_HIGHFPS____OFF` / `_DARKCOMP___OFF`. Right-justify the
  value by fixed-width padding so columns line up without spaces.
- Keep each label + state under 24 chars; if a label is longer, abbreviate at
  authoring time (static strings in the payload).

### 2.3 Triple buffering + persistence
The display is triple-buffered (present 3×), so a one-shot draw shows for one
frame then the stock content overwrites it on the next two buffers. Two ways to
persist, in order of safety:

1. **Per-frame repaint (recommended, rides Main's display hook).** The key hook
   only sets `ST_OPEN`, `ST_CURSOR`, `ST_DIRTY` in MENU_STATE. A hook already
   living on the display path (Main's green/display hook in `MovSigProcess`
   `0xC0428AE0`, or the `YuvResize` task `~0xC03CB090`) reads MENU_STATE **every
   frame** and re-issues the N `display text` calls whenever `ST_OPEN`. Because it
   runs once per composited frame it naturally covers all three buffers. **Action:
   coordinate with Main to add a `if(ST_OPEN) menu_paint()` tail to its display
   hook** — this is the single cleanest integration and needs no new observer.
2. **DrawingObserver (lead, not yet an API).** `0xC0528300` is a draw-slot manager
   (`push{r4-r7,sl,fp,lr}`; `r0=this,r1=slot,r2=?`; indexes state
   `[this+0x48+slot*4]`, threshold 3). `XC_OnceDrawingObserver` string `0xC0D009D4`
   confirms a one-shot drawing-observer class exists. Registering our paint as a
   persistent drawing observer would let the compositor call us each frame without
   piggy-backing Main's hook. **Status: researched, ABI of `0xC0528300`/the
   register call not fully reversed — treat as the follow-up, use (1) first.**

--------------------------------------------------------------------------------
## 3. Controller state machine

State lives in **MENU_STATE @ `0xC072EF00`** (layout in the .S). Three UI states,
driven entirely by the key hook; feature writes are deferred to an idle-gated
apply.

```
            open-sequence complete (K_SEQKEY ×SEQ_NEED within window)
   CLOSED ───────────────────────────────────────────────► OPEN(cursor=0)
     ▲   \  any other key: ST_SEQ_POS reset, key PASSED THROUGH        │
     │    \                                                            │ K_UP/K_DOWN: move cursor, DIRTY=1, CONSUME
     │     \                                                           │ K_SET:  flip ST_FLAGS bit(cursor); OR into ST_REQ; DIRTY=1; CONSUME
     │      \                                                          │ (any other key while open): CONSUME (swallow)
     └────────────────────── K_BACK: OPEN=0, cursor=0, DIRTY=1 ◄───────┘

   Every frame (display hook / idle):
     if ST_DIRTY: menu_paint(); ST_DIRTY=0
     if ST_REQ && record-idle: menu_apply(ST_REQ)   ; heavy cell writes here only
```
Key rules:
- **Consume vs pass-through:** closed → only the open-sequence key is watched, all
  else passes to the native handler untouched; open → every key is consumed so the
  camera UI never double-acts.
- **No heavy work in the key thread.** `K_SET` only toggles a bit and sets a
  request; the actual multi-cell writes run in `menu_apply`, called from an
  **idle/record-safe** context (RESEARCH.txt "idle-only changes"). The record-idle
  gate address is **[INFERRED / TBD]** — wire a real record-state flag before
  enabling mid-session writes; until then, apply only from standby.
- **Re-latch:** open gate / mode changes need a mode-switch away/back to re-latch
  (same as `toggle_opengate.py`); the OSD should prompt the user after `menu_apply`.

--------------------------------------------------------------------------------
## 4. Feature-toggle write map (what each menu line pokes)

All open-gate cells/values are **CONFIRMED** (they are exactly the
`toggle_opengate.py` set, proven on hardware). `menu_apply` in the .S writes them.

### Line 0 — Open Gate (bit `F_OPENGATE`)
| addr | ON | OFF (stock) | note |
|------|----|-----|------|
| `0xC0BE5888` (b) | `0x75` | `0x6A` | picker slot7 mode 106↔117 |
| `0xC0BE5A28` (b) | `0x75` | `0x6A` | picker table 2 mirror |
| `0xC0BE5BC8` (b) | `0x75` | `0x6A` | picker table 3 mirror |
| `0xC0B59A28` (w) | `0x00041C70` | `0x00040888` | mode117 VMAX 99.9↔29.97 |
| `0xC0BD9A34` (w) | `0x400` | `0x640` | RWZM live H |
| `0xC0BE1684` (w) | `0x400` | `0x640` | RWZM rec H |
| `0xC0BD9EFC` (w) | `0x400` | `0x640` | RWZM live V |
| `0xC0BE1B4C` (w) | `0x400` | `0x640` | RWZM rec V |
| `0xC072FA10` (w) | `0x1` | `0x0` | ARMED flag |
| `0xC043A19C` (w) | `0xEB0BD597` | `0xE1A00004` | geometry hook: `BL 0xC072F800` ↔ `mov r0,r4` |
Ordering: ON = data first then hook word LAST; OFF = hook word FIRST then data
(matches the on-hardware-safe order in `toggle_opengate.py`).

### Line 1 — High-FPS (bit `F_HIGHFPS`) — same shape, per chosen slot S
- picker mode id: `0xC0BE5810 + S*0x10 + 8` (and mirrors `0xC0BE59B0`, `0xC0BE5B50`)
  ← target mode id (e.g. M134 2016x1136@119.88).
- VMAX: `0xC0B59500 + rec*0x20 + 0x08` ← the fast-mode timing value.
- **No geometry hook** if the repurposed slot's native raster already matches the
  target sensor raster (FIRMWARE-DECODE §5). Concrete slot/mode/VMAX values are
  the per-profile data to fill after the hardware picker-vs-menu check; mechanism
  identical to open gate. (CONFIRMED mechanism; specific slot values chosen later.)

### Line 2 — Darkness comp (bit `F_DARKCOMP`)
- per-profile float scalar at `0xC0BD852C` / `0xC0BD89F4` (profile 122 index,
  +0xfc/+0x100 in the record builder `0xC0439D18`): stock `2.0f` — bump to
  compensate the summed-downscale gain lost by RWZM unity (FIRMWARE-DECODE §3).
  Exact factor/direction needs a frame-level measurement (INFERRED magnitude
  ~0.64×…0.41×), so the menu should expose a few discrete steps to A/B on scene.

--------------------------------------------------------------------------------
## 5. Confidence summary
- **CONFIRMED:** handler `0xC0265800` and its `r0=this,r1=keyid`→`r0=1` ABI;
  descriptor record `0xC091EA30`/`0xC091EA38`; `0xC0269B50`=destructor;
  `0xC02DD6A8` debouncer ABI; original words at the hook site; all open-gate
  write cells/values; OSD builder `0xC03E4620` + layout fields; color cell
  `0xC0BB1208`; `0xC0528300` draw-slot manager exists.
- **INFERRED (needs 1 on-hardware capture, no brick risk — RAM only):** that
  `0xC0265800` fires for every key id; the physical-button→id map (incl. nav
  ids and a safe open key); the record-idle gate address for `menu_apply`;
  darkness-comp exact factor.
- **LEAD (follow-up):** DrawingObserver `0xC0528300` register ABI for
  self-persisting overlay; until then paint per-frame from the display hook
  (coordinate with Main).
- **No hardware was tested; no on-camera behavior is claimed.**
