.syntax unified
.arm
.text
/* Stage-3 loader pipeline proof: entry runs once at boot (lr -> loader).
 * Write a marker to a free scratch word so the shell can confirm our BIN
 * loaded AND executed, then return so boot continues and the shell starts. */
.global entry
entry:
    push    {r4, lr}
    movw    r0, #0xFA00
    movt    r0, #0xC072          @ 0xC072FA00 free scratch (above worker code)
    movw    r1, #0xADED
    movt    r1, #0x510A          @ marker 0x510AADED
    str     r1, [r0]
    mov     r0, #0
    pop     {r4, pc}
