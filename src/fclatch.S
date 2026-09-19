.syntax unified
.arm
.text
/* Turn the camera's own False Color / EL Zone button into a toggle.
 *
 * Stock behaviour: an assigned function key calls CameraIF vtable +0xCC on
 * press, which posts rec-manager event 0x21, and +0xD0 on release, which posts
 * 0x22. Nothing is stored, so the mode lasts exactly as long as the button is
 * held.
 *
 * Nothing in the image branches to either method directly -- every call arrives
 * through the vtable at 0xC0B9928C -- so replacing the two method bodies
 * catches every caller, whichever button the user has mapped and whatever
 * dispatches it. The camera's own Custom Button Functions mapping is left
 * alone; this only changes what press and release do.
 *
 *   press   (0xC03722E8): flip our flag, post 0x21 or 0x22 to match
 *   release (0xC0372330): return without posting, so letting go does not cancel
 *
 * Both methods are ten instructions and identical apart from the event number,
 * so the posting is reimplemented here rather than calling back into a patched
 * method: a zeroed 0xBC-byte request, event id at +0x00, 1 at +0x04, posted to
 * [object+4] through 0xC03A0798.
 *
 * Entered with B (not BL) at each site, so lr still belongs to the method's
 * caller and returning is a normal epilogue.
 *
 * State block, at STATE:
 *   +0x00 our latch: 1 = we asked for it on
 *   +0x04 presses seen
 *   +0x08 releases swallowed
 */
.equ MEMSET, 0xC0015058
.equ POST,   0xC03A0798

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

/* Replaces 0xC03722E8. r0 = the CameraIF object. */
.global fc_press
fc_press:
    push    {r4, r5, lr}
    mov     r4, r0
    LDA     r5, STATE
    ldr     r1, [r5, #4]
    add     r1, r1, #1
    str     r1, [r5, #4]
    ldr     r0, [r5]
    eor     r0, r0, #1                 @ our latch, since the camera keeps none
    str     r0, [r5]
    cmp     r0, #0
    mov     r1, #0x22                  @ off
    movne   r1, #0x21                  @ on
    mov     r0, r4
    bl      fc_post
    pop     {r4, r5, pc}

/* Replaces 0xC0372330: the release. Swallowed, so the mode stays up.
 *
 * The stock method returns whatever the post returned, and its callers use that
 * as a success flag, so this returns 1 rather than 0.
 */
.global fc_release
fc_release:
    LDA     r1, STATE
    ldr     r0, [r1, #8]
    add     r0, r0, #1
    str     r0, [r1, #8]
    mov     r0, #1
    bx      lr

/* r0 = the CameraIF object, r1 = event id. The stock body, reproduced. */
fc_post:
    push    {r4, r5, lr}
    mov     r4, r0
    mov     r5, r1
    sub     sp, sp, #0xBC              @ the request, zeroed
    mov     r0, sp
    mov     r1, #0
    mov     r2, #0xBC
    LDA     r3, MEMSET
    blx     r3
    str     r5, [sp]                   @ event id
    mov     r1, #1
    strb    r1, [sp, #4]
    ldr     r0, [r4, #4]               @ the rec-manager target
    mov     r1, sp
    LDA     r3, POST
    blx     r3
    add     sp, sp, #0xBC
    pop     {r4, r5, pc}
