.syntax unified
.arm
.text

/* =============================================================================
 * menu_keyhook.S -- Stage 3 on-camera OSD menu: input interception + controller
 * SIGMA fp Ver.5.02, base 0xC0000000. DRAFT (offline; no hardware test).
 *
 * WHAT IS CONFIRMED (disasm-grounded, this session)
 * -------------------------------------------------
 * - HybridKeyEventObserver descriptor record @ 0xC091EA30:
 *       +0x04 0xC091EA34 = 0xC0269B50   dtor slot   (deleting destructor)
 *       +0x08 0xC091EA38 = 0xC0265800   HANDLER slot (the real key entry)
 *       +0x0C 0xC091EA3C = 0xC0726FB8   owner thread base
 * - Key HANDLER 0xC0265800 ABI (CONFIRMED by disasm):
 *       r0 = this (observer object), r1 = key/event id (int)
 *       body: cmp r1,#6 ; cmpne r1,#0x6D ; special-cases those two, else falls
 *             through; returns r0 = 1 in every path.
 *       first 3 words:  0xC0265800 e92d4030  push {r4,r5,lr}
 *                       0xC0265804 e1a05000  mov  r5,r0
 *                       0xC0265808 e1a04001  mov  r4,r1
 * - The id space is small integers (6, 109 seen). Full physical-button -> id map
 *   is NOT in the image; enumerate it on hardware with the LOG ring below (same
 *   method that confirmed selector r5==175 for the geometry hook).  [INFERRED:
 *   that 0xC0265800 is invoked for EVERY key id -- verify with one capture.]
 *
 * INSTALL (two options; option A is primary, cleanest, fully reversible)
 * ---------------------------------------------------------------------
 *  A. Descriptor-pointer swap  (NO instruction displacement):
 *        write  MENU_ENTRY  to  0xC091EA38   (stock word 0xC0265800)
 *     The framework then BL's MENU_ENTRY(this,keyid) with a normal AAPCS call.
 *     Revert = write 0xC0265800 back. If a trace shows the dispatcher caches the
 *     pointer (menu never sees keys), fall back to B.
 *  B. Inline entry hook:
 *        write  B MENU_ENTRY  to  0xC0265800  (stock word 0xE92D4030)
 *     Encoding: word = 0xEA000000 | (((MENU_ENTRY-(0xC0265800+8))>>2)&0xFFFFFF)
 *     MENU_ENTRY must then, on pass-through, replay the 3 displaced words and
 *     branch to 0xC026580C (see menu_entry_inline at bottom).
 *
 * CAVE / STATE MAP  (reserve in the loader; must not overlap:
 *   0xC072DE64 loader stub, 0xC072F800 geometry cave, 0xC072FA00 telemetry/ARMED)
 *   0xC072E800  MENU_ENTRY code (this file)
 *   0xC072EF00  MENU_STATE (see layout below)
 * ============================================================================= */

/* ---- addresses (CONFIRMED unless marked) ---- */
.equ KEY_HANDLER,     0xC0265800     /* original HybridKeyEventObserver handler */
.equ KEY_HANDLER_C,   0xC026580C     /* re-entry after the 3 displaced words     */

/* ---- MENU_STATE layout @ 0xC072EF00 ---- */
.equ ST,              0xC072EF00
.equ ST_OPEN,         0x00           /* u32 0=closed 1=open                       */
.equ ST_CURSOR,       0x04           /* u32 selected line index                   */
.equ ST_DIRTY,        0x08           /* u32 nonzero -> render hook must redraw     */
.equ ST_REQ,          0x0C           /* u32 pending feature-apply request bitmap   */
.equ ST_SEQ_POS,      0x10           /* u32 open-sequence match progress          */
.equ ST_SEQ_T,        0x14           /* u32 tick of last seq key (timeout window) */
.equ ST_FLAGS,        0x18           /* u32 feature on/off bitmap (mirrors cells) */
.equ ST_LOGN,         0x1C           /* u32 ring write count                       */
.equ ST_LOG,          0x20           /* u32[32] key-id ring for hw enumeration     */

/* ---- key ids: TBD from hardware LOG. Placeholders below. Do NOT use 6/109
 *      (the original special-cases them). Serial event stream => the "chord" is
 *      a SEQUENCE within a timeout, not a simultaneous press (see MENU-UI.md
 *      for the simultaneous-mask alternative at 0xC02DD6A8). ---- */
