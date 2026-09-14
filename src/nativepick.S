.syntax unified
.arm
.text
/* Make the third entry in the camera's own Resolution list do something.
 *
 * The list gets its extra row from two writes (see docs/menu/native-tables.md).
 * This is the other half: the selection callback at 0xC057A6A0 reads the chosen
 * index from [r0+8], maps it through the pair table at 0xC2E4020C, and
 * dispatches the resulting enum. The pairs are:
 *
 *     index 0 -> enum 3   UHD
 *     index 1 -> enum 2   FHD
 *     index 2 -> enum 4   nothing valid; reaches an "Invalid MovSize" path
 *
 * So index 2 is intercepted here. Open gate repoints the FHD 29.97 cell, which
 * means the correct behaviour for "OG 3032x2012" is: switch open gate on and let
 * the camera dispatch FHD. The index is rewritten to 1 before the stock code
 * reads it, so no invalid enum is ever dispatched.
 *
 * Picking UHD or FHD switches open gate back off, so the list always tells the
 * truth about what the camera will record.
 *
 * The work happens in menu.S (`native_select_og` / `native_select_stock`), which
 * already refuses while recording and handles the conflict release. This stub
 * only decides which one to call. The menu lives in the pool, whose base is only
 * known at runtime, so it is read from POOL_PTR rather than baked in.
 */
.equ POOL_PTR,  0xC3757A7C
.equ MENU_OFF,  0x50000
.equ SITE,      0xC057A6A0
.equ RESUME,    SITE + 4

.macro LDA reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.global pick
pick:
    push    {r0, r1, r2, r3, ip, lr}
    ldr     r2, [r0, #8]           @ the index the user just chose
    cmp     r2, #2
    bne     1f
    mov     r2, #1                 @ dispatch FHD, never enum 4
    str     r2, [r0, #8]
    LDA     r3, POOL_PTR
    ldr     r3, [r3]
    cmp     r3, #0
    beq     2f
    movw    ip, #:lower16:(MENU_OFF + SEL_OG_OFF)
    movt    ip, #:upper16:(MENU_OFF + SEL_OG_OFF)
    add     ip, ip, r3
    blx     ip
    b       2f
1:  LDA     r3, POOL_PTR
    ldr     r3, [r3]
    cmp     r3, #0
    beq     2f
    movw    ip, #:lower16:(MENU_OFF + SEL_STOCK_OFF)
    movt    ip, #:upper16:(MENU_OFF + SEL_STOCK_OFF)
    add     ip, ip, r3
    blx     ip
2:  pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, r7, lr}   @ the displaced instruction, unchanged
    LDA     ip, RESUME
    bx      ip
