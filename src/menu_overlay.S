.syntax unified
.arm
.text
/* fpLAB drawn menu: the whole option list on screen at once, RAM only.
 *
 * The card's menu draws one line through the firmware's text handler, which
 * renders into a rectangle the handler hardcodes. That is why it shows a single
 * row: there is nowhere else to put one. This payload does not use that handler
 * at all. It carries its own 5x7 font and writes bytes into the OSD layer, so
 * it can put a character anywhere, and draws every row with a highlight on the
 * one the card's menu currently has selected.
 *
 * It only READS the card menu's state block, never writes it. RIGHT and UP
 * still belong to menu.S; this is a display for the state that already exists.
 *
 * The layer rotates three buffers and only the camera's UI decides which is on
 * screen, so each pass paints all of them. No display lock is taken and no
 * firmware drawing code is called, so this cannot deadlock against the UI.
 *
 * STATE_ADDR is supplied by the deployer, which owns the pool layout.
 *   +0x00 status: 0 running, 1 done, else a refusal code
 *   +0x04 passes        +0x08 stop request   +0x0C resident passes
 *   +0x10 resident exited                    +0x14 attach   +0x18 create
 *   +0x1C base0  +0x20 base1  +0x24 base2    +0x28 stride   +0x2C surface height
 *   +0x30 panel x       +0x34 panel y
 *   +0x38 cursor last seen                   +0x3C current base (scratch)
 *   +0x48 queued property function           +0x4C its value  +0x50 writes done
 *   +0x54 follow arming  +0x58 idle passes before hiding
 *   +0x5C idle passes    +0x64 last signature   +0x68 what this pass does
 *   +0x6C cursor last seen  +0x70 the key gate's state block, or 0
 *   +0x74 whether the gate was open last pass
 *   +0x100.. last seen row states
 *   +0x40 current colour (scratch)           +0x44 menu init status seen
 *   +0x80 thread holder
 *   +0xC0 re-arm false colour   +0xC4 how many times we have asked
 *   +0x140 body object        +0x144 body vtable (+0xC of it is the body)
 *   +0x160 background palette index behind the panel, 0 = transparent
 */
.equ STATE,      STATE_ADDR
.equ MENU_ST,    0xC072FB00     /* menu.S's own state block; read only */
.equ MENU_INIT,  20             /*   +20 armed flag                    */
.equ MENU_FC_ON, 72             /*   +72 the menu's own false-colour latch */
.equ MENU_GREEN_ON, 76          /*   +76 and its green-fix latch           */
.equ UI_STATE,   0xC3033A44     /* 2 live view, 4 playback, 5 the camera's menu */
.equ UI_LIVEVIEW, 2             /*   +72 the menu's own false-colour latch */
.equ SETTINGS_OBJ, 0xC0057AE8   /* the settings object every setter takes */
.equ CAMERA_IF,  0xC0370CF8     /* -> the CameraIF singleton at 0xC351FE88 */
.equ REC_SM,     0xC358CB80     /* rec state machine; +0x144 is its state */
.equ FC_ROOT,    0xC358E1E8     /* what the false colour handler starts from */
.equ SET_MOV_FRAMERATE, 0xC005C0B8  /* analysis/menu_setters.json */
.equ CACHE_FN,   0xC000E91C
.equ TK_DLY,     0xC01F89C4
.equ XT_CREATE,  0xC036E108
.equ XT_ATTACH,  0xC036E1B8
.equ INK,        0xB2           /* what the firmware's own glyphs read back as */
.equ CELL_W,     12             /* (FONT_W + 1) * 2 */
.equ ROW_PITCH,  18
.equ ROWS,       14     /* ten original rows, two 120s, two 240/120 swaps */
.equ PANEL_W,    192
.equ PANEL_H,    (24 + (ROWS + 2) * ROW_PITCH + 6)   /* two rows are ours */
.equ VALUE_X,    132
.equ PERIOD_MS,  150
.equ PRIORITY,   20
.equ STACK,      0x2000

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

/* Reach a table without a relocation the camera's loader cannot apply. */
.macro PTR reg, label
    ldr \reg, 991f
990: add \reg, pc, \reg
    b 992f
991: .word \label - (990b + 8)
992:
.endm

.global menu_entry
menu_entry:
    push    {r4, lr}
    bl      menu_core
    mov     r0, #0
    pop     {r4, pc}

.global menu_body
menu_body:
    push    {r4, lr}
    LDA     r4, STATE