.equ K_UP,            0xF001         /* TBD */
.equ K_DOWN,          0xF002         /* TBD */
.equ K_SET,           0xF003         /* TBD (OK/center) */
.equ K_BACK,          0xF004         /* TBD (menu/QS)   */
.equ K_SEQKEY,        0xF004         /* open-sequence key: press SEQ_NEED times   */
.equ SEQ_NEED,        3
.equ SEQ_WINDOW,      60             /* ticks; TBD units of the caller's clock    */

/* feature request bits (ST_REQ / ST_FLAGS) */
.equ F_OPENGATE,      0x1
.equ F_HIGHFPS,       0x2
.equ F_DARKCOMP,      0x4

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

/* =============================================================================
 * MENU_ENTRY  (install target for option A)
 *   in:  r0 = observer this,  r1 = key id     (AAPCS; may clobber r0-r3,ip)
 *   out: r0 = 1 to CONSUME (swallow the key), else tail-call original.
 * ============================================================================= */
.global menu_entry
menu_entry:
    push    {r4, r5, r6, lr}
    mov     r4, r0                    /* this  */
    mov     r5, r1                    /* keyid */
    LDA     r6, ST

    /* --- log the id (enables the on-hardware id enumeration pass) --- */
    ldr     r0, [r6, #ST_LOGN]
    and     r1, r0, #31
    add     r0, r0, #1
    str     r0, [r6, #ST_LOGN]
    add     r2, r6, #ST_LOG
    str     r5, [r2, r1, lsl #2]

    /* --- dispatch on open/closed --- */
    ldr     r0, [r6, #ST_OPEN]
    cmp     r0, #0
    bne     menu_is_open

/* ---- CLOSED: watch for the open sequence, otherwise pass everything through */
    LDA     r0, K_SEQKEY
    cmp     r5, r0
    bne     seq_reset                 /* any non-seq key resets progress          */
    /* seq key pressed: (timeout check is INFERRED-clock; kept simple here) */
    ldr     r0, [r6, #ST_SEQ_POS]
    add     r0, r0, #1
    str     r0, [r6, #ST_SEQ_POS]
    cmp     r0, #SEQ_NEED
    blt     pass_through
    /* opened! */
    mov     r0, #0
    str     r0, [r6, #ST_SEQ_POS]
    mov     r0, #1
    str     r0, [r6, #ST_OPEN]
    str     r0, [r6, #ST_DIRTY]       /* ask render hook to paint the menu        */
    mov     r0, #0
    str     r0, [r6, #ST_CURSOR]
    b       consume                   /* swallow the key that opened the menu     */
seq_reset:
    mov     r0, #0
    str     r0, [r6, #ST_SEQ_POS]
    b       pass_through

/* ---- OPEN: our menu owns the D-pad; map keys, mutate state, consume ---- */
menu_is_open:
    LDA     r0, K_UP
    cmp     r5, r0
    beq     nav_up
    LDA     r0, K_DOWN
    cmp     r5, r0
    beq     nav_down
    LDA     r0, K_SET
    cmp     r5, r0
    beq     nav_set
    LDA     r0, K_BACK
    cmp     r5, r0
    beq     nav_back
    b       consume                   /* while open, swallow all other keys too   */

nav_up:
    ldr     r0, [r6, #ST_CURSOR]
    cmp     r0, #0
    subne   r0, r0, #1
    str     r0, [r6, #ST_CURSOR]
    b       redraw_consume
nav_down:
    ldr     r0, [r6, #ST_CURSOR]
    cmp     r0, #(MENU_N-1)
    addlt   r0, r0, #1
    str     r0, [r6, #ST_CURSOR]
    b       redraw_consume
nav_back:
    mov     r0, #0
    str     r0, [r6, #ST_OPEN]
    str     r0, [r6, #ST_CURSOR]
    b       redraw_consume

nav_set:
    /* toggle the feature bit for the current line, then request an apply.
     * The heavy cell-writes are done by menu_apply (idle-gated), NOT here. */
    ldr     r0, [r6, #ST_CURSOR]
    mov     r1, #1
    lsl     r1, r1, r0                /* bit = 1<<cursor  (line i -> feature bit i)*/
    ldr     r2, [r6, #ST_FLAGS]
    eor     r2, r2, r1
    str     r2, [r6, #ST_FLAGS]
    ldr     r2, [r6, #ST_REQ]
    orr     r2, r2, r1
    str     r2, [r6, #ST_REQ]         /* render/idle hook will call menu_apply     */
    b       redraw_consume

redraw_consume:
    mov     r0, #1
    str     r0, [r6, #ST_DIRTY]
consume:
    mov     r0, #1                    /* return 1 = handled/consumed               */
    pop     {r4, r5, r6, pc}

pass_through:
    /* option A: tail-call the untouched original with the ORIGINAL args */
    mov     r0, r4
    mov     r1, r5
    pop     {r4, r5, r6, lr}
    LDA     ip, KEY_HANDLER
    bx      ip

/* =============================================================================
 * menu_entry_inline  -- pass-through tail for INSTALL OPTION B only.
 * Use this label as MENU_ENTRY if you inline-branch at 0xC0265800. Replace the
 * "bx ip" pass-through above with a jump here, which replays the 3 displaced
 * words then re-enters the original body at 0xC026580C.
 * ============================================================================= */
.global menu_pass_inline
menu_pass_inline:
    push    {r4, r5, lr}              /* replays 0xE92D4030 */
    mov     r5, r0                    /* replays 0xE1A05000 */
    mov     r4, r1                    /* replays 0xE1A04001 */
    LDA     ip, KEY_HANDLER_C
    bx      ip

/* =============================================================================
 * menu_apply(reqbits)  -- perform the guarded feature cell-writes.
 * Call from an IDLE-only context (render hook when not recording). Reads ST_REQ,
 * writes the known patch cells, clears ST_REQ. Record-idle gate address is
 * [INFERRED / TBD]; wire it before enabling writes mid-session.
 *   All cell addresses/values below are CONFIRMED (toggle_opengate.py map).
 * ============================================================================= */
.equ REC_IDLE_FLAG,   0xC0000000     /* TBD: replace with real record-state addr  */

.macro SETW addr, val
    LDA     r0, \addr
    LDA     r1, \val
    str     r1, [r0]
.endm
.macro SETB addr, val
    LDA     r0, \addr
    mov     r1, #\val
    strb    r1, [r0]
.endm

.global menu_apply
menu_apply:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r4, [r6, #ST_REQ]
    ldr     r5, [r6, #ST_FLAGS]

    tst     r4, #F_OPENGATE
    beq     1f
    /* open gate = the proven RAM patch-set. ON writes opengate values; OFF stock.
     * (values from toggle_opengate.py, CONFIRMED on hardware) */
    tst     r5, #F_OPENGATE
    beq     og_off
    /* --- ON --- (data first, hook word LAST to arm) */
    SETB    0xC0BE5888, 0x75          /* picker slot7 mode 106->117 */
    SETB    0xC0BE5A28, 0x75
    SETB    0xC0BE5BC8, 0x75
    SETW    0xC0B59A28, 0x00041C70    /* mode117 VMAX 99.9->29.97 */
    SETW    0xC0BD9A34, 0x00000400    /* RWZM live H  0x640->0x400 */
    SETW    0xC0BE1684, 0x00000400    /* RWZM rec  H */
    SETW    0xC0BD9EFC, 0x00000400    /* RWZM live V */
    SETW    0xC0BE1B4C, 0x00000400    /* RWZM rec  V */
    SETW    0xC072FA10, 0x00000001    /* ARMED flag on */
    SETW    0xC043A19C, 0xEB0BD597    /* geometry hook: BL cave 0xC072F800 (ARM) */
    b       1f
og_off:
    SETW    0xC043A19C, 0xE1A00004    /* geometry hook -> stock mov r0,r4 (disarm first) */
    SETB    0xC0BE5888, 0x6A
    SETB    0xC0BE5A28, 0x6A
    SETB    0xC0BE5BC8, 0x6A
    SETW    0xC0B59A28, 0x00040888
    SETW    0xC0BD9A34, 0x00000640
    SETW    0xC0BE1684, 0x00000640
    SETW    0xC0BD9EFC, 0x00000640
    SETW    0xC0BE1B4C, 0x00000640
    SETW    0xC072FA10, 0x00000000
1:
    /* F_HIGHFPS / F_DARKCOMP: same shape, different cells -- see MENU-UI.md
     * write map (picker+VMAX for a chosen slot; +0xfc/+0x100 float for darkness).
     * Left as data to fill per validated profile; the mechanism is identical. */

    mov     r0, #0
    str     r0, [r6, #ST_REQ]         /* consume the request */
    pop     {r4, r5, r6, pc}

.equ MENU_N, 3                        /* lines: Open Gate / High-FPS / Dark comp   */
