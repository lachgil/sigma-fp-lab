.syntax unified
.arm
.text
#include "../../reference/fpSup/gyro/ring_task.inc.S"
#include "../../reference/fpSup/gyro/imu_stream.inc.S"
/* fpLAB: RIGHT selects, UP toggles. Stock clears every feature.
 * Options: Stock, Open Gate (M117), M98 30P, M130 30P, M130 FAST, M130 24P,
 * M98 60P, M6 4K, Gyro, Gyro-Gate (linked Open Gate + gyro), and a read-only
 * SEL line showing the selector the hook's probe last saw.
 * None of the repointed modes is reachable from the stock UI: no picker cell in
 * the image names M117, M98, M130 or M6. (M58 does have its own cells, so FHD
 * 119.88 is already a stock preset and no longer has an option here.)
 * Conflicts are declared as group bits rather than written out per option:
 * release() switches off anything sharing a bit with what is being turned on.
 * ST: cursor, og, m130, gyro, gyro_ready, init_status, m98a, m98b,
 *     saved_level, m6, m130f, m130q; +0x40 is the composed readout line.
 * Code lives in the allocated pool, not the gyro caves. */
.equ ST, 0xC072FB00
.equ KEY_ORIG, 0xC0265800
.equ TEXT_FN, 0xC03E4620
.equ OSD_FN, 0xC03E3D00
.equ ARMED, 0xC072FA10
.equ SELS, 0xC072FA20
.equ PROBE, 0xC072FA30
.equ GEOM, 0xC072FA40
.equ KEEP, 0xC072FA80           /* count, then the cells the hook holds */
.equ SEL_2997, 175              /* measured on hardware: FHD/29.97 CinemaDNG */
#ifndef OG60_SEL
/* Measured on hardware 2026-09-11: the probe read AF (175) with the preset at
 * FHD/29.97 and AD (173) at FHD/59.94, which is the framerate-tracking
 * cross-check, not a single reading. */
#define OG60_SEL 173
#endif
.equ SEL_5994, OG60_SEL
.equ M98_VMAX, 0xC0B59548       /* M98 timing entry: stock 0x0004082E = 2094  */
.equ M130_VMAX, 0xC0B59A88      /* M130 timing entry: stock 0x04201014 = 4116 */
.equ M130_HMAX, 0xC0B59A84      /* M130 line period: stock 0x01BD01BD = 445   */
.equ SEL_2398, 176              /* measured: FHD/23.976, probe read B0         */
/* The native On/Off we borrow: Playback -> Slideshow -> Slideshow Settings ->
 * Repeat. Measured on hardware 2026-09-12: both the camera's own menu and
 * `menu SetSlideshowRepeat` write this byte, and the getter follows it. The
 * settings store the item table points at (0xC31B32BC+0x28) is NOT written by
 * either, so it is the wrong address to poll however reasonable it looks. */
.equ NATIVE_SW, 0xC31ADA36
/* Which resource each option repoints. Two options sharing a bit cannot both
 * be on: one picker cell or one timing entry cannot hold two values. */
.equ GRP_2997, 1                /* FHD 29.97 picker cell  */
.equ GRP_M130, 2                /* M130's timing entry    */
.equ GRP_M98,  4                /* M98's timing entry     */
.equ GRP_5994, 8                /* FHD 59.94 picker cell  */
.equ GRP_2398, 16               /* FHD 23.976 picker cell */
.equ GRP_4K,   32               /* 4K picker cell         */
.equ CELLS_N, 52                /* cells the last change rewrote  */
.equ M130_N,    56              /* pairs M130 recorded            */
.equ M130_SEL,  60              /* the selector it learned        */
.equ M130_TGT,  64              /* which option's target table    */
.equ M130_PAIRS, 0x60           /* (address, old id) pairs        */
.equ NATIVE_LAST, 68            /* last value read from the native switch */
.equ M130_MAX,  12
.equ LINE,   0xC0               /* the composed readout line      */
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
/* Only the low half of a timing word is HMAX/VMAX; the high half differs per
 * mode (4 on the binned entries, 1056 on M130, 952 on M6) and is preserved. */
.macro VMAX cell, value
    LDA     r5, \value
    LDA     r4, \cell
    str     r5, [r4]
.endm
.global boot
boot:
    push {r4, r5, r6, r7, r8, lr}
    LDA r6, ST
    mov r0, #0
    mov r1, #64                    @ the whole state block, AUTO's table included
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
    LDA r2, 0xC072FA00             @ telemetry, armed, selectors, probe, canvas
    mov r0, #0
    mov r1, #32
