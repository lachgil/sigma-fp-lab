.syntax unified
.thumb
.text
/* Feed extra NBU scene records to SIGMA's own interpreter, at 0xC05E6400.
 *
 * A scene is an allocation header followed by byte-packed records with no
 * slack, so nothing can be inserted in place. The interpreter, though, is
 * called once per record with a reader whose stream base (+0x24) and position
 * (+0x04) are both writable, so a record can be answered out of our own buffer.
 * That much is Vitaly Li's FP3K technique; this extends it from replacing one
 * record to also injecting a run of new ones at one position.
 *
 * The builder emits the table. For the FP LAB row it holds three entries:
 *   REPLACE  the scene's allocation header, enlarged for the new objects
 *   REPLACE  the Menu declaration, with one more child slot
 *   INJECT   the new row's records, immediately before the scene terminator
 *
 * Each entry carries the stock record's length and FNV-1a fingerprint. A record
 * that does not match is left alone and counted, so a different firmware, or a
 * record something else already changed, gets stock behaviour rather than a
 * blind rewrite.
 *
 * The reader's string pool (+0x14) is never touched: copied records name the
 * same pool offsets as the stock records they came from.
 *
 * Entered with B.W (T4), so lr still belongs to the interpreter's caller.
 *
 * Table entry, 24 bytes:
 *   +0x00 stock record address      +0x0C mode: bit 0 = emit a run of records
 *   +0x04 stock record length             rather than replace one,
 *   +0x08 stock FNV-1a                    bit 1 = the buffer is a
 *                                         pool-relative offset
 *                                  +0x10 buffer address or pool offset
 *                                  +0x14 buffer length
 *
 * State block:
 *   +0x00 records seen on our stream   +0x08 records injected
 *   +0x04 substitutions applied        +0x0C fingerprint refusals
 */
.equ STATE,  INJECT_STATE
.equ TABLE,  INJECT_TABLE
.equ COUNT,  INJECT_COUNT
.equ NBU,    0xC18C0460            /* the stock NBU stream base */
.equ RESUME, 0xC05E6405            /* hook site + 4, Thumb */
.equ POOL_PTR, 0xC3757A7C          /* the loader's DMA pool base, at runtime */

.macro addr reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global inject
.thumb_func
inject:
    bx      pc                      @ the site is Thumb; the work is ARM
    nop
.arm
    push    {r4-r11, lr}
    sub     sp, sp, #4              @ nine registers pushed: realign to 8
    mov     r4, r0                  @ reader
    ldr     r5, [r4, #36]           @ stream base
    addr    r6, NBU
    cmp     r5, r6
    bne     inject_stock
    ldr     r7, [r4, #4]            @ position within the stream
    add     r8, r5, r7              @ absolute address of this record
    addr    r0, STATE
    ldr     r1, [r0]
    add     r1, r1, #1
    str     r1, [r0]

    addr    r9, TABLE
    mov     r10, #0
inject_find:
    cmp     r10, #COUNT
    bhs     inject_stock
    ldr     r0, [r9]
    cmp     r0, r8
    beq     inject_found
    add     r9, r9, #24
    add     r10, r10, #1
    b       inject_find

inject_found:
    ldr     r11, [r9, #4]           @ stock length
    mov     r0, r8
    mov     r1, r11
    bl      inject_hash
    ldr     r1, [r9, #8]
    cmp     r0, r1
    beq     inject_guarded
    addr    r0, STATE
    ldr     r1, [r0, #12]
    add     r1, r1, #1
    str     r1, [r0, #12]
    b       inject_stock

inject_guarded:
    /* Mode is two flags: bit 0 injects a run rather than replacing one record,
     * bit 1 means the buffer address is an OFFSET into the loader's DMA pool
     * rather than an absolute address. The row's records and the enlarged
     * allocation header are kilobytes, and no cave in the image is that big and
     * free, so they ride in the pool the way the menu's own code does -- and the
     * pool's base is only known at runtime. */
    ldr     r6, [r9, #12]           @ mode
    ldr     r10, [r9, #16]          @ buffer
    tst     r6, #2
    beq     1f
    addr    r0, POOL_PTR
    ldr     r0, [r0]
    cmp     r0, #0
    beq     inject_stock            @ no pool: leave the scene stock
    add     r10, r10, r0
1:  tst     r6, #1
    bne     inject_run

    /* One record answered from the buffer. The interpreter advances the
     * position by whatever it consumed, so put the stock length back. */
    sub     r0, r10, r7
    str     r0, [r4, #36]
    mov     r0, r4
    bl      inject_original
    str     r0, [sp]               @ preserve the native parser status
    str     r5, [r4, #36]
    add     r0, r7, r11
    str     r0, [r4, #4]
    addr    r0, STATE
    ldr     r1, [r0, #4]
    add     r1, r1, #1
    str     r1, [r0, #4]
    ldr     r0, [sp]
    b       inject_return

inject_run:
    /* A run of new records, each read at this same stream position, so the
     * stock record that follows still begins exactly where it did. */
    ldr     r6, [r9, #20]
    add     r6, r10, r6             @ end of the run
inject_next:
    cmp     r10, r6
    bhs     inject_run_done
    sub     r0, r10, r7
    str     r0, [r4, #36]
    mov     r0, r4
    bl      inject_original
    cmp     r0, #0
    bne     inject_run_failed       @ do not parse more after a native error
    str     r7, [r4, #4]
    ldr     r0, [r10, #4]           @ this record's big-endian length
    rev     r0, r0
    add     r10, r10, r0
    addr    r0, STATE
    ldr     r1, [r0, #8]
    add     r1, r1, #1
    str     r1, [r0, #8]
    b       inject_next
inject_run_done:
    str     r5, [r4, #36]           @ restore stock stream at the anchor
    str     r7, [r4, #4]
    b       inject_stock

inject_run_failed:
    str     r5, [r4, #36]
    str     r7, [r4, #4]
    b       inject_return           @ r0 still holds the native failure

inject_stock:
    mov     r0, r4
    bl      inject_original
inject_return:
    add     sp, sp, #4
    pop     {r4-r11, pc}

/* FNV-1a over r0[0..r1); returns the hash in r0. */
inject_hash:
    push    {r4, lr}
    addr    r2, 0x811C9DC5
    addr    r3, 0x01000193
1:  ldrb    r4, [r0], #1
    eor     r2, r2, r4
    mul     r2, r3, r2
    subs    r1, r1, #1
    bne     1b
    mov     r0, r2
    pop     {r4, pc}

/* Reaching the displaced prologue. Kept in ARM so `bl` from the body needs no
 * interworking relocation: this assembler applies R_ARM_CALL only, and a Thumb
 * target leaves it with an odd displacement. `push {r4-r11, lr}` stores exactly
 * what the site's `push.w` stores, and the interpreter's own epilogue pops that
 * lr into pc, returning to ARM because bit 0 is clear.
 */
inject_original:
    push    {r4-r11, lr}
    addr    ip, RESUME
    bx      ip
