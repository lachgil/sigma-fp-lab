.syntax unified
.arm
.text
/* Simple resident menu: RIGHT(0x0C) cycles item [OpenGate<->HighFPS],
 * UP(0x14) toggles the shown item on/off. Draws a padded line + composites.
 * All other keys tail-call stock 0xC0265800. State 0xC072EF00: +0 cursor,+4 og,+8 hfps. */
.equ ST,       0xC072EF00
.equ KEY_ORIG, 0xC0265800
.equ TEXT_FN,  0xC03E4620
.equ OSD_FN,   0xC03E3D00
.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm
.global entry
entry:
    cmp     r1, #0x0C
    beq     handle
    cmp     r1, #0x14
    beq     handle
    LDA     r12, KEY_ORIG
    bx      r12
handle:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    cmp     r1, #0x14
    beq     do_up
    ldr     r0, [r6]               @ RIGHT: cursor ^= 1
    eor     r0, r0, #1
    str     r0, [r6]
    b       redraw
do_up:
    ldr     r0, [r6]
    cmp     r0, #0
    bne     up_hfps
    bl      toggle_og
    b       redraw
up_hfps:
    bl      toggle_hfps
redraw:
    ldr     r0, [r6]
    cmp     r0, #0
    bne     rd_hfps
    ldr     r1, [r6, #4]
    cmp     r1, #0
    adrne   r4, s_og_on
    adreq   r4, s_og_off
    b       rd_go
rd_hfps:
    ldr     r1, [r6, #8]
    cmp     r1, #0
    adrne   r4, s_hf_on
    adreq   r4, s_hf_off
rd_go:
    bl      draw
    mov     r0, #1
    pop     {r4, r5, r6, pc}
draw:
    push    {r4, r5, lr}
    mov     r5, #4                 @ repaint N times: OSD is multi-buffered
draw_loop:
    sub     sp, sp, #16
    adr     r0, safe_ret
    str     r0, [sp]
    str     r4, [sp, #8]
    mov     r0, sp
    add     r2, sp, #8
    mov     r1, #1
    LDA     r3, TEXT_FN
    blx     r3
    adr     r0, s_one
    str     r0, [sp, #8]
    mov     r0, sp
    add     r2, sp, #8
    mov     r1, #1
    LDA     r3, OSD_FN
    blx     r3
    add     sp, sp, #16
    subs    r5, r5, #1
    bne     draw_loop
    pop     {r4, r5, pc}
toggle_og:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #4]
    eor     r0, r0, #1
    str     r0, [r6, #4]
    cmp     r0, #0
    beq     og_off
    mov     r5, #0x75
    LDA     r4, 0xC0BE5888
    str     r5, [r4]
    LDA     r4, 0xC0BE5A28
    str     r5, [r4]
    LDA     r4, 0xC0BE5BC8
    str     r5, [r4]
    LDA     r5, 0x00041C70
    LDA     r4, 0xC0B59A28
    str     r5, [r4]
    mov     r5, #0x400
    LDA     r4, 0xC0BD9A34
    str     r5, [r4]
    LDA     r4, 0xC0BE1684
    str     r5, [r4]
    LDA     r4, 0xC0BD9EFC
    str     r5, [r4]
    LDA     r4, 0xC0BE1B4C
    str     r5, [r4]
    mov     r5, #1
    LDA     r4, 0xC072FA10
    str     r5, [r4]
    pop     {r4, r5, r6, pc}
og_off:
    mov     r5, #0x6A
    LDA     r4, 0xC0BE5888
    str     r5, [r4]
    LDA     r4, 0xC0BE5A28
    str     r5, [r4]
    LDA     r4, 0xC0BE5BC8
    str     r5, [r4]
    LDA     r5, 0x00040888
    LDA     r4, 0xC0B59A28
    str     r5, [r4]
    LDA     r5, 0x640
    LDA     r4, 0xC0BD9A34
    str     r5, [r4]
    LDA     r4, 0xC0BE1684
    str     r5, [r4]
    LDA     r4, 0xC0BD9EFC
    str     r5, [r4]
    LDA     r4, 0xC0BE1B4C
    str     r5, [r4]
    mov     r5, #0
    LDA     r4, 0xC072FA10
    str     r5, [r4]
    pop     {r4, r5, r6, pc}
toggle_hfps:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #8]
    eor     r0, r0, #1
    str     r0, [r6, #8]
    cmp     r0, #0
    movne   r5, #0x3A
    moveq   r5, #0x1B
    LDA     r4, 0xC0BE5858
    str     r5, [r4]
    LDA     r4, 0xC0BE59F8
    str     r5, [r4]
    LDA     r4, 0xC0BE5B98
    str     r5, [r4]
    pop     {r4, r5, r6, pc}
safe_ret:
    bx      lr
s_one:    .asciz "1"
    .align 2
s_og_on:  .asciz ">OPEN GATE  ON      "
    .align 2
s_og_off: .asciz ">OPEN GATE  OFF     "
    .align 2
s_hf_on:  .asciz ">HIGH FPS   ON      "
    .align 2
s_hf_off: .asciz ">HIGH FPS   OFF     "
    .align 2
