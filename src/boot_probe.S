.syntax unified
.arm
.text
.global boot_probe_entry
.global probe_init
.global probe_reader
.global probe_code_end
.global probe_state
.global probe_image_end

/* Isolated fp 5.02 diagnostic, NOT a loader or a registry module.
 * Entry runs in the upstream --boot-bin temporary staging allocation. Only
 * probe_image is retained, at C072EC60..C072EFB4 (exclusive). The isolated
 * card must have no other arena users. No CommonSave or retention-timer writes.
 *
 * State at C072ED60: init_visits, boot_sequence, reader_visits, last_sequence.
 * Sequences count observer initializations, not proven physical boots. All
 * counters wrap modulo 2^32. Reader instrumentation remains AFTER the native
 * readiness gate. Neither callback calls services or touches heap memory.
 *
 * Install accepts either two stock BLs + an entirely zero arena, or two exact
 * owned BLs + this exact immutable image (excluding the four marker words).
 * The latter performs NO writes: evidence from the native path survives the
 * subsequent AutoRun installer. Mixed/unknown states fail without mutation.
 * Validation through final cache publication excludes local IRQ/FIQ dispatch.
 * This is not a cross-core lock or protection against an arbitrary patch owner.
 * A removed nonzero image is intentionally not reinstalled without a cold boot.
 *
 * Removal accepts each site as stock or owned, verifies the image if either
 * is owned, then restores only owned sites. It NEVER clears the arena: the
 * remover can run inside an instrumented reader. Unknown sites/code fail
 * closed. Return r0: 0 installed/removed, 1 unchanged, -8 hook conflict,
 * -3 image conflict. Upstream stage2 does not display this return value.
 */
.equ CAVE_BEGIN, 0xC072EC60
.equ CAVE_END,   0xC072EFB4
.equ STATE,      0xC072ED60
.equ INIT_SITE,  0xC0020B78
.equ INIT_ORIG,  0xC03DA440
.equ READ_SITE,  0xC03DA420
.equ READ_ORIG,  0xC03DA758
.equ INIT_STOCK, 0xEB000000 | (((INIT_ORIG - INIT_SITE - 8) >> 2) & 0xFFFFFF)
.equ READ_STOCK, 0xEB000000 | (((READ_ORIG - READ_SITE - 8) >> 2) & 0xFFFFFF)
.equ INIT_OWNED, 0xEB000000 | (((CAVE_BEGIN + probe_init - probe_image - INIT_SITE - 8) >> 2) & 0xFFFFFF)
.equ READ_OWNED, 0xEB000000 | (((CAVE_BEGIN + probe_reader - probe_image - READ_SITE - 8) >> 2) & 0xFFFFFF)

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

boot_probe_entry:
    push    {r4-r9, ip, lr}
    mov     r8, sp
    bic     sp, sp, #7
    mrs     r9, cpsr
    cpsid   if
    LDA     r4, INIT_SITE
    LDA     r5, READ_SITE
    ldr     r6, [r4]
    ldr     r7, [r5]
#ifdef PROBE_REMOVE
    /* Validate both sites BEFORE restoring either. Already-stock sites need
     * no writes; a partial known removal can finish without trampling anyone. */
    LDA     r0, INIT_STOCK
    cmp     r6, r0
    LDA     r0, INIT_OWNED
    cmpne   r6, r0
    bne     hook_conflict
    LDA     r0, READ_STOCK
    cmp     r7, r0
    LDA     r0, READ_OWNED
    cmpne   r7, r0
    bne     hook_conflict
    LDA     r0, INIT_STOCK
    cmp     r6, r0
    LDA     r0, READ_STOCK
    cmpeq   r7, r0
    beq     unchanged
    bl      compare_image
    cmp     r0, #0
    bne     image_conflict
    LDA     r0, INIT_OWNED
    cmp     r6, r0
    LDA     r0, INIT_STOCK
    streq   r0, [r4]
    LDA     r0, READ_OWNED
    cmp     r7, r0
    LDA     r0, READ_STOCK
    streq   r0, [r5]
    bl      publish
