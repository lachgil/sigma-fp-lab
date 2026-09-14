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
 *     open     RIGHT cycles, UP toggles
 *     idle     it closes itself after IDLE_MS with nothing pressed
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
 *   +0x2C the card's cursor word    +0x30 the row index that is ours
 *   +0x34 when it was last touched  +0x38 when RIGHT was first pressed
 *   +0x24 false colour is on
 */
.equ STATE,      STATE_ADDR
.equ CARD_HANDLE, CARD_HANDLE_ADDR   /* the card's own `handle`, past its tests */
.equ STOCK,      0xC0265800
.equ CLOCK,      0xC002B920     /* milliseconds; the camera's own logger uses it */
.equ WINDOW_MS,  400            /* how quickly the second RIGHT has to arrive */
.equ IDLE_MS,    5000           /* how long the panel stays up untouched */
.equ K_RIGHT,    0x0C
.equ K_UP,       0x14
.equ K_DOWN,     0x18
.equ K_REPEAT,   0x0F


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

    /* Closed: only a second RIGHT in quick succession means anything to us.
     * The window is real milliseconds, read from the clock the camera's own
     * logger uses. Counting key events instead does not work: a press and its
     * release are two events, so any two RIGHT presses looked like a double
     * press no matter how far apart, which would have opened the panel while
     * panning a zoom. */
    cmp     r1, #K_RIGHT
    bne     not_right
    push    {r0, r1, r2, r3}        @ the clock answers in r0, which is an
    LDA     ip, CLOCK               @ argument we must hand on untouched
    blx     ip
    mov     r6, r0
    pop     {r0, r1, r2, r3}
    ldr     r5, [r4, #0x38]         @ when the first press was
    cmp     r5, #0
    beq     arm_press
    sub     r5, r6, r5
    cmp     r5, #WINDOW_MS
    bhi     arm_press
    mov     r5, #0
    str     r5, [r4, #0x38]
    mov     r5, #1
    str     r5, [r4]                @ open, and remember when
    str     r6, [r4, #0x34]
    ldr     r5, [r4, #0x0C]
    add     r5, r5, #1
    str     r5, [r4, #0x0C]
    b       swallow
arm_press:
    str     r6, [r4, #0x38]
    b       to_stock

not_right:
    tst     r1, #1                  @ releases and repeats leave the window alone
    bne     to_stock
    cmp     r1, #K_REPEAT
    beq     to_stock
    mov     r5, #0                  @ any other press cancels it
    str     r5, [r4, #0x38]
    b       to_stock

is_open:
    push    {r0, r1, r2, r3}        @ same here: save the arguments across it
    LDA     ip, CLOCK               @ has it been left alone long enough to close
    blx     ip
    mov     r6, r0
    pop     {r0, r1, r2, r3}
    ldr     r5, [r4, #0x34]
    sub     r5, r6, r5
    LDA     ip, IDLE_MS             @ ip, not r6: r6 still holds the timestamp
    cmp     r5, ip
    bhi     idle_out
    str     r6, [r4, #0x34]
    cmp     r1, #K_RIGHT
    beq     to_card
    cmp     r1, #K_UP
    beq     up_pressed
    cmp     r1, #(K_RIGHT + 1)      @ their releases are ours too
    beq     swallow
    cmp     r1, #(K_UP + 1)
    beq     swallow
    b       to_stock

/* UP on the last row is ours: the card's cursor has one more position than it
 * has options, and that is where the panel draws false colour. Every other row
 * toggles the card's own way, so this one should too -- no special key. */
up_pressed:
    ldr     ip, [r4, #0x2C]         @ the card's cursor word
    cmp     ip, #0
    beq     to_card
    ldr     ip, [ip]
    ldr     r5, [r4, #0x30]         @ which row is ours
    cmp     ip, r5
    bne     to_card

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

idle_out:
    mov     r5, #0                  @ the panel timed out; this key is the
    str     r5, [r4]                @ camera's again, not a closing press
    b       to_stock

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
