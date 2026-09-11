.syntax unified
.arm
.text
#include "../../reference/fpSup/gyro/ring_task.inc.S"
#include "../../reference/fpSup/gyro/imu_stream.inc.S"
/* fpLAB: RIGHT selects, UP toggles. Stock clears all three features.
 * Gyro-Gate is a linked OG+gyro toggle; High FPS remains independent.
 * ST: cursor, og, hfps, gyro, gyro_ready, init_status, reserved, reserved,
 *     saved_level. Code lives in the allocated pool, not the gyro caves. */
.equ ST, 0xC072FB00
.equ KEY_ORIG, 0xC0265800
.equ TEXT_FN, 0xC03E4620
.equ OSD_FN, 0xC03E3D00
.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm
/* Forward label addresses stay PIC without ADR's rotated-immediate limit. */
.macro PTR reg, label
    ldr \reg, 991f
990: add \reg, pc, \reg
    b 992f
991: .word \label - (990b + 8)
992:
.endm
.global boot
boot:
    push {r4, r5, r6, r7, r8, lr}
    LDA r6, ST
    mov r0, #0
    mov r1, #9
    mov r2, r6
1:  str r0, [r2], #4
    subs r1, r1, #1
    bne 1b
    PTR r4, guard_table
    movw r5, #:lower16:GUARD_COUNT
