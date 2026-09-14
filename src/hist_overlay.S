.syntax unified
.arm
.text
/* fpLAB live histogram overlay, RAM only.
 *
 * Three entry points, all sharing one core:
 *   hist_entry  one pass, for the borrowed shell command handler
 *   hist_body   the resident loop, called by the firmware's own thread pool
 *   hist_spawn  builds that thread and wakes it, once
 *
 * The core reads the detection image channel's descriptor, bins its 8-bit
 * samples, and paints bars into an 8-bit display surface. It never fills the
 * whole layer and never presents.
 *
 * Measured before this payload existed: the detection entry at 0xC375D8C0
 * carries {pixels, width, height}; the OSD surface reported by `display osd 1`
 * is one byte per pixel with the surface width as its stride, and drawn glyph
 * pixels read back as 0xB2. The bars use the same value.
 *
 * Nothing is trusted blind: every pointer and dimension is range-checked, and a
 * refusal is recorded as a status code rather than a wild write.
 *
 * STATE_ADDR is supplied by the deployer, which owns the pool layout.
 *   +0x00 status: 0 running, 1 done, else a refusal code
 *   +0x04 pixels   +0x08 width      +0x0C height     +0x10 samples
 *   +0x14 max bin  +0x18 shift      +0x1C surface base
 *   +0x20 surface stride            +0x24 surface height
 *   +0x28 passes
 *   +0x2C surface base override, 0 = ask the drawable for its backbuffer
 *   +0x30 stride override, used with the base override
 *   +0x34 plot x       +0x38 plot y
 *   +0x3C stop request  +0x40 resident passes  +0x44 resident exited
 *   +0x48 attach result +0x4C create result
 *   +0x58 optional second renderer, called after each pass, 0 for none
 *   +0x5C draw the histogram at all; 0 leaves the screen to the second one
 *   +0x80 thread holder +0xC0 body object      +0xC4 body vtable
 *   +0x100 the 256 bins
 *
 * The override exists because the layer only rotates buffers when something
 * presents. Painting straight into the buffer already on screen is what lets
 * the resident body refresh the plot without taking the display lock at all.
 * With the override set the core calls no firmware drawing code whatsoever.
 */
.equ STATE,      STATE_ADDR
.equ DETECTION,  0xC375D8C0      /* image manager slot 2 */
.equ DRAW_LOCK,  0xC03E5698      /* display handle, takes the display lock */
.equ DRAW_OBJ,   0xC03E56D8      /* handle -> drawable */
.equ CACHE_FN,   0xC000E91C      /* what makes CPU writes visible downstream */
.equ TK_DLY,     0xC01F89C4      /* tk_dly_tsk(ms) */
.equ XT_CREATE,  0xC036E108      /* f(&holder, priority, stack, name) */
.equ XT_ATTACH,  0xC036E1B8      /* f(&holder, body object) -- attach and wake */
.equ BIN_OFF,    0x100
.equ PLOT_W,     256
.equ PLOT_H,     96
.equ INK,        0xB2
.equ PERIOD_MS,  100
.equ PRIORITY,   20
.equ STACK,      0x2000

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global hist_entry
hist_entry:
    push    {r4, lr}
    bl      hist_core
    mov     r0, #0
    pop     {r4, pc}
/* The resident body. The pool worker calls vtable slot +0xC with the body
 * object in r0; returning ends the thread's work, so the loop is here and a
 * host-settable stop request is the only way out.
 *
 * One thread drives every overlay. A second renderer is a word the deployer
 * fills in, not a second thread: spawning one from the shell's own task is
 * what preceded a session where the shell stopped answering, and nothing about
 * drawing needs its own thread anyway. */
.global hist_body
hist_body:
    push    {r4, lr}
    LDA     r4, STATE
