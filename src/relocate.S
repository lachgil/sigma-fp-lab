.syntax unified
.arm
.text
/* Copy a block of camera memory to another, on the camera.
 *
 * The menu's scene resources are 127 KB. Sending that over the shell four bytes
 * at a time is half an hour; both ends are already in the camera's own memory,
 * so the copy belongs there. This is the whole of it: a checked, bounded
 * memcpy driven from a parameter block, run once through a borrowed command
 * handler.
 *
 * It refuses rather than trusts: source and destination must both be inside
 * mapped camera RAM, the length is capped, and nothing overlaps.
 *
 *   +0x00 status: 0 running, 1 copied, else a refusal code
 *   +0x04 source   +0x08 destination   +0x0C length   +0x10 bytes copied
 */
.equ STATE,   STATE_ADDR
.equ MAX_LEN, 0x40000

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global relocate
relocate:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r8, STATE
    mov     r0, #0
    str     r0, [r8]
    str     r0, [r8, #0x10]
    ldr     r4, [r8, #4]            @ source
    ldr     r5, [r8, #8]            @ destination
    ldr     r6, [r8, #0xC]          @ length

    cmp     r6, #0
    moveq   r0, #0x11
    beq     refuse
    LDA     r0, MAX_LEN
    cmp     r6, r0
    movhi   r0, #0x12
    bhi     refuse
    LDA     r0, 0x40000000          @ the destination must be our own RAM
    cmp     r5, r0
    movlo   r0, #0x13
    blo     refuse
    LDA     r0, 0x50000000
    add     r7, r5, r6
    cmp     r7, r0
    movhi   r0, #0x14
    bhi     refuse
    LDA     r0, 0xC0000000          @ the source must be camera memory
    cmp     r4, r0
    movlo   r0, #0x15
    blo     refuse
    add     r7, r4, r6
    LDA     r0, 0xC4000000
    cmp     r7, r0
    movhi   r0, #0x16
    bhi     refuse

1:  ldrb    r0, [r4], #1
    strb    r0, [r5], #1
    subs    r6, r6, #1
    bne     1b
    ldr     r0, [r8, #0xC]
    str     r0, [r8, #0x10]
    mov     r0, #1
    str     r0, [r8]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, pc}

refuse:
    str     r0, [r8]
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, pc}