1:  ldr     r0, [r4, #0x08]
    cmp     r0, #0
    bne     2f
    bl      menu_core
    ldr     r0, [r4, #0x0C]
    add     r0, r0, #1
    str     r0, [r4, #0x0C]
    mov     r0, #PERIOD_MS
    LDA     ip, TK_DLY
    blx     ip
    b       1b
2:  mov     r0, #1
    str     r0, [r4, #0x10]
    mov     r0, #0
    pop     {r4, pc}

.global menu_spawn
menu_spawn:
    push    {r4, lr}
    LDA     r4, STATE
    mov     r0, #0
    str     r0, [r4, #0x08]
    str     r0, [r4, #0x10]
    str     r0, [r4, #0x0C]
    add     r0, r4, #0x80
    mov     r1, #PRIORITY
    LDA     r2, STACK
    PTR     r3, thread_name
    LDA     ip, XT_CREATE
    blx     ip
    str     r0, [r4, #0x18]
    add     r0, r4, #0x80
    add     r1, r4, #0x140          @ the ADDRESS of the body object word.
                                    @ NOT +0xC0: repeat_request reads that as
                                    @ "re-arm false colour", so a body object
                                    @ parked there reads as armed and the panel
                                    @ posts false colour at itself once a second
    LDA     ip, XT_ATTACH
    blx     ip
    str     r0, [r4, #0x14]
    mov     r0, #0
    pop     {r4, pc}

.global menu_core
menu_core:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r0, [r10, #4]
    add     r0, r0, #1
    str     r0, [r10, #4]
    mov     r0, #0
    str     r0, [r10]

    LDA     r4, MENU_ST             @ refuse to draw a menu that is not there
    ldr     r0, [r4, #MENU_INIT]
    str     r0, [r10, #0x44]
    cmp     r0, #1
    movne   r0, #0x30
    bne     refuse

    /* Not in live view: take the panel off the screen and stop. The camera's
     * own menu and playback own the display then, and painting over them is
     * both wrong and confusing. 2 = live view; measured on hardware. */
    LDA     r0, UI_STATE
    ldr     r0, [r0]
    cmp     r0, #UI_LIVEVIEW
    beq     1f
    ldr     r1, [r10, #0x58]        @ leave the countdown EXPIRED, not frozen:
    add     r1, r1, #1              @ otherwise coming back from the camera's
    str     r1, [r10, #0x5C]        @ menu re-shows the panel with time still
                                    @ on the clock, which looks like it opened
                                    @ itself. A real change brings it back.
    ldr     r0, [r10, #0x68]        @ exactly one clearing pass, as when hiding
    cmp     r0, #1
    moveq   r0, #2
    movne   r0, #0
    str     r0, [r10, #0x68]
    cmp     r0, #0
    beq     done
    b       2f
1:
    bl      follow_options          @ the cursor or an option moved: follow it
    bl      visibility              @ decide whether the panel shows at all
    ldr     r0, [r10, #0x68]
    cmp     r0, #0
    beq     done                    @ idle and already hidden: nothing to draw

2:  ldr     r7, [r10, #0x28]        @ stride
    ldr     r8, [r10, #0x2C]        @ surface height
    ldr     r5, [r10, #0x30]        @ panel x
    ldr     r6, [r10, #0x34]        @ panel y
    add     r0, r5, #PANEL_W
    cmp     r0, r7
    movhi   r0, #0x31
    bhi     refuse
    movw    r0, #PANEL_H
    add     r0, r6, r0
    cmp     r0, r8
    movhi   r0, #0x32
    bhi     refuse

    mov     r9, #0x1C               @ walk the three surface base words
surface_loop:
    ldr     r0, [r10, r9]
    cmp     r0, #0
    beq     surface_next
    LDA     r1, 0xC4000000
    cmp     r0, r1
    movlo   r0, #0x33
    blo     refuse
    LDA     r1, 0xC8000000
    cmp     r0, r1
    movhs   r0, #0x34
    bhs     refuse
    ldr     r1, [r10, #0x68]
    cmp     r1, #2
    bne     3f
    bl      clear_panel             @ the pass that hides it
    b       surface_next
3:  bl      paint_panel
surface_next:
    add     r9, r9, #4
    cmp     r9, #0x24
    bls     surface_loop

done:
    bl      apply_setting           @ a queued camera setting, if one is waiting
    bl      apply_camera_call       @ and a queued camera-interface request
    bl      watch_rec_state         @ note which state the recorder is in
    bl      repeat_request          @ and, if armed, keep asking for it
    LDA     ip, CACHE_FN            @ CPU writes are only data to the caches
    blx     ip
    mov     r0, #1
    str     r0, [r10]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}
refuse:
    str     r0, [r10]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* A camera setting the host asked for, applied from THIS thread rather than
 * from the shell's dispatcher: the recipe is
 *
 *     r0 = SETTINGS_OBJ()            the settings object
 *     r1 = value, r2 = 1
 *     blx property_fn                one row of analysis/menu_setters.json
 *
 * and it is the same call the camera's own menu makes, so the value lands in
 * the master block rather than in the mirror `setting set` writes. The request
 * is cleared before the call, so a wedge cannot become a loop of writes.
 *
 *   +0x48 property function, 0 when there is nothing to do
 *   +0x4C value        +0x50 completed count
 */
apply_setting:
    push    {r4, r5, r6, lr}
    LDA     r4, STATE
    ldr     r5, [r4, #0x48]
    cmp     r5, #0
    beq     9f
    LDA     r0, 0xC0000000          @ only ever call into the firmware image
    cmp     r5, r0
    blo     8f
    LDA     r0, 0xC2000000
    cmp     r5, r0
    bhs     8f
    ldr     r6, [r4, #0x4C]
    mov     r0, #0
    str     r0, [r4, #0x48]         @ clear first: one request, one write
    LDA     ip, SETTINGS_OBJ
    blx     ip
    mov     r1, r6
    mov     r2, #1
    blx     r5
    ldr     r0, [r4, #0x50]
    add     r0, r0, #1
    str     r0, [r4, #0x50]
    b       9f
8:  mov     r0, #0
    str     r0, [r4, #0x48]
9:  pop     {r4, r5, r6, pc}

/* An option that has just been switched ON selects the recording preset its
 * cell belongs to, so the camera lands on the right rate without the usual
 * "switch the preset away and back" dance. Two things make this safe:
 *
 *   - it reacts to a TRANSITION in menu.S's own state, and menu.S refuses to
 *     toggle anything while recording or while the writer is busy, so a change
 *     we can see is one the card already allowed;
 *   - it queues the write through apply_setting, which is the camera's own
 *     property call, and only acts when the request slot is free.
 *
 * Rows with a zero in the table are left alone. M130 is one of them on purpose:
 * it repoints whichever rate is selected AT THE MOMENT it is toggled, so moving
 * the rate afterwards would leave the two disagreeing. Choosing the rate first
 * belongs in menu.S, not in a poller that necessarily runs after the fact.
 *
 *   +0x54 armed: 0 leaves the camera alone entirely
 *   +0x60 .. +0x84 the state each row was last seen in
 */
follow_options:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r4, STATE
    ldr     r0, [r4, #0x54]
    cmp     r0, #0
    beq     9f
    LDA     r5, MENU_ST
    ldr     r0, [r5]                @ the cursor, for the rows that must be
    ldr     r1, [r4, #0x6C]         @ set up BEFORE they are switched on
    str     r0, [r4, #0x6C]
    cmp     r0, r1
    beq     0f
    cmp     r0, #ROWS
    bhs     0f
    PTR     r7, cursor_rates
    ldr     r7, [r7, r0, lsl #2]
    cmp     r7, #0
    beq     0f
    ldr     r1, [r4, #0x48]
    cmp     r1, #0
    bne     0f
    str     r7, [r4, #0x4C]
    LDA     r7, SET_MOV_FRAMERATE
    str     r7, [r4, #0x48]
0:  mov     r6, #0
1:  mov     r0, r6
    bl      row_value
    cmp     r0, #1                  @ only a real ON counts as a transition
    moveq   r0, #1
    movne   r0, #0
    add     r7, r4, #0x100          @ clear of the thread holder at +0x80
    ldr     r8, [r7, r6, lsl #2]
    str     r0, [r7, r6, lsl #2]
    cmp     r0, #1
    bne     2f
    cmp     r8, #0
    bne     2f                      @ already on last pass: nothing to do
    PTR     r7, option_rates
    ldr     r0, [r7, r6, lsl #2]
    cmp     r0, #0
    beq     2f
    ldr     r1, [r4, #0x48]
    cmp     r1, #0
    bne     2f                      @ a request is already waiting; leave it
    str     r0, [r4, #0x4C]
    LDA     r0, SET_MOV_FRAMERATE
    str     r0, [r4, #0x48]
2:  add     r6, r6, #1
    cmp     r6, #ROWS
    blo     1b
9:  pop     {r4, r5, r6, r7, r8, pc}

/* Show the panel while the menu is being used and get out of the way when it
 * is not. Any change to the cursor or to an option's state brings it back, so
 * pressing RIGHT or UP is all it takes -- no key hook, and nothing to hold down.
 *
 *   +0x58 idle passes before hiding, 0 to stay up permanently
 *   +0x5C passes since the last change   +0x64 what the menu looked like then
 *   +0x68 this pass: 0 nothing, 1 paint, 2 clear once
 */
visibility:
    push    {r4, r5, r6, r7, lr}
    LDA     r4, STATE
    LDA     r5, MENU_ST
    ldr     r6, [r5]                @ a signature of everything we display
    mov     r7, #0
1:  mov     r0, r7
    bl      row_value
    add     r6, r6, r0, lsl #4
    add     r6, r6, r7
    add     r7, r7, #1
    cmp     r7, #ROWS
    blo     1b
    mov     r7, #0                  @ r7: did anything move this pass. The
                                    @ comparison happens ONCE, below, after the
                                    @ latch is folded in: doing it here as well
                                    @ made every pass look like a change and the
                                    @ countdown never advanced.
    ldr     r5, [r4, #0x70]         @ the key gate, when there is one
    cmp     r5, #0
    beq     no_gate

    /* The gate opens the menu; closing it on idle is ours, because the gate
     * has no clock and we are the thing that runs regularly. The same pass
     * ages the double-press window. */
    ldr     r0, [r5, #8]
    add     r0, r0, #1
    str     r0, [r5, #8]
    cmp     r0, #2
    movhs   r1, #0
    strhs   r1, [r5, #4]            @ the window has passed
    ldr     r0, [r5, #0x24]         @ the latch belongs in the signature too
    add     r6, r6, r0, lsl #8
    ldr     r0, [r4, #0x64]
    str     r6, [r4, #0x64]
    cmp     r0, r6
    movne   r7, #1
    moveq   r7, #0
    ldr     r0, [r5]
    ldr     r1, [r4, #0x74]         @ the countdown belongs to THIS opening:
    str     r0, [r4, #0x74]         @ without this it inherits the idle time
    cmp     r0, r1                  @ from before the menu was opened and
    movne   r6, #0                  @ closes again on the very next pass
    strne   r6, [r4, #0x5C]
    cmp     r0, #0
    beq     gate_shut
    cmp     r7, #0
    beq     2f
    mov     r0, #0                  @ something moved: restart the countdown
    str     r0, [r4, #0x5C]
    b       3f
2:  ldr     r0, [r4, #0x5C]
    add     r0, r0, #1
    str     r0, [r4, #0x5C]
    ldr     r1, [r4, #0x58]
    cmp     r1, #0
    beq     3f                      @ no timeout configured: stay up
    cmp     r0, r1
    blo     3f
    mov     r0, #0
    str     r0, [r5]                @ idle for long enough: close it
    b       gate_shut
3:  mov     r0, #1
    str     r0, [r4, #0x68]
    b       9f
gate_shut:
    ldr     r0, [r4, #0x68]         @ exactly one clearing pass, then nothing
    cmp     r0, #1
    moveq   r0, #2
    movne   r0, #0
    str     r0, [r4, #0x68]
    b       9f

no_gate:
    ldr     r0, [r4, #0x64]         @ no gate: compare the signature here instead
    str     r6, [r4, #0x64]
    cmp     r0, r6
    movne   r7, #1
    cmp     r7, #0
    beq     4f
    mov     r0, #0
    str     r0, [r4, #0x5C]
    mov     r0, #1
    str     r0, [r4, #0x68]
    b       9f
4:  ldr     r0, [r4, #0x58]
    cmp     r0, #0
    moveq   r0, #1
    streq   r0, [r4, #0x68]
    beq     9f
    ldr     r1, [r4, #0x5C]
    add     r1, r1, #1
    str     r1, [r4, #0x5C]
    cmp     r1, r0
    movlo   r0, #1
    strlo   r0, [r4, #0x68]
    blo     9f
    moveq   r0, #2
    streq   r0, [r4, #0x68]
    movhi   r0, #0
    strhi   r0, [r4, #0x68]
9:  pop     {r4, r5, r6, r7, pc}

/* clear_panel(base): take our rectangle back off the screen, nothing else. */
clear_panel:
    push    {r4, lr}
    LDA     r4, STATE
    str     r0, [r4, #0x3C]
    mov     r0, #0
    str     r0, [r4, #0x40]
    ldr     r0, [r4, #0x30]
    ldr     r1, [r4, #0x34]
    movw    r2, #PANEL_W
    movw    r3, #PANEL_H
    bl      fill_rect
    pop     {r4, pc}

/* A request posted through the camera's own interface object, the way a
 * function key does it. False colour is the reason this exists: it is not a
 * stored setting at all, which is why nothing showed up in a diff of the
 * settings store while it was switched on. Holding an assigned button calls
 * CameraIF vtable +0xCC, which posts rec-manager event 0x21 (start), and
 * releasing calls +0xD0, which posts 0x22 (stop).
 *
 *   +0x78 the CameraIF method to call, 0 when there is nothing to do
 *   +0x7C how many have been made
 */
apply_camera_call:
    push    {r4, r5, lr}
    LDA     r4, STATE
    ldr     r5, [r4, #0x78]
    cmp     r5, #0
    beq     9f
    LDA     r0, 0xC0000000          @ only ever call into the firmware image
    cmp     r5, r0
    blo     8f
    LDA     r0, 0xC2000000
    cmp     r5, r0
    bhs     8f
    mov     r0, #0
    str     r0, [r4, #0x78]         @ clear first: one request, one call
    LDA     ip, REC_SM              @ what state we were in when we asked
    ldr     ip, [ip, #0x144]
    str     ip, [r4, #0x90]
    LDA     ip, CAMERA_IF
    blx     ip                      @ r0 = the camera interface singleton
    blx     r5
    str     r0, [r4, #0x8C]         @ and what the camera answered
    ldr     r0, [r4, #0x7C]
    add     r0, r0, #1
    str     r0, [r4, #0x7C]
    b       9f
8:  mov     r0, #0
    str     r0, [r4, #0x78]
9:  pop     {r4, r5, pc}

/* Ask for false colour over and over while armed.
 *
 * The custom-key dispatcher has two guard functions per entry, so the camera
 * can simply refuse to run the button's action while recording -- in which case
 * the request is never posted at all and nothing downstream ever sees it. This
 * posts it ourselves, on our own thread, which does not go through those guards.
 *
 *   +0xC0 armed   +0xC4 how many times we have asked
 */
repeat_request:
    push    {r4, lr}
    LDA     r4, STATE
    ldr     r0, [r4, #0xC0]
    cmp     r0, #0
    beq     9f
    ldr     r0, [r4, #0x78]         @ leave any pending request alone
    cmp     r0, #0
    bne     9f
    ldr     r0, [r4, #0xC4]
    add     r0, r0, #1
    str     r0, [r4, #0xC4]
    tst     r0, #7                  @ roughly once a second at ten passes
    bne     9f
    LDA     r0, 0xC03722E8
    str     r0, [r4, #0x78]
9:  pop     {r4, pc}

/* Which state the rec state machine is in, sampled every pass.
 *
 * Assist features are dispatched per state: only the live-view state implements
 * "start false colour" (vtable +0x6C -> 0xC0394240); every other state points at
 * a default that returns without doing anything. To make it work while
 * recording we need to know WHICH state that is, and the shell cannot be asked
 * during a take -- a command sent then wedges the USB endpoint. So the camera
 * writes it down for us instead.
 *
 *   +0x84 the state right now   +0x88 a bit per state seen
 *   +0x8C what the last camera request returned   +0x90 the state it was made in
 *   +0xA0 the false colour gate object while recording, +0xA4/+0xA8/+0xAC its
 *   three checked fields: +0x44, +0x328, +0x330
 */
watch_rec_state:
    LDA     r0, REC_SM
    ldr     r1, [r0, #0x144]
    LDA     r0, STATE
    str     r1, [r0, #0x84]
    cmp     r1, #7                  @ the array has eight states; anything else
    bhi     9f                      @ is a transient value, not a state
    mov     r2, #1
    lsl     r2, r2, r1
    ldr     r3, [r0, #0x88]
    orr     r3, r3, r2
    str     r3, [r0, #0x88]
    LDA     r2, FC_ROOT             @ sample the false colour gate every pass:
                                    @ the state index does not move when a take
                                    @ starts, so gating on it saw nothing
    ldr     r2, [r2, #8]
    cmp     r2, #0
    beq     9f
    ldr     r2, [r2, #4]
    cmp     r2, #0
    beq     9f
    str     r2, [r0, #0xA0]
    ldr     r3, [r2, #0x44]
    str     r3, [r0, #0xA4]
    ldrb    r3, [r2, #0x328]
    str     r3, [r0, #0xA8]
    ldrb    r3, [r2, #0x330]
    str     r3, [r0, #0xAC]
    ldr     r3, [r2, #0x44]         @ keep the highest +0x44 ever seen, so a
    ldr     r1, [r0, #0xB0]         @ value that only appears mid-take survives
    cmp     r3, r1
    strhi   r3, [r0, #0xB0]
    ldrb    r3, [r2, #0x328]
    ldr     r1, [r0, #0xB4]
    orr     r3, r3, r1
    str     r3, [r0, #0xB4]
    ldrb    r3, [r2, #0x330]
    ldr     r1, [r0, #0xB8]
    orr     r3, r3, r1
    str     r3, [r0, #0xB8]
9:  bx      lr

/* paint_panel(base): the frame, the title, and every row with its value. */
paint_panel:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    ldr     r4, [r10, #0x30]        @ panel x
    ldr     r5, [r10, #0x34]        @ panel y

    /* Background. 0 leaves the layer transparent, which is what this always
     * did; any other value is a palette index painted behind the rows. Which
     * index is an opaque black is not derivable from the image -- the OSD is
     * one byte per pixel into a palette we do not have -- so it is a state
     * word that can be swept live over the shell rather than a guess. */
    ldr     r0, [r10, #0x160]       @ background palette index, 0 = transparent
    str     r0, [r10, #0x40]
    mov     r0, r4
    mov     r1, r5
    movw    r2, #PANEL_W
    movw    r3, #PANEL_H
    bl      fill_rect

    mov     r0, #INK                @ frame
    str     r0, [r10, #0x40]
    mov     r0, r4
    mov     r1, r5
    movw    r2, #PANEL_W
    mov     r3, #1
    bl      fill_rect
    mov     r0, r4
    movw    r1, #(PANEL_H - 1)      @ too big for an ARM immediate once the
    add     r1, r5, r1              @ list grew past ten rows
    movw    r2, #PANEL_W
    mov     r3, #1
    bl      fill_rect
    mov     r0, r4
    mov     r1, r5
    mov     r2, #1
    movw    r3, #PANEL_H
    bl      fill_rect
    add     r0, r4, #(PANEL_W - 1)
    mov     r1, r5
    mov     r2, #1
    movw    r3, #PANEL_H
    bl      fill_rect

    add     r0, r4, #8              @ title
    add     r1, r5, #6
    PTR     r2, title_text
    bl      draw_str

    LDA     r6, MENU_ST
    ldr     r7, [r6]                @ the cursor the card's menu is on
    str     r7, [r10, #0x38]
    mov     r8, #0                  @ row index
row_loop:
    add     r9, r5, #24
    mov     r0, #ROW_PITCH
    mla     r9, r8, r0, r9          @ this row's top edge

    cmp     r8, r7                  @ selected row: a filled bar, text knocked out
    bne     1f
    mov     r0, #INK
    str     r0, [r10, #0x40]
    add     r0, r4, #2
    sub     r1, r9, #2
    movw    r2, #(PANEL_W - 4)
    mov     r3, #ROW_PITCH
    bl      fill_rect
    mov     r0, #0
    str     r0, [r10, #0x40]
    b       2f
1:  mov     r0, #INK
    str     r0, [r10, #0x40]
2:
    PTR     r2, row_labels          @ twelve-byte slots, so the index is a shift
    mov     r0, #12
    mla     r2, r8, r0, r2
    add     r0, r4, #8
    mov     r1, r9
    bl      draw_str

    mov     r0, r8
    bl      row_value               @ 0 off, 1 on, 2 nothing to show
    mov     r1, #4
    mul     r0, r0, r1
    PTR     r2, value_texts
    add     r2, r2, r0
    add     r0, r4, #VALUE_X
    mov     r1, r9
    bl      draw_str

    add     r8, r8, #1
    cmp     r8, #ROWS
    blo     row_loop

    /* One row that is ours rather than the card's: false colour, which the
     * camera itself only offers as a button you hold down. */
    add     r9, r5, #24
    mov     r0, #ROW_PITCH
    mla     r9, r8, r0, r9
    LDA     r0, MENU_ST             @ our row highlights like any of the card's
    ldr     r0, [r0]
    cmp     r0, #ROWS
    bne     1f
    mov     r0, #INK
    str     r0, [r10, #0x40]
    add     r0, r4, #2
    sub     r1, r9, #2
    movw    r2, #(PANEL_W - 4)
    mov     r3, #ROW_PITCH
    bl      fill_rect
    mov     r0, #0
    str     r0, [r10, #0x40]
    b       2f
1:  mov     r0, #INK
    str     r0, [r10, #0x40]
2:  add     r0, r4, #8
    mov     r1, r9
    PTR     r2, false_colour_label
    bl      draw_str
    /* Where the latch lives depends on who is driving. Pushed over USB, the
     * key gate owns it (+0x70 -> gate+0x24). On a card there is no gate: the
     * menu owns the row itself and keeps its answer at MENU_ST+FC_ON, which is
     * why this row read OFF forever on the first card build. */
    ldr     r1, [r10, #0x70]
    cmp     r1, #0
    ldrne   r0, [r1, #0x24]
    beq     4f
    b       5f
4:  LDA     r1, MENU_ST
    ldr     r0, [r1, #MENU_FC_ON]
5:  cmp     r0, #0
    movne   r0, #1
    moveq   r0, #0
    mov     r1, #4
    mul     r0, r0, r1
    PTR     r2, value_texts
    add     r2, r2, r0
    add     r0, r4, #VALUE_X
    mov     r1, r9
    bl      draw_str

    /* And the second of ours: the green fix, which is display-only and so has
     * no camera state to read back either -- the row shows what the menu last
     * armed, at MENU_ST+MENU_GREEN_ON. */
    add     r9, r9, #ROW_PITCH
    LDA     r0, MENU_ST
    ldr     r0, [r0]
    cmp     r0, #(ROWS + 1)
    bne     6f
    mov     r0, #INK
    str     r0, [r10, #0x40]
    add     r0, r4, #2
    sub     r1, r9, #2
    movw    r2, #(PANEL_W - 4)
    mov     r3, #ROW_PITCH
    bl      fill_rect
    mov     r0, #0
    str     r0, [r10, #0x40]
    b       7f
6:  mov     r0, #INK
    str     r0, [r10, #0x40]
7:  add     r0, r4, #8
    mov     r1, r9
    PTR     r2, green_fix_label
    bl      draw_str
    LDA     r1, MENU_ST
    ldr     r0, [r1, #MENU_GREEN_ON]
    cmp     r0, #0
    movne   r0, #1
    moveq   r0, #0
    mov     r1, #4
    mul     r0, r0, r1
    PTR     r2, value_texts
    add     r2, r2, r0
    add     r0, r4, #VALUE_X
    mov     r1, r9
    bl      draw_str

    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* row_value(index) -> 0 off, 1 on, 2 not a toggle.
 * Mirrors menu.S: Stock is on when everything else is off, Gyro-Gate is on when
 * open gate and gyro are both on, and SEL is a readout rather than a switch. */
row_value:
    push    {r4, r5, r6, lr}
    LDA     r5, MENU_ST
    cmp     r0, #9
    moveq   r0, #2
    beq     9f
    cmp     r0, #8
    bne     1f
    ldr     r0, [r5, #4]
    ldr     r1, [r5, #12]
    and     r0, r0, r1
    cmp     r0, #0
    movne   r0, #1
    b       9f
1:  cmp     r0, #0
    bne     2f
    PTR     r4, state_offsets       @ Stock: on only when nothing else is
    mov     r6, #1
    mov     r1, #0
3:  ldr     r2, [r4, r6, lsl #2]
    ldr     r2, [r5, r2]
    orr     r1, r1, r2
    add     r6, r6, #1
    cmp     r6, #8
    blo     3b
    cmp     r1, #0
    moveq   r0, #1
    movne   r0, #0
    b       9f
2:  PTR     r4, state_offsets
    ldr     r1, [r4, r0, lsl #2]
    ldr     r0, [r5, r1]
    cmp     r0, #0
    movne   r0, #1
9:  pop     {r4, r5, r6, pc}

/* fill_rect(x, y, w, h) in the current colour. Touches nothing outside it. */
fill_rect:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x28]        @ stride
    ldr     r9, [r10, #0x40]        @ colour
    mov     r4, r0
    mov     r5, r1
    mov     r8, r2
    cmp     r3, #0
    beq     9f
    cmp     r8, #0
    beq     9f
1:  mla     ip, r5, r7, r6
    add     ip, ip, r4
    mov     lr, r8
2:  strb    r9, [ip], #1
    subs    lr, lr, #1
    bne     2b
    add     r5, r5, #1
    subs    r3, r3, #1
    bne     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* draw_str(x, y, asciz) in the current colour, left to right. */
draw_str:
    push    {r4, r5, r6, r7, r8, lr}
    mov     r4, r0
    mov     r5, r1
    mov     r6, r2
1:  ldrb    r2, [r6], #1
    cmp     r2, #0
    beq     9f
    mov     r0, r4
    mov     r1, r5
    bl      draw_char
    add     r4, r4, #CELL_W
    b       1b
9:  pop     {r4, r5, r6, r7, r8, pc}

/* draw_char(x, y, character): the font at double size, one 2x2 block per dot. */
draw_char:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x28]        @ stride
    ldr     r8, [r10, #0x40]        @ colour
    sub     r2, r2, #FONT_FIRST
    cmp     r2, #(FONT_LAST - FONT_FIRST)
    bhi     9f
    PTR     r3, font5x7
    add     r3, r3, r2, lsl #2
    add     r3, r3, r2              @ five bytes per glyph
    mov     r4, #0
col_loop:
    ldrb    r5, [r3, r4]
    mov     r9, #0
row_bit:
    mov     lr, #1
    lsl     lr, lr, r9
    tst     r5, lr
    beq     next_bit
    add     lr, r1, r9, lsl #1
    mla     ip, lr, r7, r6
    add     ip, ip, r0
    add     ip, ip, r4, lsl #1
    strb    r8, [ip]
    strb    r8, [ip, #1]
    add     ip, ip, r7
    strb    r8, [ip]
    strb    r8, [ip, #1]
next_bit:
    add     r9, r9, #1
    cmp     r9, #FONT_H
    blo     row_bit
    add     r4, r4, #1
    cmp     r4, #FONT_W
    blo     col_loop
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

.balign 4
/* menu.S's own cursor-to-state-offset table, mirrored. Stock and Gyro-Gate and
 * SEL are computed instead, so their entries here are never read. */
/* The recording rate each option's cell lives in, as SetMovFramerate takes it.
 * Measured on this camera by stepping the value and reading the screen:
 *   1 = 23.98, 2 = 25, 3 = 29.97, 4 = 50, 5 = 59.94.
 * 0 means "leave the camera alone". */
/* Rates that must be selected BEFORE the option is switched on, applied when
 * the cursor lands on the row. M130 repoints whichever rate is live at the
 * moment it is toggled, so 23.98 has to be in place first: that is what makes
 * the M130 rows mean 24p, which is the only M130 that sustains -- 29.97 needs
 * 471 MB/s and stops after about five seconds. */
cursor_rates:
    .word 0        @ STOCK
    .word 0        @ OPEN GATE
    .word 0        @ M98 30P
    .word 1        @ M130        23.98
    .word 1        @ M130 FAST   23.98
    .word 0        @ M98 60P
    .word 0        @ M6 4K
    .word 0        @ GYRO
    .word 0        @ GYRO-GATE
    .word 0        @ SEL
    .word 0        @ 2K120       its own cell, not an FHD rate
    .word 0        @ 672 240     likewise
    .word 0        @ FHD 120     its own cell
    .word 0        @ 2088 120    likewise

option_rates:
    .word 0        @ STOCK
    .word 3        @ OPEN GATE   FHD 29.97 cell
    .word 3        @ M98 30P     FHD 29.97 cell
    .word 0        @ M130        follows whatever rate was selected first
    .word 0        @ M130 FAST   likewise
    .word 5        @ M98 60P     FHD 59.94 cell
    .word 0        @ M6 4K       the 4K cell, not an FHD rate
    .word 0        @ GYRO
    .word 0        @ GYRO-GATE
    .word 0        @ SEL
    .word 0        @ 2K120       its own cell, not an FHD rate
    .word 0        @ 672 240     likewise
    .word 0        @ FHD 120     its own cell
    .word 0        @ 2088 120    likewise

state_offsets:
    .word 0, 4, 24, 8, 40, 28, 36, 12, 0, 0
    .word 44, 48, 80, 84     @ the high-framerate rows

title_text:
    .asciz "FPLAB"
    .balign 4
value_texts:
    .asciz "OFF"
    .asciz "ON";  .space 1     @ four-byte slots: the index is a shift, not a walk
    .asciz "--";  .space 1
    .balign 4
/* Twelve bytes each, so a row index is a multiply rather than a table walk. */
false_colour_label:
    .asciz "FALSE COL"
green_fix_label:
    .asciz "GREEN FIX"
    .balign 4
    .balign 4
row_labels:
    .asciz "STOCK";      .space 6
    .asciz "OPEN GATE";  .space 2
    .asciz "M98 30P";    .space 4
    .asciz "M130";       .space 7
    .asciz "M130 FAST";  .space 2
    .asciz "M98 60P";    .space 4
    .asciz "M6 4K";      .space 6
    .asciz "GYRO";       .space 7
    .asciz "GYRO-GATE";  .space 2
    .asciz "SEL";        .space 8
    .asciz "2K120";       .space 6
    .asciz "672 240";     .space 4
    .asciz "FHD 120";     .space 4
    .asciz "2088 120";    .space 3
thread_name:
    .asciz "fpLABmn"
    .balign 4

#include "font5x7.inc.S"
