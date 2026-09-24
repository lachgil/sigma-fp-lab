/* ABI1 ARM entry points. Only data is relocated, before any C executes.
 * The linker places this entry immediately after the six-word header.
 * Linker-defined ABSOLUTE symbols below are image offsets, not pointers.
 */
.syntax unified
.arm
.section .fp.header, "a", %progbits
.space 24, 0

.section .fp.bootstrap, "ax", %progbits
.balign 4
.global __fp_c_initialize
.hidden __fp_c_initialize
.type __fp_c_initialize, %function
__fp_c_initialize:
    adr r3, __fp_c_initialize
    sub r3, r3, #24
    ldr ip, .Lstate_offset
    ldr r2, [r3, ip]
    cmp r2, #0
    bne .Linitialized

    /* Preserve both arguments and AAPCS stack alignment. */
    push {r0, r1, r4, lr}
    ldr r4, .Lrel_start_offset
    add r4, r3, r4
    ldr ip, .Lrel_end_offset
    add ip, r3, ip
.Lrelocate:
    cmp r4, ip
    bhs .Lrelocated
    /* Build-time validation permits only symbol-free R_ARM_RELATIVE.
     * REL addends are the linked image offsets stored at each target.
     */
    ldr r0, [r4], #8
    ldr r1, [r3, r0]
    add r1, r1, r3
    str r1, [r3, r0]
    b .Lrelocate
.Lrelocated:
    ldr ip, .Lstate_offset
    mov r2, #1
    str r2, [r3, ip]
    pop {r0, r1, r4, lr}
.Linitialized:
    /* A repeated init reruns the C callback, not the relocations. */
    b fp_module_init
.size __fp_c_initialize, . - __fp_c_initialize

.global __fp_c_invoke
.hidden __fp_c_invoke
.type __fp_c_invoke, %function
__fp_c_invoke:
    b fp_module_invoke
.size __fp_c_invoke, . - __fp_c_invoke

.balign 4
.Lstate_offset:     .word __fp_c_state_offset
.Lrel_start_offset: .word __fp_c_rel_start_offset
.Lrel_end_offset:   .word __fp_c_rel_end_offset

.section .note.GNU-stack, "", %progbits
