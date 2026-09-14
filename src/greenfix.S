.syntax unified
.arm
.text
/* The standby/live-view green fix, as a menu option instead of its own card.
 *
 * A 3:2 sensor frame greens the preview because the display accessor keeps
 * handing out the stock 16:9 preview geometry, so the YUV resize writes a
 * region that does not match the buffer and the untouched Y reads as green.
 *
 * The fix is FP3K's, and this is a port of it, not a new idea: hook the indexed
 * display accessor 0xC0437E98 and, for index 0 on the two live-view containers,
 * return a 3:2 (1620x1080) descriptor of our own instead of the firmware's
 * object. A RAM poke cannot do this -- the geometry is rebuilt every frame.
 * Analysis, and how the fingerprints were recovered, in docs/display/green.md.
 *
 * Two things about it are honest and important:
 *   * It is the STANDBY path. The monitor while recording uses 0xC04376E0 with
 *     a 16:9 buffer allocated at record start, and this does not touch it.
 *   * It engages only if the container's head object still fingerprints as
 *     expected. On a unit where it does not, this is a no-op, not a corruption.
 *
 * 1620x1080 is 3:2, so it suits the 3:2 modes -- open gate, M98 and M130. It is
 * wrong for M6 4K (1.92:1), which is why it is a switch you throw rather than
 * something armed behind your back.
 *
 * Entered with B (not BL) from the hook site, so lr still belongs to the
 * accessor's caller and returning a descriptor is a plain `bx lr`.
 */
.equ SITE,     0xC0437E98
.equ RESUME,   SITE + 4
.equ LIVE_A,   0xC375EB68
.equ LIVE_B,   0xC375ED3C
.equ FNV_A,    0xAED11366          /* FNV-1a of LIVE_A's head, 44 bytes */
.equ FNV_B,    0xE0255ED6          /* and of LIVE_B's */
.equ HEAD,     44

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

.global green_desc
green_desc:
    /* size_h, size_v, then the duplicate pair and the tail FP3K forces. */
    .word 1620, 1080, 0, 0
    .word 1620, 1080, 0, 4
    .word 0x003FC000, 1, 1

.global green_fix
green_fix:
    cmp     r1, #0                  @ an indexed accessor; only index 0 is ours
    bne     green_stock
    LDA     r2, GREEN_STATE
    ldr     r3, [r2]
    cmp     r3, #0
    beq     green_stock
    LDA     r2, LIVE_A
    cmp     r0, r2
    LDA     r3, FNV_A
    beq     green_hash
    LDA     r2, LIVE_B
    cmp     r0, r2
    bne     green_stock
    LDA     r3, FNV_B
green_hash:
    ldr     r2, [r0]                @ the head object the fingerprint covers
    push    {r4, r5}                @ two registers: the stack stays 8-aligned
    LDA     r4, 0x811C9DC5
    LDA     r5, 0x01000193
    mov     ip, #HEAD
1:  ldrb    r1, [r2], #1
    eor     r4, r4, r1
    mul     r4, r5, r4
    subs    ip, ip, #1
    bne     1b
    cmp     r4, r3
    pop     {r4, r5}
    bne     green_restore_index
    LDA     r0, GREEN_DESC
    bx      lr
green_restore_index:
    mov     r1, #0                  @ the hash loop borrowed r1; put the index back
green_stock:
    push    {r4, r5, lr}            @ the displaced instruction, unchanged
    LDA     ip, RESUME
    bx      ip
