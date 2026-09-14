.syntax unified
.arm
.text
/* Double-press RIGHT to open the card's menu; everything else stays the
 * camera's.
 *
 * The card's key handler takes RIGHT and UP the moment it is loaded, which
 * collides with zoom and moving the magnified frame. This makes the menu modal:
 *
 *     closed   RIGHT and UP go to the camera. A single RIGHT still pans.
 *     RIGHT x2 in quick succession opens the menu
 *     open     RIGHT cycles, UP toggles, DOWN latches false colour
 *     MENU/AEL closes it again
 *
 * WHERE THIS RUNS MATTERS. An earlier attempt published its own function in the
 * observer vtable at 0xC091EA38 and pressing OK froze the camera solid, three
 * times: record light on, no UI, no buttons, no USB. That slot belongs to a C++
 * observer whose contract we do not have. So nothing here touches it. The card
 * keeps the slot, and its first instruction is replaced with a branch to this
 * routine, which decides and then either continues into the card's own handler
 * or tail-calls the camera's. Same context, same registers, no new owner.
 *
 * r0..r3 are the handler's arguments and are passed on untouched. The only
 * register written before a tail call is ip, which the card's handler already
 * uses as scratch for exactly this.
 *
 * The double-press window is counted by the drawing thread rather than by a
 * clock: it clears the pending flag after a couple of passes, which is roughly
 * half a second.
 *
 *   +0x00 open        +0x04 a RIGHT is pending   +0x08 passes since it was
 *   +0x0C opens seen  +0x10 keys seen
 *   +0x18 where to leave a camera request   +0x1C start fn   +0x20 stop fn
 *   +0x28 the panel renderer, called on every key
 *   +0x24 false colour is on
 */
.equ STATE,      STATE_ADDR
.equ CARD_HANDLE, CARD_HANDLE_ADDR   /* the card's own `handle`, past its tests */
.equ STOCK,      0xC0265800
.equ K_RIGHT,    0x0C
.equ K_UP,       0x14
.equ K_DOWN,     0x18
.equ K_MENU,     0x0A
.equ K_AEL,      0x24

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global keygate
keygate:
    push    {r4, r5, r6, lr}
    LDA     r4, STATE
    ldr     ip, [r4, #0x10]
    add     ip, ip, #1
    str     ip, [r4, #0x10]
    /* Repaint from here rather than from a thread. The panel only needs
     * redrawing when something changes, and a keypress is the only thing that
     * changes it. Every freeze in this project followed creating a pool thread,
     * so there is no longer one. The renderer is our own code: memory writes
     * and a cache maintenance call, with the arguments saved across it. */
    ldr     ip, [r4, #0x28]
    cmp     ip, #0
    beq     1f
    push    {r0, r1, r2, r3}
    blx     ip
    pop     {r0, r1, r2, r3}
1:
    ldr     ip, [r4]
    cmp     ip, #0
    bne     is_open

    /* Closed: only a second RIGHT means anything to us. */
    cmp     r1, #K_RIGHT
    bne     to_stock
    ldr     ip, [r4, #4]
    cmp     ip, #0
    bne     open_it
    mov     ip, #1                  @ first press: arm, and let it pan as usual
    str     ip, [r4, #4]
    mov     ip, #0
    str     ip, [r4, #8]
    b       to_stock

open_it:
    mov     ip, #0
    str     ip, [r4, #4]
    mov     ip, #1
    str     ip, [r4]
    ldr     ip, [r4, #0x0C]
    add     ip, ip, #1
    str     ip, [r4, #0x0C]
    b       swallow

is_open:
    cmp     r1, #K_RIGHT
    beq     to_card
    cmp     r1, #K_UP
    beq     to_card
    cmp     r1, #K_DOWN
    beq     false_colour
    cmp     r1, #(K_DOWN + 1)
    beq     swallow
    cmp     r1, #K_MENU             @ a way out that is not a timeout
    beq     close_menu
    cmp     r1, #K_AEL
    beq     close_menu
    cmp     r1, #(K_RIGHT + 1)      @ their releases are ours too
    beq     swallow
    cmp     r1, #(K_UP + 1)
    beq     swallow
    b       to_stock

/* DOWN, while the menu is open, latches false colour. The camera only offers it
 * as hold-a-button, because the function key posts rec-manager event 0x21 on
 * press and 0x22 on release. Nothing is stored anywhere, which is why it never
 * showed up in a diff of the settings store. All this does is leave the request
 * for the drawing thread to make: posting it from here would mean calling
 * firmware from the key path, and that is what froze the camera before. */
false_colour:
    ldr     ip, [r4, #0x18]         @ where the thread looks for a request
    cmp     ip, #0
    beq     to_stock
    ldr     r5, [r4, #0x24]
    eor     r5, r5, #1
    str     r5, [r4, #0x24]
    cmp     r5, #0
    ldrne   r6, [r4, #0x1C]         @ start
    ldreq   r6, [r4, #0x20]         @ stop
    str     r6, [ip]
    b       swallow

close_menu:
    mov     r5, #0
    str     r5, [r4]
    b       swallow

to_card:
    pop     {r4, r5, r6, lr}
    LDA     ip, CARD_HANDLE
    bx      ip

to_stock:
    pop     {r4, r5, r6, lr}
    LDA     ip, STOCK
    bx      ip

swallow:
    pop     {r4, r5, r6, lr}
    mov     r0, #1
    bx      lr