4:  str r0, [r2], #4
    subs r1, r1, #1
    bne 4b
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
    push {r0, r1, r2, r3, r12, lr}
    bl sync_native                 @ adopt the native toggle before dispatching
    pop {r0, r1, r2, r3, r12, lr}
    cmp r1, #0x0C
    beq handle
    cmp r1, #0x14
    beq handle
    LDA r12, KEY_ORIG
    bx r12

/* Make Open Gate follow the borrowed native switch. Runs on every key, so it
 * has to be cheap and must refuse exactly where the menu's own UP refuses:
 * before the payload is armed, and while anything is recording -- rewriting
 * geometry under a running take is the one thing that must never happen.
 *
 * Edge-triggered, not level-triggered. Comparing the switch against the option
 * every time makes the two fight: turn Open Gate on from our menu and the next
 * keypress sees the native switch still off and turns it straight back off.
 * So act only when the switch itself moves, and let either source win until
 * the other is touched. NATIVE_LAST holds what the switch read last time. */
sync_native:
    push {r4, r5, r6, lr}
    LDA r6, ST
    ldr r0, [r6, #20]
    cmp r0, #1                     @ armed only; a refused boot stays inert
    bne 1f
    LDA r4, NATIVE_SW
    ldrb r0, [r4]
    cmp r0, #0
    movne r0, #1                   @ any nonzero is On; the state word is 0 or 1
    ldr r5, [r6, #NATIVE_LAST]
    cmp r0, r5
    beq 1f                         @ switch unmoved: leave the menu's choice alone
    mov r5, r0
    bl busy
    cmp r0, #0
    bne 1f                         @ recording: do not latch it, retry next key
    str r5, [r6, #NATIVE_LAST]
    ldr r1, [r6, #4]
    cmp r5, r1
    beq 1f                         @ already in that state; nothing to toggle
    mov r0, #GRP_2997              @ same conflict release the menu's UP does
    mov r1, #4
    bl release
    bl toggle_og
1:  pop {r4, r5, r6, pc}
handle:
    push {r4, r5, r6, lr}
    LDA r6, ST
    cmp r1, #0x14
    beq do_up
    ldr r0, [r6]
    add r0, r0, #1
    cmp r0, #10
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
    beq up_m98a
    cmp r0, #3
    beq up_m130
    cmp r0, #4
    beq up_m130f
    cmp r0, #5
    beq up_m98b
    cmp r0, #6
    beq up_m6
    cmp r0, #9
    beq show_sel                   @ read-only readout
    ldr r1, [r6, #16]
    cmp r1, #0
    beq show_unavailable
    cmp r0, #7
    beq up_gyro
    mov r0, #GRP_2997              @ Gyro-Gate owns the 29.97 cell
    mov r1, #4
    bl release
    ldr r0, [r6, #4]
    ldr r1, [r6, #12]
    and r5, r0, r1
    eor r5, r5, #1                 @ linked toggle, not two independent flips
    cmp r0, r5
    beq 1f
    bl toggle_og
1:  ldr r0, [r6, #12]
    cmp r0, r5
    beq redraw
    bl toggle_gyro
    b redraw

/* Switch off every option that holds a resource in r0's group mask, except the
 * one at state offset r1, which is about to toggle itself. Table-driven: a new
 * option declares its groups once and every conflict follows from that. */
release:
    push {r4, r5, r6, r7, r8, r9, lr}
    mov r8, r0
    mov r9, r1
    LDA r6, ST
    PTR r7, entry                  @ runtime base for the table's offsets
    PTR r4, features_table
    movw r5, #:lower16:FEATURE_COUNT
1:  ldmia r4!, {r0, r1, r2}
    tst r1, r8
    beq 2f
    cmp r0, r9
    beq 2f
    ldr r3, [r6, r0]
    cmp r3, #0
    beq 2f
    add r2, r2, r7
    push {r4, r5}                  @ two registers: the stack stays 8-aligned
    blx r2
    pop {r4, r5}
2:  subs r5, r5, #1
    bne 1b
    pop {r4, r5, r6, r7, r8, r9, pc}

stock:
    mov r0, #63                    @ every group
    mov r1, #0xFF                  @ an offset no option has: skip nothing
    bl release
    ldr r0, [r6, #28]
    cmp r0, #0
    beq 1f
    bl toggle_m98b
1:  ldr r0, [r6, #36]
    cmp r0, #0
    beq 2f
    bl toggle_m6
2:  ldr r0, [r6, #12]
    cmp r0, #0
    beq redraw
    bl toggle_gyro
    b redraw
up_og:
    mov r0, #(GRP_2997)
    mov r1, #4
    bl release
    bl toggle_og
    b redraw
up_m98a:
    mov r0, #(GRP_2997|GRP_M98)
    mov r1, #24
    bl release
    bl toggle_m98a
    b redraw
up_m130:
    mov r0, #(GRP_2997|GRP_M130)
    mov r1, #8
    bl release
    bl toggle_m130
    cmp r0, #0
    beq show_need_sel
    b redraw
up_m130f:
    mov r0, #(GRP_2997|GRP_M130)
    mov r1, #40
    bl release
    bl toggle_m130f
    cmp r0, #0
    beq show_need_sel
    b redraw
up_m98b:
    movw r0, #:lower16:SEL_5994
    cmp r0, #0
    beq show_need_sel              @ never rewrite geometry for a guessed selector
    mov r0, #(GRP_5994|GRP_M98)
    mov r1, #28
    bl release
    bl toggle_m98b
    b redraw
up_m6:
    mov r0, #GRP_4K
    mov r1, #36
    bl release
    bl toggle_m6
    b redraw
up_gyro:
    bl toggle_gyro
redraw:
    ldr r0, [r6]
    cmp r0, #0
    beq state_stock
    cmp r0, #9
    beq show_sel
    cmp r0, #8
    beq state_gate
    PTR r2, state_offsets
    ldr r1, [r2, r0, lsl #2]
    ldr r1, [r6, r1]
    b state_text
state_stock:
    PTR r2, state_offsets
    mov r3, #9
    mov r1, #0
1:  ldr r0, [r2, r3, lsl #2]
    ldr r0, [r6, r0]
    orr r1, r1, r0
    subs r3, r3, #1
    bne 1b
    ldr r0, [r6, #12]
    orrs r1, r1, r0
    moveq r1, #1
    movne r1, #0
    b state_text
state_gate:
    ldr r1, [r6, #4]
    ldr r2, [r6, #12]
    and r1, r1, r2
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
    b paint
/* A framerate whose selector has not been measured shows what the probe last
 * saw for a 1936x1090 row: record that framerate once with nothing repointed
 * and the number to build with is on the screen. */
show_need_sel:
    PTR r0, need_sel_label
    mov r1, #4
    bl compose
    b paint
/* Read-only: whatever selector the probe last saw for an FHD row. Switch the
 * preset to a framerate, read the two hex digits, and that is the number a new
 * option for that framerate has to be built with. */
show_sel:
    PTR r0, sel_label
    mov r1, #4
    bl compose
    push {r4, lr}
    LDA r0, ST                     @ C: cells the last change rewrote
    ldr r1, [r0, #CELLS_N]
    add r2, r4, #9
    bl puthex
    LDA r0, ST                     @ L: cells naming the ACTIVE option's mode.
    ldr r0, [r0, #M130_TGT]        @ Which id that is depends on the option --
    cmp r0, #0                     @ M130 installs 130, M10 installs 10 -- so
    movne r1, r0                   @ read it from the target table rather than
    ldrne r0, [r1]                 @ assuming, which reported a false 00.
    moveq r0, #0x82
    bl tally
    mov r1, r0
    add r2, r4, #14
    bl puthex
    pop {r4, lr}
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


/* Compose a readout line at ST+0x40 from a 24-byte template in r0, patching the
 * probe's last selector in as two hex digits at offset r1. Returns r4 = line. */
compose:
    push    {r5, r6, lr}
    LDA     r6, ST
    add     r4, r6, #LINE
    mov     r2, r4
    mov     r3, #6
1:  ldr     r5, [r0], #4
    str     r5, [r2], #4
    subs    r3, r3, #1
    bne     1b
    add     r2, r4, r1
    LDA     r0, PROBE
    ldr     r1, [r0]
    bl      puthex
    pop     {r5, r6, pc}

/* r1 = value, r2 = where: two hex digits. */
puthex:
    lsr     r0, r1, #4
    and     r0, r0, #0xF
    cmp     r0, #10
    addlo   r0, r0, #48
    addhs   r0, r0, #55
    strb    r0, [r2]
    and     r0, r1, #0xF
    cmp     r0, #10
    addlo   r0, r0, #48
    addhs   r0, r0, #55
    strb    r0, [r2, #1]
    bx      lr

/* The raw_zoom scaler, per profile.
 *
 * The camera's own `imager mode_now` reports a raw_zoom stage: 1024 is unity
 * and the FHD profiles ship 0x640 = 1600, a 1.5625x downscale. That is what
 * made every rate except 29.97 record a shrunken frame in the corner of a
 * full-size one -- 3968/1.5625 = 2540 and 2640/1.5625 = 1690, which is exactly
 * what the clips measured. Open gate only ever set unity for the FHD 29.97
 * profile, so that was the only rate that came out whole.
 *
 * The four arrays are indexed by profile with a 4-byte stride, and
 * profile = selector - 53, so the cells for any rate are the 29.97 ones offset
 * by (selector - 175) * 4. Verified on hardware: the predicted 25p cells all
 * held 0x640, and writing unity to them made M130 fill the frame at 25p.
 *
 * r0 = selector, r1 = value (0x400 unity, 0x640 stock). */
set_zoom:
    push    {r4, r5, r6, lr}
    sub     r0, r0, #175
    lsl     r0, r0, #2             @ signed: 173 and 176 are both in range
    PTR     r4, zoom_bases
    mov     r5, #4
1:  ldr     r6, [r4], #4
    add     r6, r6, r0
    str     r1, [r6]
    subs    r5, r5, #1
    bne     1b
    pop     {r4, r5, r6, pc}

/* Rewrite every cell naming mode r0 so it names r1; returns the count, which
 * the SEL line reports.
 *
 * Hardcoding a few addresses failed twice on hardware. A mode id appears in the
 * 0xC0BE5700 records -- {group, format id, mode, descriptor} at 0x10 stride --
 * AND in each of the three parallel tables above them, and which of those a
 * given framerate reads is NOT uniform: open gate works rewriting three of
 * M106's four cells, while the 23.976 row has five and rewriting three left the
 * sensor on its stock mode. That failure has a specific signature: the canvas
 * applies, the mode does not, and the clip is the stock raster in the top-left
 * corner of an oversized frame with garbage in the rest.
 *
 * So rewrite them all. Reversing scans for the new id and writes the old one
 * back, which is unambiguous because any two options that could produce the
 * same id share a group bit and cannot be on together. */
/* r0 = a mode id -> r0 = how many cells name it right now. Read-only. */
tally:
    push    {r4, r5, r6, lr}
    LDA     r4, 0xC0BE5700
    LDA     r5, 0xC0BE5D00
    mov     r6, #0
1:  ldr     r1, [r4]
    cmp     r1, r0
    addeq   r6, r6, #1
    add     r4, r4, #4
    cmp     r4, r5
    bne     1b
    mov     r0, r6
    pop     {r4, r5, r6, pc}

repoint:
    push    {r4, r5, r6, r7, lr}
    LDA     r4, 0xC0BE5700
    LDA     r5, 0xC0BE5D00
    mov     r7, #0
1:  ldr     r6, [r4]
    cmp     r6, r0
    bne     2f
    str     r1, [r4]
    add     r7, r7, #1
2:  add     r4, r4, #4
    cmp     r4, r5
    bne     1b
    LDA     r6, ST
    str     r7, [r6, #CELLS_N]
    mov     r0, r7
    pop     {r4, r5, r6, r7, pc}

/* The cells and words every repointed framerate shares. Recomputed from all
 * the flags, because turning one feature off must not take state another still
 * needs -- and the canvas is per selector slot, so a 3968x2640 mode at 29.97
 * can run beside a 3032x2012 one at 59.94. */
apply_shared:
    push    {r4, r5, r6, r7, lr}
    LDA     r6, ST
    ldr     r0, [r6, #4]
    ldr     r1, [r6, #24]
    orr     r7, r0, r1             @ 29.97 slot: Open Gate or M98 30P
    ldr     r1, [r6, #8]
    orr     r0, r7, r1             @ ...or M130 30P
    ldr     r1, [r6, #40]
    orr     r0, r0, r1             @ ...or M130 FAST
    ldr     r1, [r6, #28]
    orr     r0, r0, r1             @ ...or the 59.94 slot
    orrs    r0, r0, r1             @ (the 59.94 slot closes the list)
    movne   r5, #1
    moveq   r5, #0
    LDA     r4, ARMED
    str     r5, [r4]
    /* Slot 0, at the measured 29.97 selector. */
    LDA     r4, SELS
    LDA     r5, GEOM
    cmp     r7, #0
    bne     shared_2997_binned
    ldr     r0, [r6, #8]
    ldr     r1, [r6, #40]
    orrs    r0, r0, r1
    bne     shared_2997_full
    mov     r0, #0                 @ nobody holds it: publish no canvas
    str     r0, [r4]
    mov     r1, #0
    mov     r2, #0
    mov     r3, #0
    stmia   r5, {r0, r1, r2, r3}
    b       shared_5994
shared_2997_binned:
    mov     r0, #SEL_2997
    str     r0, [r4]
    movw    r0, #3032
    movw    r1, #2012
    movw    r2, #3008
    movw    r3, #2000
    stmia   r5, {r0, r1, r2, r3}
    b       shared_5994
shared_2997_full:
    ldr     r0, [r6, #M130_SEL]    @ learned from the probe, never baked
    str     r0, [r4]
    ldr     r2, [r6, #M130_TGT]    @ the canvas this option asked for
    ldr     r0, [r2, #12]
    ldr     r1, [r2, #16]
    /* A 1:1 window, not a binned raster, so the 12/6 crop margin measured on
     * M117's DNGs does not carry over and no margin is claimed: confirmed on
     * hardware, DefaultCropSize came back as the full raster. */
    mov     r2, r0
    mov     r3, r1
    stmia   r5, {r0, r1, r2, r3}
    b       shared_5994
shared_5994:
    ldr     r0, [r6, #28]
    cmp     r0, #0
    movne   r1, #SEL_5994
    moveq   r1, #0
    str     r1, [r4, #4]
    movw    r0, #3032
    movw    r1, #2012
    movw    r2, #3008
    movw    r3, #2000
    ldr     r7, [r6, #28]
    cmp     r7, #0
    moveq   r0, #0
    moveq   r1, #0
    moveq   r2, #0
    moveq   r3, #0
    add     r5, r5, #0x10
    stmia   r5, {r0, r1, r2, r3}
    pop     {r4, r5, r6, r7, pc}

/* Open Gate: M106 -> M117 in the FHD 29.97 cell, and M117's own timing entry
 * slowed from 99.9 fps to 29.97 so the recorder gets the rate it asked for. */
toggle_og:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #4]
    eor     r0, r0, #1
    str     r0, [r6, #4]
    cmp     r0, #0
    beq     og_off
    mov     r0, #0x6a
    mov     r1, #0x75
    bl      repoint
    VMAX    0xC0B59A28, 0x00041C70
    mov     r0, #SEL_2997
    movw    r1, #0x400
    bl      set_zoom
    b       og_done
og_off:
    mov     r0, #0x75
    mov     r1, #0x6a
    bl      repoint
    VMAX    0xC0B59A28, 0x00040888
    mov     r0, #SEL_2997
    movw    r1, #0x640
    bl      set_zoom
og_done:
    bl      apply_shared
    pop     {r4, r5, r6, pc}

/* M98 30P: the same 3032x2012 canvas as Open Gate from a different sensor mode
 * (hmax 445, 12.44 ms readout instead of M117's 330 / 9.22 ms), at the already
 * measured 29.97 selector. Timing entry 2094 -> 5398 gives 29.97 fps. A
 * comparison against M117, not an upgrade: same 2x2 binning, more skew. */
toggle_m98a:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #24]
    eor     r0, r0, #1
    str     r0, [r6, #24]
    cmp     r0, #0
    beq     m98a_off
    mov     r0, #0x6a
    mov     r1, #0x62
    bl      repoint
    VMAX    M98_VMAX, 0x00041516
    mov     r0, #SEL_2997
    movw    r1, #0x400
    bl      set_zoom
    b       m98a_done
m98a_off:
    mov     r0, #0x62
    mov     r1, #0x6a
    bl      repoint
    VMAX    M98_VMAX, 0x0004082E
    mov     r0, #SEL_2997
    movw    r1, #0x640
    bl      set_zoom
m98a_done:
    bl      apply_shared
    pop     {r4, r5, r6, pc}

/* M130, applied to whatever FHD CinemaDNG preset is currently selected.
 *
 * Baking a framerate in failed repeatedly, because BOTH halves of the job are
 * decided at runtime and not in the image:
 *
 *   The selector. Measured 175 at FHD/29.97 12-bit, 173 at 59.94, 176 at
 *   23.976 -- and 156 at another FHD configuration, so bit depth moves it too.
 *   A baked selector means the hook never fires for any other combination and
 *   the camera quietly records perfectly ordinary files. So it comes from the
 *   probe, which follows whatever the live preset is using.
 *
 *   The mode id. The picker cells are written by the camera when a preset is
 *   selected, so which id sits there is not fixed: M109 and M113 are both
 *   23.976, and M113 appears nowhere in the image. So match ANY id of a rate
 *   the table can serve, take the frame length from the row that matched, and
 *   record each (address, old id) pair so it can be put back exactly.
 *
 * A cell already holding M130 is adopted rather than skipped, and recorded
 * against this rate's first stock id. Skipping it leaked: measured on hardware
 * as C=04 with L=05, one cell left naming M130 after a revert -- which means
 * something copies values between these cells behind us.
 *
 * r0 = which frame-length column (4 = line period 445, 8 = 330).
 * Returns r0 = 1 applied, 0 refused with nothing changed. */
m130_apply:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r6, ST
    str     r1, [r6, #M130_TGT]    @ which full-readout mode this is for
    mov     r7, r0                 @ the column this caller wants
    mov     r10, #0                @ before any branch to the undo path
    LDA     r0, PROBE
    ldr     r9, [r0]
    cmp     r9, #0
    beq     m130_refuse            @ nothing seen yet: pick a preset first
    PTR     r8, m130_rates
1:  ldr     r0, [r8]
    cmp     r0, #0
    beq     m130_refuse            @ this selector has never been measured
    cmp     r0, r9
    beq     2f
    add     r8, r8, #20
    b       1b
2:  ldr     r0, [r8, #12]          @ the two ids this rate can appear as
    ldr     r1, [r8, #16]
    LDA     r4, 0xC0BE5700
    LDA     r5, 0xC0BE5D00
3:  ldr     r2, [r4]
    ldr     r3, [r6, #M130_TGT]
    ldr     r3, [r3]
    cmp     r2, r3
    moveq   r2, r0                 @ a stray left by an earlier toggle: adopt it
    beq     4f                     @ so the revert puts a stock id back
    cmp     r2, r0
    beq     4f
    cmp     r2, r1
    bne     5f
4:  cmp     r10, #M130_MAX
    beq     5f
    add     r3, r6, #M130_PAIRS
    add     r3, r3, r10, lsl #3
    str     r4, [r3]               @ where
    str     r2, [r3, #4]           @ and what it held
    ldr     r2, [r6, #M130_TGT]
    ldr     r2, [r2]               @ the mode id this option installs
    str     r2, [r4]
    add     r10, r10, #1
5:  add     r4, r4, #4
    cmp     r4, r5
    bne     3b
    cmp     r10, #0
    beq     m130_refuse            @ that rate is not in the live table at all
    ldr     r0, [r8, r7]           @ the frame length for this rate
    str     r10, [r6, #M130_N]
    str     r9, [r6, #M130_SEL]
    str     r10, [r6, #CELLS_N]
    /* Hand the addresses to the hook so it can hold them against whatever
     * rewrites them when a preset is re-latched. Count written last, so the
     * hook never walks a partial list. */
    LDA     r2, KEEP
    mov     r3, #0
    str     r3, [r2]
    add     r1, r6, #M130_PAIRS
    mov     r3, r10
    add     r2, r2, #4
7:  ldr     r4, [r1], #8
    str     r4, [r2], #4
    subs    r3, r3, #1
    bne     7b
    LDA     r2, KEEP
    str     r10, [r2]
    ldr     r1, [r6, #M130_TGT]
    ldr     r1, [r1, #4]           @ that mode's own timing entry
    ldr     r2, [r1]
    lsr     r2, r2, #16
    lsl     r2, r2, #16            @ the high half differs per mode; keep it
    orr     r2, r2, r0
    str     r2, [r1]
    mov     r0, r9                 @ the selector this preset uses...
    movw    r1, #0x400
    bl      set_zoom               @ ...so its scaler runs at unity
    mov     r0, #1
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}
m130_refuse:
    add     r0, r6, #M130_PAIRS
5:  cmp     r10, #0
    beq     6f
    ldr     r1, [r0]
    ldr     r2, [r0, #4]
    str     r2, [r1]               @ leave nothing half-applied
    add     r0, r0, #8
    sub     r10, r10, #1
    b       5b
6:  mov     r0, #0
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* Put every recorded cell back to exactly what it held, and the timing entry
 * back to stock. */
m130_revert:
    push    {r4, r5, r6, r7, lr}
    LDA     r6, ST
    ldr     r7, [r6, #M130_N]
    add     r4, r6, #M130_PAIRS
1:  cmp     r7, #0
    beq     2f
    ldr     r0, [r4]
    ldr     r1, [r4, #4]
    str     r1, [r0]
    add     r4, r4, #8
    sub     r7, r7, #1
    b       1b
2:  ldr     r0, [r6, #M130_SEL]
    cmp     r0, #0
    beq     3f
    movw    r1, #0x640
    bl      set_zoom               @ put that profile's scaler back
3:  LDA     r0, KEEP
    mov     r1, #0
    str     r1, [r0]               @ stop the hook holding them
    ldr     r0, [r6, #M130_TGT]
    cmp     r0, #0
    beq     4f
    ldr     r1, [r0, #4]           @ its timing entry
    ldr     r2, [r1]
    lsr     r2, r2, #16
    lsl     r2, r2, #16
    ldr     r3, [r0, #8]           @ and its stock frame length
    orr     r2, r2, r3
    str     r2, [r1]
4:
    mov     r0, #0
    str     r0, [r6, #M130_N]
    str     r0, [r6, #M130_SEL]
    pop     {r4, r5, r6, r7, pc}

toggle_m130:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #8]
    cmp     r0, #0
    bne     m130_disable
    PTR     r1, target_m130
    mov     r0, #4                 @ stock line period
    bl      m130_apply
    cmp     r0, #0
    beq     m130_out
    mov     r0, #1
    str     r0, [r6, #8]
    bl      apply_shared
    mov     r0, #1
m130_out:
    pop     {r4, r5, r6, pc}
m130_disable:
    mov     r0, #0
    str     r0, [r6, #8]
    bl      m130_revert
    bl      apply_shared
    mov     r0, #1
    pop     {r4, r5, r6, pc}

/* M130 FAST: the same, with the line period cut 445 -> 330 -- which is what
 * rolling shutter is made of: 16.32 ms -> 12.10 ms at 2640 lines, better than
 * stock 4K's 13.44. The frame length comes from the table's second column so
 * the rate is unchanged and nothing has to be divided at runtime.
 * EXPERIMENTAL: every 12-bit mode in the image uses 445 whatever its width,
 * and 330 appears only on binned modes up to 3032 wide, so 330 at 3968 wide
 * 1:1 is unobserved. If the sensor cannot read a line that fast the frames
 * tear; it is RAM only, so toggling off or a power cycle undoes it. */
toggle_m130f:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #40]
    cmp     r0, #0
    bne     m130f_disable
    PTR     r1, target_m130
    mov     r0, #8                 @ the shorter line period's column
    bl      m130_apply
    cmp     r0, #0
    beq     m130f_out
    LDA     r4, M130_HMAX
    LDA     r5, 0x014A014A
    str     r5, [r4]
    mov     r0, #1
    str     r0, [r6, #40]
    bl      apply_shared
    mov     r0, #1
m130f_out:
    pop     {r4, r5, r6, pc}
m130f_disable:
    mov     r0, #0
    str     r0, [r6, #40]
    LDA     r4, M130_HMAX
    LDA     r5, 0x01BD01BD
    str     r5, [r4]
    bl      m130_revert
    bl      apply_shared
    mov     r0, #1
    pop     {r4, r5, r6, pc}

/* M98 60P: 3032x2012 in the FHD 59.94 cell -- open-gate geometry at double the
 * rate. Timing entry 2094 -> 2698 is 59.94 fps at M98's hmax, about 548 MB/s:
 * above any card and near the SSD ceiling, so expect short bursts. */
toggle_m98b:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #28]
    eor     r0, r0, #1
    str     r0, [r6, #28]
    cmp     r0, #0
    beq     m98b_off
    mov     r0, #0x1b
    mov     r1, #0x62
    bl      repoint
    VMAX    M98_VMAX, 0x00040A8A
    movw    r0, #:lower16:SEL_5994
    movw    r1, #0x400
    bl      set_zoom
    b       m98b_done
m98b_off:
    mov     r0, #0x62
    mov     r1, #0x1b
    bl      repoint
    VMAX    M98_VMAX, 0x0004082E
    movw    r0, #:lower16:SEL_5994
    movw    r1, #0x640
    bl      set_zoom
m98b_done:
    bl      apply_shared
    pop     {r4, r5, r6, pc}

/* M6 4K: M102 -> M6 in the 4K cell. Same 4176x2174 raster and same 29.97, so
 * no geometry hook and no timing rewrite -- a picker-only swap to a 14-bit
 * FULL readout mode. Tested live on this camera 2026-09-11 and it looked
 * identical to stock 4K: the CinemaDNG file depth follows the menu's 8/10/12
 * setting, so M6's extra two bits are quantised away. Kept because it is three
 * words and the only path to 14-bit if that recorder limit is ever lifted. */
toggle_m6:
    push    {r4, r5, r6, lr}
    LDA     r6, ST
    ldr     r0, [r6, #36]
    eor     r0, r0, #1
    str     r0, [r6, #36]
    cmp     r0, #0
    movne   r0, #0x66
    movne   r1, #0x06
    moveq   r0, #0x06
    moveq   r1, #0x66
    bl      repoint
    pop     {r4, r5, r6, pc}

.balign 4
/* What the probed selector means, measured on hardware, one row per rate:
 *
 *   selector, frame length at line period 445, at 330, then the stock mode ids
 *   that rate can appear as.
 *
 * Keyed by selector because that is the only thing that identifies the live
 * preset: the picker cells of every framerate look alike, so matching mode ids
 * alone repointed all of them at once. Two ids per rate because which one the
 * camera writes into the live table is not fixed -- M109 and M113 are both
 * 23.976, and M113 appears nowhere in the image, so it can only get there at
 * preset selection. Both columns are tabulated so nothing is divided at
 * runtime: 72MHz/(445*fps) and 72MHz/(330*fps).
 *
 * An unmeasured selector is REFUSED, not guessed. 59.94 is deliberately absent:
 * M130 at that rate would be about 1.1 GB/s. */
/* One row per full-readout option: the mode id it installs, that mode's timing
 * entry, the frame length to put back, and the canvas it needs. The rate table
 * below serves all of them because every 12-bit mode has line period 445. */
target_m130:
    .word 130, M130_VMAX, 0x1014, 3968, 2640
zoom_bases:
    .word 0xC0BD9A34, 0xC0BD9EFC, 0xC0BE1684, 0xC0BE1B4C   @ the FHD 29.97 row
m130_rates:
    .word 175, 0x1517, 0x1c70, 0x6a, 0x6f     @ FHD 29.97,  M106 / M111
    .word 176, 0x1a5c, 0x238c, 0x6d, 0x71     @ FHD 23.976, M109 / M113
    .word 180, 0x1948, 0x2217, 0x7d, 0x7f     @ FHD 25,     M125 / M127
    .word 0, 0, 0, 0, 0
state_offsets:
    .word 0, 4, 24, 8, 40, 28, 36, 12, 0, 0
/* state offset, the resources it holds, and its toggle -- see release(). */
features_table:
    .word 4,  GRP_2997,            toggle_og - entry
    .word 24, GRP_2997|GRP_M98,    toggle_m98a - entry
    .word 8,  GRP_2997|GRP_M130,   toggle_m130 - entry
    .word 40, GRP_2997|GRP_M130,   toggle_m130f - entry
    .word 28, GRP_5994|GRP_M98,    toggle_m98b - entry
    .word 36, GRP_4K,              toggle_m6 - entry
.equ FEATURE_COUNT, (. - features_table) / 12
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
    .word 0xC0BE5858, 0x1B, 0xC0BE59F8, 0x1B, 0xC0BE5B98, 0x1B
    .word 0xC0BE58E8, 0x66, 0xC0BE5A88, 0x66, 0xC0BE5C28, 0x66
    .word 0xC0B59A28, 0x00040888
    .word M98_VMAX, 0x0004082E
    .word M130_VMAX, 0x04201014
    .word M130_HMAX, 0x01BD01BD
    .word 0xC0BE58B8, 0x6D, 0xC0BE5A58, 0x6D, 0xC0BE5BF8, 0x6D
    .word 0xC0BE58A8, 0xDA, 0xC0BE5A48, 0xDA, 0xC0BE5BE8, 0xDA
    .word 0xC0BD9A34, 0x640, 0xC0BE1684, 0x640
    .word 0xC0BD9EFC, 0x640, 0xC0BE1B4C, 0x640
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
    .asciz ">M98 30P    OFF        "
    .space 8
    .asciz ">M98 30P    ON         "
    .space 8
    .asciz ">M130       OFF        "
    .space 8
    .asciz ">M130       ON         "
    .space 8
    .asciz ">M130 FAST  OFF        "
    .space 8
    .asciz ">M130 FAST  ON         "
    .space 8
    .asciz ">M98 60P    OFF        "
    .space 8
    .asciz ">M98 60P    ON         "
    .space 8
    .asciz ">M6 4K      OFF        "
    .space 8
    .asciz ">M6 4K      ON         "
    .space 8
    .asciz ">GYRO       OFF        "
    .space 8
    .asciz ">GYRO       ON         "
    .space 8
    .asciz ">GYRO-GATE  OFF        "
    .space 8
    .asciz ">GYRO-GATE  ON         "
    .space 8
    .asciz ">SEL        OFF        "
    .space 8
    .asciz ">SEL        ON         "
    .space 8
busy_label: .asciz "STOP RECORDING / WAIT   "
unavailable_label: .asciz "GYRO INIT FAILED       "
need_sel_label: .asciz "SEL=00 RATE UNKNOWN    "
sel_label:      .asciz "SEL=00 C=00 L=00       "
.balign 4
