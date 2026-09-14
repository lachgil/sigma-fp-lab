.syntax unified
.arm
.text
/* Find out what the camera's buttons are actually called.
 *
 * The card's menu takes RIGHT and UP for itself, which is fine until you want
 * those buttons for what the camera uses them for -- zoom, and moving the
 * magnified frame around. Fixing that means opening our menu with something
 * else, and choosing that something needs the key codes, which are not in the
 * firmware's strings.
 *
 * So this sits in front of whatever handler is published at 0xC091EA38, writes
 * down every (control, key) pair it sees, and passes the call straight on. It
 * decides nothing and swallows nothing: with this installed the camera behaves
 * exactly as it did, including the card menu.
 *
 * The saved handler is read from the state block rather than assembled in, so
 * the deployer can put back whatever was there before -- which is the only way
 * out of this, since a wrong handler here means no buttons at all.
 *
 *   +0x00 events seen        +0x04 the handler we chain to
 *   +0x08 .. +0x107 a ring of 32 (r0, r1) pairs, oldest overwritten
 */
.equ STATE,     STATE_ADDR
.equ RING,      32

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global keylog
keylog:
    push    {r0, r1, r2, r3, r12, lr}
    LDA     r2, STATE
    ldr     r3, [r2]
    and     r12, r3, #(RING - 1)
    add     r12, r2, r12, lsl #3
    str     r0, [r12, #8]
    str     r1, [r12, #12]
    add     r3, r3, #1
    str     r3, [r2]
    ldr     r12, [r2, #4]           @ the handler that was published before us
    pop     {r0, r1, r2, r3}
    add     sp, sp, #4              @ the saved r12: we are using it
    pop     {lr}
    cmp     r12, #0
    bxne    r12
    bx      lr                      @ nothing to chain to: behave as a no-op
