.syntax unified
.thumb
.text
/* Observation-only Thumb hook on the GUI element append, FUN_c05e2ca8.
 *
 * Answers one question and changes nothing: does an AutoRun run BEFORE the
 * startup resource pass that builds the value lists? ijigen's measurements
 * (research/ui, and docs/menu/gui-resources.md here) show those arrays are
 * built once at boot and never rebuilt by any screen transition, so a hook that
 * wants to add a resolution entry must already be armed when that pass runs.
 * Nobody has established whether AutoRun is early enough. We boot our own code
 * from AutoRun, so we can find out.
 *
 * A hit means the route to a native entry is open. Zero hits rules it out and
 * sends the search to an earlier mount point. Either way it is a real answer.
 *
 * The GUI region is Thumb-2, so none of this project's ARM hook machinery
 * applies. Entered with B.W (T4) rather than BL so lr still holds the original
 * caller's return address and the displaced instructions run unchanged.
 *
 *   r0 = container, r1 = element. element+0x18 is its name string.
 *
 * State block:
 *   +0x00 appends seen
 *   +0x04 .. +0x20 the first eight element name pointers
 */
.equ STATE,  PROBE_STATE
.equ RESUME, PROBE_RESUME      /* hook site + 4, with the Thumb bit set */

.global probe
probe:
    push    {r0, r1, r2, r3}
    movw    r2, #:lower16:STATE
    movt    r2, #:upper16:STATE
    ldr     r3, [r2]
    adds    r3, r3, #1
    str     r3, [r2]
    cmp     r3, #8                  @ keep the first eight names, then just count
    bhi     1f
    ldr     r0, [r1, #0x18]
    add.w   r2, r2, r3, lsl #2
    str     r0, [r2]
1:  pop     {r0, r1, r2, r3}
    push    {r4, r5, lr}            @ the two displaced instructions, unchanged
    mov     r5, r0
    movw    r3, #:lower16:RESUME    @ r3 is scratch: the hooked function takes
    movt    r3, #:upper16:RESUME    @ two arguments and never reads it
    bx      r3