2:  ldmia r4!, {r0, r1}
    ldr r2, [r0]
    cmp r1, r2
    bne boot_refused
    subs r5, r5, #1
    bne 2b
    LDA r0, LEVEL_GATE
    ldrb r1, [r0]
    str r1, [r6, #32]
    LDA r3, 0xC072E064
    blx r3                         @ proven gyro boot, allocations before hooks
    mov r0, #1
    str r0, [r6, #12]              @ boot armed gyro; turn it off below
    LDA r4, B_PTR
    mov r5, #BUF_N
3:  ldr r0, [r4], #4
    cmp r0, #0
    beq boot_disable
    subs r5, r5, #1
    bne 3b
    LDA r4, G_TEXT
    ldr r0, [r4]
    cmp r0, #0
    beq boot_disable
    mov r0, #1
    str r0, [r6, #16]
boot_disable:
    bl toggle_gyro
    LDA r0, 0xC043A19C
    LDA r1, (0xEB000000 | (((0xC072F800-0xC043A19C-8)>>2)&0xFFFFFF))
    str r1, [r0]
    LDA r0, 0xC072FA00
    mov r1, #0
    str r1, [r0]
    str r1, [r0, #4]
    str r1, [r0, #8]
    str r1, [r0, #12]
    str r1, [r0, #16]
    LDA r3, F_CACHE
    blx r3
    PTR r1, entry
    LDA r0, 0xC091EA38
    str r1, [r0]                   @ publish only after all code/state is ready
    mov r0, #1
    str r0, [r6, #20]
    pop {r4, r5, r6, r7, r8, pc}
boot_refused:
    mov r0, #2
    str r0, [r6, #20]
    pop {r4, r5, r6, r7, r8, pc}

.global entry
entry:
    cmp r1, #0x0C
    beq handle
    cmp r1, #0x14
    beq handle
    LDA r12, KEY_ORIG
    bx r12
handle:
    push {r4, r5, r6, lr}
    LDA r6, ST
    cmp r1, #0x14
    beq do_up
    ldr r0, [r6]
    add r0, r0, #1
    cmp r0, #5
    moveq r0, #0
    str r0, [r6]
    b redraw
 do_up:
    bl busy
    cmp r0, #0
    bne show_busy
    ldr r0, [r6]
    cmp r0, #0
    beq stock
    cmp r0, #1
    beq up_og
    cmp r0, #2
    beq up_hfps
    ldr r1, [r6, #16]
    cmp r1, #0
    beq show_unavailable
    cmp r0, #3
    beq up_gyro
    ldr r0, [r6, #4]
    ldr r1, [r6, #12]
    and r5, r0, r1
    eor r5, r5, #1                 @ linked toggle, not two independent flips
    cmp r0, r5
    beq 1f
    bl toggle_og
1:
    ldr r0, [r6, #12]
    cmp r0, r5
    beq redraw
    bl toggle_gyro
    b redraw
stock:
    ldr r0, [r6, #4]
    cmp r0, #0
    beq 2f
    bl toggle_og
2:
    ldr r0, [r6, #8]
    cmp r0, #0
    beq 3f
    bl toggle_hfps
3:
    ldr r0, [r6, #12]
    cmp r0, #0
    beq redraw
    bl toggle_gyro
    b redraw
up_og:
    bl toggle_og
    b redraw
up_hfps:
    bl toggle_hfps
    b redraw
up_gyro:
    bl toggle_gyro
redraw:
    ldr r0, [r6]
    cmp r0, #0
    bne state_feature
    ldr r1, [r6, #4]
    ldr r2, [r6, #8]
    orr r1, r1, r2
    ldr r2, [r6, #12]
    orrs r1, r1, r2
    moveq r1, #1
    movne r1, #0
    b state_text
state_feature:
    cmp r0, #4
    ldrne r1, [r6, r0, lsl #2]
    ldreq r1, [r6, #4]
    ldreq r2, [r6, #12]
    andeq r1, r1, r2
state_text:
    PTR r4, labels
    add r0, r1, r0, lsl #1
    add r4, r4, r0, lsl #5         @ fixed 32-byte slots, padded visible labels
    b paint
show_busy:
    PTR r4, busy_label
    b paint
show_unavailable:
    PTR r4, unavailable_label
paint:
    bl draw
    mov r0, #1
    pop {r4, r5, r6, pc}

/* Never unhook a running producer/writer or change a recording's geometry. */
busy:
    PTR r2, busy_words
    movw r3, #:lower16:BUSY_WORD_COUNT
    mov r0, #0
1:  ldr r1, [r2], #4
    ldr r1, [r1]
    orr r0, r0, r1
    subs r3, r3, #1
    bne 1b
    PTR r2, busy_bytes
    movw r3, #:lower16:BUSY_BYTE_COUNT
2:  ldr r1, [r2], #4
    ldrb r1, [r1]
    orr r0, r0, r1
    subs r3, r3, #1
    bne 2b
    bx lr

/* Preserve allocations; only change hooks between fully closed takes. */
toggle_gyro:
    push {r4, r5, r6, lr}
    LDA r6, ST
    ldr r5, [r6, #12]
    eor r5, r5, #1
    cmp r5, #0
    beq gyro_hooks
    LDA r0, LEVEL_GATE
    ldrb r1, [r0]
    str r1, [r6, #32]
gyro_hooks:
    PTR r4, gyro_hooks_table
    mov r2, #4
1:  ldmia r4!, {r0, r1, r3}
    cmp r5, #0
    movne r1, r3
    str r1, [r0]
    subs r2, r2, #1
    bne 1b
    LDA r3, F_CACHE
    blx r3
    cmp r5, #0
    bne gyro_level_on
    LDA r0, LEVEL_GATE
    ldr r1, [r6, #32]
    strb r1, [r0]
    b gyro_done
gyro_level_on:
    LDA r0, MODE_OBJ
    ldr r1, [r0]
    lsr r1, r1, #28
    cmp r1, #0xC
    bne gyro_done
    LDA r3, F_MODE_GET
    blx r3
    cmp r0, #MODE_CINE
    LDA r1, LEVEL_GATE
    moveq r0, #0
    strbeq r0, [r1]
gyro_done:
    str r5, [r6, #12]
    pop {r4, r5, r6, pc}

draw:
    push {r4, r5, r6, lr}          @ keep AAPCS SP eight-byte aligned
    sub sp, sp, #16
    mov r5, #4                    @ hardware-proven repeat count, cause unproven
    PTR r0, safe_ret
    str r0, [sp]
draw_loop:
    str r4, [sp, #8]
    mov r0, sp
    add r2, sp, #8
    mov r1, #1
    LDA r3, TEXT_FN
    blx r3
    adr r0, s_one
    str r0, [sp, #8]
    mov r0, sp
    add r2, sp, #8
    mov r1, #1
    LDA r3, OSD_FN
    blx r3
    subs r5, r5, #1
    bne draw_loop
    add sp, sp, #16
    pop {r4, r5, r6, pc}
safe_ret:
    bx lr
s_one: .asciz "1"
.balign 4
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

.balign 4
gyro_hooks_table:
    .word ACCEL_SITE, 0xE3A02000, ARM_ACCEL
    .word START_SITE, 0xE5DB25CE, ARM_START
    .word STOP_SITE, 0xE3500000, ARM_STOP
    .word MODE_SITE, 0xE1A05001, ARM_MODE
busy_words:
    .word T_WANT, T_FOPEN, W_THREAD, T_MBX
    .word B_BUSY, B_BUSY+4, B_BUSY+8, B_BUSY+12
    .word B_BUSY+16, B_BUSY+20, B_BUSY+24, B_BUSY+28
    .word 0xC358E140
.equ BUSY_WORD_COUNT, (. - busy_words) / 4
busy_bytes:
    .word 0xC347E08F, 0xC3476609, 0xC35A952C, 0xC38D2DE8
.equ BUSY_BYTE_COUNT, (. - busy_bytes) / 4
/* Refuse a different firmware or an already modified cold-boot baseline. */
guard_table:
    .word ACCEL_SITE, 0xE3A02000
    .word START_SITE, 0xE5DB25CE
    .word STOP_SITE, 0xE3500000
    .word MODE_SITE, 0xE1A05001
    .word 0xC091EA38, KEY_ORIG
    .word 0xC043A19C, 0xE1A00004
    .word 0xC0BE5888, 0x6A, 0xC0BE5A28, 0x6A, 0xC0BE5BC8, 0x6A
    .word 0xC0B59A28, 0x40888
    .word 0xC0BD9A34, 0x640, 0xC0BE1684, 0x640
    .word 0xC0BD9EFC, 0x640, 0xC0BE1B4C, 0x640
    .word 0xC0BE5858, 0x1B, 0xC0BE59F8, 0x1B, 0xC0BE5B98, 0x1B
.equ GUARD_COUNT, (. - guard_table) / 8
labels:
    .asciz ">STOCK      OFF        "
    .space 8
    .asciz ">STOCK      ON         "
    .space 8
    .asciz ">OPEN GATE  OFF        "
    .space 8
    .asciz ">OPEN GATE  ON         "
    .space 8
    .asciz ">HIGH FPS   OFF        "
    .space 8
    .asciz ">HIGH FPS   ON         "
    .space 8
    .asciz ">GYRO       OFF        "
    .space 8
    .asciz ">GYRO       ON         "
    .space 8
    .asciz ">GYRO-GATE  OFF        "
    .space 8
    .asciz ">GYRO-GATE  ON         "
    .space 8
busy_label: .asciz "STOP RECORDING / WAIT   "
unavailable_label: .asciz "GYRO INIT FAILED       "
.balign 4
