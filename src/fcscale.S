.syntax unified
.arm
.text
/* False Color / EL Zone as a toggle on the button you already mapped, with the
 * stop scale drawn across the bottom of live view.
 *
 * Self-contained: no menu, no key hook, no panel, nothing bound to RIGHT or UP.
 * The only buttons involved are the one the camera's own Custom Button
 * Functions menu already points at False Color.
 *
 *   press 1   mode on, and it stays on
 *   press 2   the scale appears
 *   press 3   both off
 *
 * The camera calls CameraIF vtable +0xCC on press and +0xD0 on release; both
 * are replaced outright (see docs/research/false-colour-el-zone.md). The press
 * handler also takes the display lock once to learn the OSD buffers, because it
 * runs in key context where that is safe, and a drawing thread repaints every
 * 150 ms because the UI rotates three buffers and paints over whatever it
 * finds.
 *
 * State block at STATE:
 *   +0x00 0 off, 1 on, 2 on with the scale   +0x20 stride
 *   +0x04 presses                            +0x24 height
 *   +0x08 releases swallowed                 +0x28..+0x30 the three OSD buffers
 *   +0x0C the scale flag                     +0x34 thread create result
 *   +0x10 buffer-discovery attempts          +0x38 passes
 *   +0x14 scale drawn on the last pass       +0x3C current buffer, while drawing
 *   +0x18 spawn result                       +0x40 current colour
 *   +0x80.. the thread object and its body object
 */
.equ MEMSET,     0xC0015058
.equ POST,       0xC03A0798
.equ POOL_PTR,   0xC3757A7C
.equ DRAW_LOCK,  0xC03E5698
.equ DRAW_OBJ,   0xC03E56D8
.equ UI_STATE,   0xC3033A44         /* 2 live view, 4 playback, 5 camera menu */
.equ UI_LIVEVIEW, 2
.equ CACHE_FN,   0xC000E91C
.equ TK_DLY,     0xC01F89C4
.equ XT_CREATE,  0xC036E108
.equ XT_ATTACH,  0xC036E1B8
.equ PERIOD_MS,  150
.equ PRIORITY,   20
.equ STACK,      0x2000
.equ FETCH_MAX,  8                  /* the display lock is taken at most this
                                     * often, and never again once all three
                                     * buffers are known */
.equ INK,        0xB2               /* what the firmware's own glyphs read as */
.equ FONT_W,     5
.equ FONT_H,     7
.equ CELL_W,     12                 /* (FONT_W + 1) * 2: the font is drawn 2x */
/* Thirteen whole stops. 44 + 12 * 78 = 980, so the last label ends at 1004 on
 * a 1024-wide surface. The cleared strip is a cell wider at each end because
 * the labels are centred on their ticks. */
.equ LEG_X,      44
.equ LEG_STEP,   78
.equ LEG_W,      960
.equ LEG_CLR_X,  (LEG_X - CELL_W)
.equ LEG_CLR_W,  (LEG_W + CELL_W * 2)
.equ LEG_TICK,   10
.equ LEG_H,      29
.equ LEG_UP,     40                 /* the strip's top, above the bottom edge */
/* Palette indices.
 *
 * 0..29 are the camera's own, dumped with `display palette 2 1 <path>` on
 * 2026-09-20: A,R,G,B, and that is all the layer defines. Anything above 29
 * reads past the table, which is why a 256-cell chart showed scattered colour
 * and why 0xB2 came out red -- those colours are whatever memory follows, not
 * ours to rely on.
 *
 * So the card carries its own palette file and loads it at boot with
 * `display osdPalette 1 \FPLAB.PAL`. Entries 0..29 in that file are copied
 * from the camera's own, so nothing the UI draws changes colour; 30 upwards
 * are ours. STOP_BASE..STOP_BASE+12 are the thirteen stops, deep blue under,
 * grey at middle, through yellow to red over. */