#else
    LDA     r0, INIT_STOCK
    cmp     r6, r0
    LDA     r0, READ_STOCK
    cmpeq   r7, r0
    beq     first_install
    LDA     r0, INIT_OWNED
    cmp     r6, r0
    LDA     r0, READ_OWNED
    cmpeq   r7, r0
    bne     hook_conflict
    bl      compare_image
    cmp     r0, #0
    bne     image_conflict
    b       unchanged
first_install:
    /* Stock sites do not establish arena ownership. Require every word zero
     * before copying anything, including the unused tail of our reservation. */
    LDA     r0, CAVE_BEGIN
    LDA     r1, CAVE_END
1:  ldr     r2, [r0], #4
    cmp     r2, #0
    bne     image_conflict
    cmp     r0, r1
    blo     1b
    adr     r0, probe_image
    LDA     r1, CAVE_BEGIN
    LDA     r2, CAVE_END
2:  ldr     r3, [r0], #4
    str     r3, [r1], #4
    cmp     r1, r2
    blo     2b
    /* The template explicitly zeroes all state and padding. Publish the
     * complete resident image before either BL can reach it. */
    bl      publish
    LDA     r0, INIT_OWNED
    str     r0, [r4]
    LDA     r0, READ_OWNED
    str     r0, [r5]
    bl      publish
#endif
    mov     r0, #0
    b       done
unchanged:
    mov     r0, #1
    b       done
hook_conflict:
    mvn     r0, #7
    b       done
image_conflict:
    mvn     r0, #2
done:
    mov     sp, r8
    msr     cpsr_cxsf, r9
    pop     {r4-r9, ip, pc}

/* Compare every immutable byte, not just a magic tag. Marker words alone
 * are mutable. This leaf uses only caller-saved registers. */
compare_image:
    adr     r0, probe_image
    LDA     r1, CAVE_BEGIN
    mov     r2, #0
3:  cmp     r2, #(STATE - CAVE_BEGIN)
    addeq   r2, r2, #16
    ldr     r3, [r0, r2]
    ldr     ip, [r1, r2]
    cmp     r3, ip
    movne   r0, #1
    bxne    lr
    add     r2, r2, #4
    cmp     r2, #(CAVE_END - CAVE_BEGIN)
    blo     3b
    mov     r0, #0
    bx      lr

publish:
    push    {r4, lr}
    LDA     ip, 0xC000E91C
    blx     ip
    LDA     ip, 0xC000EABC
    blx     ip
    dsb     sy
    isb     sy
    pop     {r4, pc}

.balign 4
/* Keep probe_image locally bound so ADR resolves without linker relocations.
 * The upstream symbol reader still exposes this .text label to the builder. */
probe_image:
probe_init:
    /* Tiny, call-free critical sections keep each marker transaction intact.
     * Every register, flag and incoming stack argument reaches native intact. */
    push    {r0-r3}
    mrs     r3, cpsr
    cpsid   if
    LDA     r0, STATE
    ldr     r1, [r0]
    add     r1, r1, #1
    str     r1, [r0]
    ldr     r1, [r0, #4]
    add     r1, r1, #1
    str     r1, [r0, #4]
    dmb     sy
    msr     cpsr_cxsf, r3
    pop     {r0-r3}
    ldr     pc, init_target
init_target:
    .word   INIT_ORIG
probe_reader:
    push    {r0-r3}
    mrs     r3, cpsr
    cpsid   if
    LDA     r0, STATE
    ldr     r1, [r0, #8]
    add     r1, r1, #1
    str     r1, [r0, #8]
    ldr     r1, [r0, #4]
    str     r1, [r0, #12]
    dmb     sy
    msr     cpsr_cxsf, r3
    pop     {r0-r3}
    ldr     pc, read_target
read_target:
    .word   READ_ORIG
probe_code_end:
    .space  (STATE - CAVE_BEGIN) - (. - probe_image), 0
probe_state:
    .word   0, 0, 0, 0
    .space  (CAVE_END - CAVE_BEGIN) - (. - probe_image), 0
probe_image_end:
