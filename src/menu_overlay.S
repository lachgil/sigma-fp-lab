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
 *   +0x40 current colour (scratch)           +0x44 menu init status seen
 *   +0x80 thread holder +0xC0 body object    +0xC4 body vtable
 */
.equ STATE,      STATE_ADDR
.equ MENU_ST,    0xC072FB00     /* menu.S's own state block; read only */
.equ MENU_INIT,  20             /*   +20 armed flag                    */
.equ SETTINGS_OBJ, 0xC0057AE8   /* the settings object every setter takes */
.equ CACHE_FN,   0xC000E91C
.equ TK_DLY,     0xC01F89C4
.equ XT_CREATE,  0xC036E108
.equ XT_ATTACH,  0xC036E1B8
.equ INK,        0xB2           /* what the firmware's own glyphs read back as */
.equ CELL_W,     12             /* (FONT_W + 1) * 2 */
.equ ROW_PITCH,  18
.equ ROWS,       10
.equ PANEL_W,    192
.equ PANEL_H,    (24 + ROWS * ROW_PITCH + 6)
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
    add     r1, r4, #0xC0           @ the ADDRESS of the body object word
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

    ldr     r7, [r10, #0x28]        @ stride
    ldr     r8, [r10, #0x2C]        @ surface height
    ldr     r5, [r10, #0x30]        @ panel x
    ldr     r6, [r10, #0x34]        @ panel y
    add     r0, r5, #PANEL_W
    cmp     r0, r7
    movhi   r0, #0x31
    bhi     refuse
    add     r0, r6, #PANEL_H
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
    bl      paint_panel
surface_next:
    add     r9, r9, #4
    cmp     r9, #0x24
    bls     surface_loop

    bl      apply_setting           @ a queued camera setting, if one is waiting
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

/* paint_panel(base): the frame, the title, and every row with its value. */
paint_panel:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    ldr     r4, [r10, #0x30]        @ panel x
    ldr     r5, [r10, #0x34]        @ panel y

    mov     r0, #0                  @ clear the panel, and nothing outside it
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
    add     r1, r5, #(PANEL_H - 1)
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
state_offsets:
    .word 0, 4, 24, 8, 40, 28, 36, 12, 0, 0

title_text:
    .asciz "FPLAB"
    .balign 4
value_texts:
    .asciz "OFF"
    .asciz "ON";  .space 1     @ four-byte slots: the index is a shift, not a walk
    .asciz "--";  .space 1
    .balign 4
/* Twelve bytes each, so a row index is a multiply rather than a table walk. */
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
thread_name:
    .asciz "fpLABmn"
    .balign 4

#include "font5x7.inc.S"
