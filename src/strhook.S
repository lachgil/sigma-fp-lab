.syntax unified
.thumb
.text
/* Private names for NBU records, by answering the string resolver ourselves.
 *
 * Every string in a scene record is a byte offset into the NBU pool, resolved
 * by the leaf function at 0xC05E5B58:
 *
 *     r1 == -1            -> 0
 *     r1 >= [r0+0x10]     -> 0          (the pool's length, 0x2B018)
 *     else                -> [r0+0x14] + r1
 *
 * So an offset past the pool length already returns zero: the range above it is
 * free, and nothing stock can reach into it. This answers that range out of our
 * own blob and does the stock arithmetic itself for everything else. The
 * function is a leaf -- no stack, no calls -- so there is no displaced prologue
 * to run and no resume: the whole of it is reimplemented here, and the four
 * bytes at the site become a B.W to this.
 *
 * That matters because it is the only way to NAME something of our own. Every
 * four-digit localization key in the pool is referenced by at least one record,
 * so there is no spare key to borrow, and overwriting a stock English string
 * (which an earlier build of ours did) renames it everywhere it is used.
 *
 * The mechanism is FP3K's; this is our own implementation of it.
 *
 * A private offset is PRIVATE_BASE + (byte offset into str_blob). The strings
 * here are localization KEYS as far as the text system is concerned, and no
 * `.nloc` holds them, so the text system falls back to drawing them literally.
 * That fallback is what puts a label on screen without any artwork.
 *
 * Entered with B.W (T4), so lr still belongs to the resolver's caller.
 */
.equ SITE,         0xC05E5B58
.equ POOL_LENGTH,  0x2B018            /* checked against the image at build time */

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

.global str_resolve
.thumb_func
str_resolve:
    LDA     r2, PRIVATE_BASE
    cmp     r1, r2
    blo     str_stock
    subs    r1, r1, r2
    cmp     r1, #BLOB_LENGTH
    bhs     str_none                  @ past our blob: zero, like the stock tail
    LDA     r2, STR_BLOB
    adds    r0, r1, r2
    bx      lr

str_stock:
    adds    r2, r1, #1                @ 0xFFFFFFFF + 1 == 0: the -1 case
    beq     str_none
    ldr     r2, [r0, #0x10]           @ pool length, from the reader itself
    cmp     r1, r2
    bhs     str_none
    ldr     r2, [r0, #0x14]           @ pool base, likewise
    adds    r0, r1, r2
    bx      lr

str_none:
    movs    r0, #0
    bx      lr

/* The private pool. Offsets are relative to PRIVATE_BASE, so `FP LAB` is
 * PRIVATE_BASE + 0 and anything added below keeps its own offset. */
.global str_blob
str_blob:
    .asciz "FP LAB"
str_blob_end:
.equ BLOB_LENGTH, str_blob_end - str_blob