.equ STOCK_RED,  1
.equ STOCK_GREY, 4
.equ STOP_BASE,  30
.equ C_WHITE,    43
.equ C_BLACK,    44
.equ C_BACK,     45             /* black at 75% alpha, our own */
.equ C_GREY,     46
.equ LEG_MIN_W,  1024

#ifndef SWATCH
#define SWATCH 0                   /* --swatch: the palette chart instead */
#endif

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

/* Forward label addresses stay position independent. */
.macro PTR reg, label
    ldr \reg, 991f
990: add \reg, pc, \reg
    b 992f
991: .word \label - (990b + 8)
992:
.endm

/* ---------------------------------------------------------------- the hooks */

/* Replaces 0xC03722E8, the press. r0 = the CameraIF object. */
.global fc_press
fc_press:
    push    {r4, r5, lr}
    mov     r4, r0
    LDA     r5, STATE
    bl      learn_surface           @ clobbers r0-r3, keeps r4 and r5
    ldr     r1, [r5, #4]
    add     r1, r1, #1
    str     r1, [r5, #4]
    ldr     r0, [r5]
    add     r0, r0, #1
    cmp     r0, #3
    moveq   r0, #0
    str     r0, [r5]
    cmp     r0, #2                  @ the scale step: the mode is already on
    moveq   r1, #1
    streq   r1, [r5, #0xC]
    popeq   {r4, r5, pc}
    mov     r1, #0
    str     r1, [r5, #0xC]
    cmp     r0, #0
    mov     r1, #0x22               @ off
    movne   r1, #0x21               @ on
    mov     r0, r4
    bl      fc_post
    pop     {r4, r5, pc}

/* Replaces 0xC0372330, the release: swallowed, so letting go does not cancel.
 * The stock method returns the post's result and callers read it as a success
 * flag, so this returns 1. */
.global fc_release
fc_release:
    LDA     r1, STATE
    ldr     r0, [r1, #8]
    add     r0, r0, #1
    str     r0, [r1, #8]
    mov     r0, #1
    bx      lr

/* Draw one pass on demand, from a shell command rather than a key press.
 * The AutoRun borrows the echo handler to call this, so a capture taken a
 * moment later has the chart on screen. Takes no arguments and returns 0.
 */
.global fc_probe
fc_probe:
    push    {r4, r5, lr}
    sub     sp, sp, #4
    LDA     r5, STATE
    bl      learn_surface
    bl      fc_pass
    mov     r0, #0
    add     sp, sp, #4
    pop     {r4, r5, pc}

/* r0 = the CameraIF object, r1 = event id. The stock method body, reproduced,
 * so nothing calls back into a patched method. */
fc_post:
    push    {r4, r5, lr}
    mov     r4, r0
    mov     r5, r1
    sub     sp, sp, #0xBC
    mov     r0, sp
    mov     r1, #0
    mov     r2, #0xBC
    LDA     r3, MEMSET
    blx     r3
    str     r5, [sp]
    mov     r1, #1
    strb    r1, [sp, #4]
    ldr     r0, [r4, #4]
    mov     r1, sp
    LDA     r3, POST
    blx     r3
    add     sp, sp, #0xBC
    pop     {r4, r5, pc}

/* ------------------------------------------------------------- the surfaces */

/* Learn the OSD buffers. Runs from the press handler, which is key context --
 * the same context the camera's own function key runs in, and the only place
 * the display lock is safe to take. The layer rotates three buffers and only
 * the UI decides which is live, so every one seen is remembered and all of
 * them are painted.
 *
 * Bounded twice: nothing happens once the third buffer is known, and never
 * more than FETCH_MAX attempts in case this body only ever presents one.
 * r5 = STATE on entry and exit.
 */
learn_surface:
    push    {r4, r6, r7, lr}
    ldr     r0, [r5, #0x30]
    cmp     r0, #0
    bne     9f                      @ all three known: nothing left to ask for
    ldr     r0, [r5, #0x10]
    cmp     r0, #FETCH_MAX
    bhs     9f
    add     r0, r0, #1
    str     r0, [r5, #0x10]
    LDA     r0, DRAW_LOCK
    blx     r0
    LDA     r1, DRAW_OBJ
    blx     r1
    cmp     r0, #0
    beq     9f
    mov     r7, r0
    ldr     r1, [r7]
    ldr     r1, [r1, #0x10]         @ getBackbuffer
    mov     r0, r7
    blx     r1
    cmp     r0, #0
    beq     9f
    ldr     r2, [r0, #4]            @ the buffer's base
    ldr     r1, [r0, #8]
    cmp     r1, #0
    beq     9f
    ldr     r6, [r1]                @ width, which is also the stride
    str     r6, [r5, #0x20]
    ldr     r6, [r1, #4]
    str     r6, [r5, #0x24]
    mov     r6, #0x28               @ remember this base if it is new
1:  ldr     r0, [r5, r6]
    cmp     r0, r2
    beq     9f
    cmp     r0, #0
    streq   r2, [r5, r6]
    beq     9f
    add     r6, r6, #4
    cmp     r6, #0x30
    bls     1b
9:  pop     {r4, r6, r7, pc}

/* ------------------------------------------------------------- the painting */

.global fc_spawn
fc_spawn:
    push    {r4, lr}
    LDA     r4, STATE
    add     r0, r4, #0x80
    mov     r1, #PRIORITY
    LDA     r2, STACK
    PTR     r3, thread_name
    LDA     ip, XT_CREATE
    blx     ip
    str     r0, [r4, #0x18]
    add     r0, r4, #0x80
    add     r1, r4, #0x140
    LDA     ip, XT_ATTACH
    blx     ip
    str     r0, [r4, #0x34]
    mov     r0, #0
    pop     {r4, pc}

.global fc_body
fc_body:
    push    {r4, lr}
    LDA     r4, STATE
1:  bl      fc_pass
    ldr     r0, [r4, #0x38]
    add     r0, r0, #1
    str     r0, [r4, #0x38]
    mov     r0, #PERIOD_MS
    LDA     ip, TK_DLY
    blx     ip
    b       1b

/* One repaint. Draws into every buffer it has seen, and takes the strip down
 * again on the first pass after the scale is switched off or live view is
 * left, so nothing of ours is ever left on a screen that is not ours. */
fc_pass:
    push    {r4, r5, r6, lr}
    LDA     r4, STATE
    ldr     r0, [r4]                @ state 2 is the only one that draws
    cmp     r0, #2
    moveq   r0, #1
    movne   r0, #0
    mov     r5, r0
    LDA     r1, UI_STATE
    ldr     r1, [r1]
    cmp     r1, #UI_LIVEVIEW
    movne   r5, #0                  @ playback or the camera's menu: not ours
    ldr     r0, [r4, #0x14]
    orrs    r0, r0, r5
    beq     9f                      @ not up, and nothing to take down
    mov     r6, #0x28
1:  ldr     r0, [r4, r6]
    cmp     r0, #0
    beq     2f
    mov     r1, r5
#if SWATCH
    bl      draw_swatches
#else
    bl      draw_legend
#endif
2:  add     r6, r6, #4
    cmp     r6, #0x30
    bls     1b
    str     r5, [r4, #0x14]
    LDA     ip, CACHE_FN            @ our writes are only data to the caches
    blx     ip
9:  pop     {r4, r5, r6, pc}

#if SWATCH
/* The palette chart, in place of the scale: 256 cells, index 0 at the top
 * left, running left to right then down, sixty-four to a row, four rows.
 *
 * The OSD is one byte per pixel into a palette we do not have, which is why
 * the scale came out all one colour: 0xB2 is simply the index the firmware's
 * own glyphs use. Photographing this chart is what turns the palette into
 * something we can pick colours out of.
 *
 * It is anchored to the bottom of whatever surface we were handed and refuses
 * one it does not fit on, exactly like the scale. An earlier version painted
 * 960x480 from a fixed origin with no check at all and froze the camera: if
 * the buffer is smaller or its stride differs, that writes straight past the
 * end of it.
 */
.equ SW_X,       32
.equ SW_CELL_W,  15
.equ SW_CELL_H,  20
.equ SW_COLS,    64
.equ SW_ROWS,    4
.equ SW_W,       (SW_CELL_W * SW_COLS)      /* 960 */
.equ SW_H,       (SW_CELL_H * SW_ROWS)      /* 80 */
.equ SW_UP,      (SW_H + 40)                /* clear of the very bottom edge */

draw_swatches:
    push    {r4, r5, r6, r7, r8, r9, lr}
    sub     sp, sp, #4                      @ seven registers: keep sp aligned
    mov     r8, r1                          @ wanted
    LDA     r7, STATE
    str     r0, [r7, #0x3C]                 @ the buffer fill_rect paints into
    ldr     r0, [r7, #0x20]                 @ stride
    cmp     r0, #(SW_X + SW_W)
    blo     9f
    ldr     r9, [r7, #0x24]                 @ height
    cmp     r9, #SW_UP
    blo     9f
    sub     r9, r9, #SW_UP                  @ the chart's top edge

    mov     r0, #0                          @ clear it either way
    str     r0, [r7, #0x40]
    mov     r0, #SW_X
    mov     r1, r9
    movw    r2, #SW_W
    mov     r3, #SW_H
    bl      fill_rect
    cmp     r8, #0
    beq     9f

    mov     r4, #0                          @ index
1:  and     r5, r4, #(SW_COLS - 1)          @ column
    lsr     r6, r4, #6                      @ row
    str     r4, [r7, #0x40]                 @ each cell is its own index
    mov     r0, #SW_CELL_W
    mul     r0, r5, r0
    add     r0, r0, #SW_X
    mov     r1, #SW_CELL_H
    mla     r1, r6, r1, r9
    mov     r2, #(SW_CELL_W - 1)
    mov     r3, #(SW_CELL_H - 2)
    bl      fill_rect
    add     r4, r4, #1
    cmp     r4, #256
    blo     1b
9:  add     sp, sp, #4
    pop     {r4, r5, r6, r7, r8, r9, pc}
#endif

/* draw_legend(base, wanted): clear our strip, then draw it if wanted. */
draw_legend:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    mov     r9, r1
    ldr     r7, [r10, #0x20]        @ stride
    ldr     r8, [r10, #0x24]        @ height
    movw    r0, #LEG_MIN_W
    cmp     r7, r0
    blo     9f                      @ narrower than the scale: leave it alone
    cmp     r8, #(LEG_UP + LEG_H)
    blo     9f
    sub     r6, r8, #LEG_UP

    mov     r0, #0                  @ clear, transparent
    str     r0, [r10, #0x40]

    mov     r0, #LEG_CLR_X
    mov     r1, r6
    movw    r2, #LEG_CLR_W
    mov     r3, #LEG_H
    bl      fill_rect
    cmp     r9, #0
    beq     9f

    mov     r0, #C_BACK             @ a backdrop, so the numbers read over a
    str     r0, [r10, #0x40]        @ bright scene as well as a dark one
    mov     r0, #LEG_CLR_X
    mov     r1, r6
    movw    r2, #LEG_CLR_W
    mov     r3, #LEG_H
    bl      fill_rect

    mov     r0, #C_GREY             @ the line the ticks stand on
    str     r0, [r10, #0x40]
    mov     r0, #LEG_X
    add     r1, r6, #LEG_TICK
    movw    r2, #LEG_W
    mov     r3, #1
    bl      fill_rect

    mov     r4, #0
1:  PTR     r0, legend_colours      @ blue below, green at zero, red above
    ldrb    r0, [r0, r4]
    str     r0, [r10, #0x40]
    mov     r0, #LEG_STEP
    mul     r5, r4, r0
    add     r5, r5, #LEG_X
    mov     r0, r5
    mov     r1, r6
    mov     r2, #1
    mov     r3, #LEG_TICK
    bl      fill_rect
    PTR     r2, legend_labels       @ four-byte slots, two characters each
    mov     r0, #4
    mla     r2, r4, r0, r2
    sub     r0, r5, #CELL_W         @ centred on the tick
    add     r1, r6, #(LEG_TICK + 3)
    bl      draw_str
    add     r4, r4, #1
    cmp     r4, #13
    blo     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* fill_rect(x, y, w, h) in the current colour.
 *
 * Refuses anything that would leave the buffer, and refuses entirely if no
 * buffer or geometry is known yet. This is the last line of defence and it
 * exists because it was missing: a chart drawn from a fixed origin, with no
 * check, wrote past the end of the buffer and froze the camera.
 */
fill_rect:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ stride
    ldr     r9, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r7, #0
    beq     9f
    cmp     r3, #0                  @ empty height or width: nothing to do
    beq     9f
    cmp     r2, #0
    beq     9f
    adds    r4, r0, r2              @ x + w must stay inside the stride
    bcs     9f
    cmp     r4, r7
    bhi     9f
    adds    r4, r1, r3              @ y + h must stay inside the height
    bcs     9f
    cmp     r4, r9
    bhi     9f
    ldr     r9, [r10, #0x40]        @ colour
    mov     r4, r0
    mov     r5, r1
    mov     r8, r2
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

/* draw_str(x, y, asciz) in the current colour. */
draw_str:
    push    {r4, r5, r6, lr}
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
9:  pop     {r4, r5, r6, pc}

/* draw_char(x, y, character): the font at double size, one 2x2 block per dot. */
draw_char:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]
    ldr     r7, [r10, #0x20]
    ldr     r8, [r10, #0x40]
    sub     r2, r2, #FONT_FIRST
    cmp     r2, #(FONT_LAST - FONT_FIRST)
    bhi     9f
    PTR     r3, font5x7
    add     r3, r3, r2, lsl #2
    add     r3, r3, r2              @ five bytes per glyph
    mov     r4, #0
1:  ldrb    r5, [r3, r4]
    mov     r9, #0
2:  mov     lr, #1
    lsl     lr, lr, r9
    tst     r5, lr
    beq     3f
    add     lr, r1, r9, lsl #1
    mla     ip, lr, r7, r6
    add     ip, ip, r0
    add     ip, ip, r4, lsl #1
    strb    r8, [ip]
    strb    r8, [ip, #1]
    add     ip, ip, r7
    strb    r8, [ip]
    strb    r8, [ip, #1]
3:  add     r9, r9, #1
    cmp     r9, #FONT_H
    blo     2b
    add     r4, r4, #1
    cmp     r4, #FONT_W
    blo     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

.balign 4
thread_name:
    .asciz "fpFCsc"
    .balign 4
/* One palette index per stop: blue below middle grey, green at it, red above.
 * Dumped from the camera on 2026-09-20 with `display palette 2 1 <path>`: the
 * layer carries THIRTY entries, A,R,G,B, and nothing above 29 is defined --
 * which is why an index like 0xB2 came out red and most of a 256-cell chart
 * was invisible. There is no yellow, orange or purple to be had, so this is
 * not EL Zone's own ramp; it is the closest the layer can draw. */
legend_colours:
    .byte STOP_BASE +  0, STOP_BASE +  1, STOP_BASE +  2, STOP_BASE +  3
    .byte STOP_BASE +  4, STOP_BASE +  5, STOP_BASE +  6, STOP_BASE +  7
    .byte STOP_BASE +  8, STOP_BASE +  9, STOP_BASE + 10, STOP_BASE + 11
    .byte STOP_BASE + 12
    .balign 4

legend_labels:
    .asciz "-6"; .space 1
    .asciz "-5"; .space 1
    .asciz "-4"; .space 1
    .asciz "-3"; .space 1
    .asciz "-2"; .space 1
    .asciz "-1"; .space 1
    .asciz " 0"; .space 1
    .asciz "+1"; .space 1
    .asciz "+2"; .space 1
    .asciz "+3"; .space 1
    .asciz "+4"; .space 1
    .asciz "+5"; .space 1
    .asciz "+6"; .space 1
    .balign 4
#include "font5x7.inc.S"