1:  ldr     r0, [r4, #0x3C]
    cmp     r0, #0
    bne     2f
    bl      hist_core
    ldr     r0, [r4, #0x40]
    add     r0, r0, #1
    str     r0, [r4, #0x40]
    ldr     r0, [r4, #0x58]         @ the menu panel, when one is placed
    cmp     r0, #0
    blxne   r0
    mov     r0, #PERIOD_MS
    LDA     ip, TK_DLY
    blx     ip
    b       1b
2:  mov     r0, #1
    str     r0, [r4, #0x44]
    mov     r0, #0
    pop     {r4, pc}

/* Build the thread and wake it. The deployer fills in the body object and its
 * vtable first, because only it knows where this blob landed. */
.global hist_spawn
hist_spawn:
    push    {r4, lr}
    LDA     r4, STATE
    mov     r0, #0
    str     r0, [r4, #0x3C]
    str     r0, [r4, #0x44]
    str     r0, [r4, #0x40]
    add     r0, r4, #0x80
    mov     r1, #PRIORITY
    LDA     r2, STACK
    adr     r3, thread_name
    LDA     ip, XT_CREATE
    blx     ip
    str     r0, [r4, #0x4C]
    add     r0, r4, #0x80
    add     r1, r4, #0xC0           @ the ADDRESS of the body object word
    LDA     ip, XT_ATTACH
    blx     ip
    str     r0, [r4, #0x48]
    mov     r0, #0
    pop     {r4, pc}

/* Next to its only user: `adr` reaches a couple of hundred bytes, and this
 * string sat at the end of the file until the payload outgrew that. */
thread_name:
    .asciz  "fpLABhi"
    .align  2

.global hist_core
hist_core:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r0, [r10, #0x28]
    add     r0, r0, #1
    str     r0, [r10, #0x28]
    mov     r0, #0
    str     r0, [r10]
    ldr     r0, [r10, #0x5C]        @ switched off: the thread still runs, so
    ldr     r1, [r10, #0x68]        @ whatever else it draws keeps updating
    str     r0, [r10, #0x68]
    cmp     r0, #0
    bne     1f
    cmp     r1, #0                  @ just turned off: the last plot is still
    movne   r2, #4                  @ sitting in whichever buffers we painted,
    strne   r2, [r10, #0x60]        @ so owe them each a wipe
    b       hist_off
1:  mov     r0, #0                  @ drawing normally again
    str     r0, [r10, #0x64]

    LDA     r4, DETECTION
    ldr     r5, [r4]                @ pixels
    ldr     r6, [r4, #4]            @ width
    ldr     r7, [r4, #8]            @ height
    str     r5, [r10, #4]
    str     r6, [r10, #8]
    str     r7, [r10, #0xC]

    LDA     r0, 0x40000000
    cmp     r5, r0
    movlo   r0, #0x11
    blo     refuse
    LDA     r0, 0x4A000000
    cmp     r5, r0
    movhs   r0, #0x12
    bhs     refuse
    cmp     r6, #0
    moveq   r0, #0x13
    beq     refuse
    movw    r0, #1920
    cmp     r6, r0
    movhi   r0, #0x14
    bhi     refuse
    cmp     r7, #0
    moveq   r0, #0x15
    beq     refuse
    movw    r0, #1200
    cmp     r7, r0
    movhi   r0, #0x16
    bhi     refuse
    mul     r8, r6, r7              @ samples
    LDA     r0, 0x40000
    cmp     r8, r0
    movhi   r0, #0x17
    bhi     refuse
    str     r8, [r10, #0x10]

    add     r3, r10, #BIN_OFF
    mov     r0, #0
    mov     r1, #256
1:  str     r0, [r3], #4
    subs    r1, r1, #1
    bne     1b

    add     r3, r10, #BIN_OFF
    mov     r1, r8
    mov     r2, r5
2:  ldrb    r0, [r2], #1
    ldr     ip, [r3, r0, lsl #2]
    add     ip, ip, #1
    str     ip, [r3, r0, lsl #2]
    subs    r1, r1, #1
    bne     2b

    mov     r0, #0
    mov     r1, #256
    mov     r2, r3
3:  ldr     ip, [r2], #4
    cmp     ip, r0
    movhi   r0, ip
    subs    r1, r1, #1
    bne     3b
    str     r0, [r10, #0x14]
    cmp     r0, #0
    moveq   r0, #0x18
    beq     refuse

    mov     r4, #0                  @ shift: scale by shifting, no division
4:  mov     ip, r0, lsr r4
    cmp     ip, #PLOT_H
    addhi   r4, r4, #1
    bhi     4b
    str     r4, [r10, #0x18]

    ldr     r6, [r10, #0x2C]        @ surface base override
    cmp     r6, #0
    bne     override_surfaces
    LDA     ip, DRAW_LOCK
    blx     ip
    LDA     ip, DRAW_OBJ
    blx     ip
    mov     r5, r0                  @ drawable
    cmp     r5, #0
    moveq   r0, #0x20
    beq     refuse
    ldr     ip, [r5]
    ldr     ip, [ip, #0x10]         @ getBackbuffer
    mov     r0, r5
    blx     ip
    cmp     r0, #0
    moveq   r0, #0x21
    beq     refuse
    ldr     r6, [r0, #4]            @ surface base
    ldr     r1, [r0, #8]
    cmp     r1, #0
    moveq   r0, #0x22
    beq     refuse
    ldr     r7, [r1]                @ surface width, also its stride
    ldr     r8, [r1, #4]            @ surface height
    str     r6, [r10, #0x1C]
    str     r7, [r10, #0x20]
    str     r8, [r10, #0x24]
    mov     r0, r6
    mov     r1, r7
    mov     r2, r8
    bl      hist_paint
    cmp     r0, #0
    bne     refuse
    b       painted

/* The layer rotates three buffers and only the camera's own UI decides which
 * one is on screen, so a resident pass paints every buffer the host measured.
 * Painting one of them is what made the plot vanish the moment the UI
 * presented a different buffer. */
override_surfaces:
    ldr     r7, [r10, #0x30]        @ stride the host measured
    ldr     r8, [r10, #0x24]        @ height the host measured
    str     r6, [r10, #0x1C]
    str     r7, [r10, #0x20]
    mov     r9, #0x2C               @ walk the three base words
surface_loop:
    ldr     r0, [r10, r9]
    cmp     r0, #0
    beq     surface_next
    mov     r1, r7
    mov     r2, r8
    bl      hist_paint
    cmp     r0, #0
    bne     refuse
surface_next:
    cmp     r9, #0x2C
    moveq   r9, #0x50
    addne   r9, r9, #4
    cmp     r9, #0x54               @ the last base word; +0x58 is the renderer
    bls     surface_loop
painted:
    LDA     ip, CACHE_FN            @ CPU writes are only data to the caches
    blx     ip
    mov     r0, #1
    str     r0, [r10]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* Off, with a wipe still owed. Runs the same surface walk as a normal pass but
 * with "clear only" set, so hist_paint blanks its rectangle and draws nothing.
 * Four passes, because the layer rotates buffers and the host may have measured
 * three of them. */
hist_off:
    ldr     r0, [r10, #0x60]
    cmp     r0, #0
    beq     idle
    sub     r0, r0, #1
    str     r0, [r10, #0x60]
    mov     r0, #1
    str     r0, [r10, #0x64]
    ldr     r6, [r10, #0x2C]
    cmp     r6, #0
    bne     override_surfaces
    b       idle                    @ no override: nothing measured to wipe

idle:
    mov     r0, #1
    str     r0, [r10]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

refuse:
    str     r0, [r10]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* hist_paint(base, stride, height) -> 0 or a refusal code.
 * Reads the bins, the shift and the plot origin from the state block. Touches
 * only its own rectangle. */
hist_paint:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    mov     r6, r0
    mov     r7, r1
    mov     r8, r2
    LDA     r10, STATE
    ldr     r4, [r10, #0x18]        @ shift

    LDA     r0, 0xC4000000
    cmp     r6, r0
    movlo   r0, #0x23
    blo     paint_refuse
    LDA     r0, 0xC8000000
    cmp     r6, r0
    movhs   r0, #0x24
    bhs     paint_refuse
    ldr     r9, [r10, #0x34]        @ plot x
    ldr     r5, [r10, #0x38]        @ plot y
    add     r0, r9, #PLOT_W
    cmp     r0, r7
    movhi   r0, #0x25
    bhi     paint_refuse
    add     r0, r5, #PLOT_H
    add     r0, r0, #1
    cmp     r0, r8
    movhi   r0, #0x26
    bhi     paint_refuse

    mov     r0, r5                  @ clear only our own rectangle
    mov     r2, #PLOT_H
clear_row:
    mla     r3, r0, r7, r6
    add     r3, r3, r9
    mov     r1, #0
    mov     ip, #PLOT_W
5:  strb    r1, [r3], #1
    subs    ip, ip, #1
    bne     5b
    add     r0, r0, #1
    subs    r2, r2, #1
    bne     clear_row

    ldr     r0, [r10, #0x64]        @ clear only: the rectangle is blank now,
    cmp     r0, #0                  @ which is the whole job when switched off.
    movne   r0, #0                  @ the flag stays set for the whole walk --
    popne   {r4, r5, r6, r7, r8, r9, r10, pc}  @ every buffer needs the wipe
    add     r3, r10, #BIN_OFF
    mov     r2, #0
bar_loop:
    ldr     r0, [r3, r2, lsl #2]
    mov     r0, r0, lsr r4
    cmp     r0, #PLOT_H
    movhi   r0, #PLOT_H
    cmp     r0, #0
    beq     bar_next
    add     r1, r5, #(PLOT_H - 1)
    mov     ip, r0
bar_column:
    mla     lr, r1, r7, r6
    add     lr, lr, r9
    add     lr, lr, r2
    mov     r0, #INK
    strb    r0, [lr]
    sub     r1, r1, #1
    subs    ip, ip, #1
    bne     bar_column
bar_next:
    add     r2, r2, #1
    cmp     r2, #PLOT_W
    blo     bar_loop

    add     r0, r5, #PLOT_H         @ a baseline, so an empty plot still shows
    mla     r3, r0, r7, r6
    add     r3, r3, r9
    mov     r1, #INK
    mov     ip, #PLOT_W
6:  strb    r1, [r3], #1
    subs    ip, ip, #1
    bne     6b

    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

paint_refuse:
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

