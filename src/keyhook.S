.syntax unified
.arm
.text
/* Give the camera its buttons back.
 *
 * The card's menu takes RIGHT and UP permanently, which collides with what the
 * camera uses them for -- zoom, and moving the magnified frame. This sits in
 * front of the published key handler and makes the menu MODAL instead:
 *
 *     closed   RIGHT and UP go to the camera, untouched
 *     AEL      opens the fpLAB menu, and closes it again
 *     open     RIGHT cycles and UP toggles, as before
 *     MENU     also closes it
 *
 * The codes were read off this camera with keylog.S, pressing one button at a
 * time. A press is an even code, its release is that code plus one, and a key
 * held down produces a stream of 0x0F:
 *
 *     MENU 0x0A   RIGHT 0x0C   LEFT 0x10   UP 0x14
 *     DOWN 0x18   OK   0x1C    QS    0x22  AEL 0x24
 *
 * AEL is ours while this is loaded: its press and release are swallowed, so
 * the camera does not see an exposure lock. That is the whole cost of getting
 * RIGHT and UP back, and unloading the payload gives AEL back.
 *
 * r0..r3 are the handler's arguments and are passed on UNTOUCHED, which is why
 * nothing below writes to them -- not even as scratch, not even for a counter.
 * Two versions got this wrong: one held the forwarding address in r2, the other
 * counted events in r0. The first handed the camera's own handler a corrupted
 * argument, and pressing OK put the camera into record and froze it solid:
 * record light on, no UI, no buttons, no USB. Scratch here is r4-r6 and ip.
 * tests/test_keyhook.py checks every argument survives every path.
 *
 * Toggling is not reimplemented here. When the menu is open, RIGHT and UP are
 * handed to the card's own handler, which owns the cursor, the conflict rules
 * and the recording guards. This decides who gets the keypress, nothing else.
 *
 *   +0x00 events seen      +0x04 the card's key handler   +0x08 the stock one
 *   +0x0C open             +0x1C times opened            +0x20 last code

 */
.equ STATE,      STATE_ADDR
.equ K_MENU,     0x0A
.equ K_RIGHT,    0x0C
.equ K_REPEAT,   0x0F
.equ K_UP,       0x14
.equ K_QS,       0x22
.equ K_AEL,      0x24

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global keyhook
keyhook:
    push    {r4, r5, r6, lr}
    LDA     r4, STATE
    ldr     r5, [r4]
    add     r5, r5, #1
    str     r5, [r4]
    str     r1, [r4, #0x20]

    ldr     r5, [r4, #0x0C]
    cmp     r5, #0
    bne     when_open

    /* Closed. AEL is the way in; everything else carries on as normal. */
    cmp     r1, #K_AEL
    beq     open_menu
    cmp     r1, #(K_AEL + 1)
    beq     swallow
    cmp     r1, #K_RIGHT            @ the two the card would otherwise eat
    beq     to_stock
    cmp     r1, #(K_RIGHT + 1)
    beq     to_stock
    cmp     r1, #K_UP
    beq     to_stock
    cmp     r1, #(K_UP + 1)
    beq     to_stock
    b       to_card                 @ anything else: the card still sees it

open_menu:
    mov     r5, #1
    str     r5, [r4, #0x0C]
    ldr     r5, [r4, #0x1C]
    add     r5, r5, #1
    str     r5, [r4, #0x1C]
    b       swallow

when_open:
    cmp     r1, #K_RIGHT
    beq     to_card
    cmp     r1, #K_UP
    beq     to_card
    cmp     r1, #K_MENU             @ either way out
    beq     close
    cmp     r1, #K_AEL
    beq     close
    cmp     r1, #(K_AEL + 1)
    beq     swallow
    cmp     r1, #(K_RIGHT + 1)      @ releases of the keys we just used
    beq     swallow
    cmp     r1, #(K_UP + 1)
    beq     swallow
    cmp     r1, #K_REPEAT
    beq     swallow
    b       to_stock                @ the rest of the camera still works

close:
    mov     r5, #0
    str     r5, [r4, #0x0C]
    b       swallow

to_card:
    ldr     ip, [r4, #4]
    cmp     ip, #0
    beq     to_stock
    pop     {r4, r5, r6, lr}
    bx      ip                      @ tail call: the card answers the caller

to_stock:
    ldr     ip, [r4, #8]
    pop     {r4, r5, r6, lr}
    bx      ip

swallow:
    mov     r0, #1                  @ handled; the camera hears nothing
    pop     {r4, r5, r6, pc}
