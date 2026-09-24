#include "module_abi.h"

.syntax unified
.arm
.text

/* fp 5.02 ABI1 module 0x102. The runtime copies this entire image, including
 * state and the native scale, into USER memory before calling initialize.
 * Only three checked eight-byte branch veneers occupy the boot cave arena.
 * Boot installs the existing fcscale behavior, never a demo or a USB command.
 */
.equ FC_MODULE_ID,       0x102
.equ FC_PRESS_SITE,      0xC03722E8
.equ FC_RELEASE_SITE,    0xC0372330
.equ FC_SUBMIT_SITE,     0xC02E8A08
.equ FC_BUTTON_STOCK,    0xE92D4010
.equ FC_SUBMIT_STOCK,    0xE92D48F0
.equ FC_DCACHE,          0xC000E91C
.equ FC_ICACHE,          0xC000EABC
.equ FC_ECONFLICT,       -8
.equ FC_ENOSPACE,        -9
.equ FC_ARMED,           0
.equ FC_VENEER,          4
.equ FC_PRESS_BRANCH,    8
.equ FC_RELEASE_BRANCH,  12
.equ FC_SUBMIT_BRANCH,   16
.equ FC_STATE_BYTES,     0x90
.equ FC_VENEER_BYTES,    24

.macro constant reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

/* The architectural PC at add is this expansion's label plus sixteen. */
.macro pc_address reg, target
.Laddress\@:
    movw \reg, #:lower16:(\target - .Laddress\@ - 16)
    movt \reg, #:upper16:(\target - .Laddress\@ - 16)
    add \reg, pc, \reg
.endm

.macro call_fw address
    constant ip, \address
    blx ip
.endm

/* Prologue replacement uses B, not BL: the veneer must retain the caller's
 * LR. Every address in the checked cave arena is in reach of all three sites.
 */
.macro branch_word result, target, site
    sub \result, \target, \site
    sub \result, \result, #8
    mov \result, \result, asr #2
    bic \result, \result, #0xFF000000
    orr \result, \result, #0xEA000000
.endm

.global module_start
module_start:
    .word FP_MODULE_MAGIC
    .word FP_ABI
    .word FC_MODULE_ID
    .word module_end - module_start
    .word initialize - module_start
    .word invoke - module_start

/* initialize(api, record). All rejection paths precede any bump reservation
 * or hook change. Interrupts remain masked from fingerprints through cache
 * publication, as in module_ui's hook installation. Once a hook is armed we
 * must return success, otherwise the runtime would free its live target.
 */
.global initialize
initialize:
    cmp r0, #0
    beq initialize_bad_api
    tst r0, #3
    bne initialize_bad_api
    ldr r2, [r0, #API_MAGIC]
    constant r3, FP_RUNTIME_MAGIC
    cmp r2, r3
    bne initialize_bad_api
    ldr r2, [r0, #API_ABI]
    cmp r2, #FP_ABI
    bne initialize_bad_api
    ldr r2, [r0, #API_BYTES]
    cmp r2, #API_SIZE
    blo initialize_bad_api

    push {r4, r5, r6, r7, r8, r9, r10, r11, ip, lr}
    pc_address r4, fc_module_state
    ldr r0, [r4, #FC_ARMED]
    cmp r0, #0
    movne r0, #0
    bne initialize_return           @ a second call cannot reserve or reset
    constant r5, FC_PRESS_SITE
    constant r6, FC_RELEASE_SITE
    constant r7, FC_SUBMIT_SITE
    mrs r8, cpsr
    cpsid if

    ldr r0, [r5]
    constant r1, FC_BUTTON_STOCK
    cmp r0, r1
    bne initialize_conflict
    ldr r0, [r6]
    cmp r0, r1
    bne initialize_conflict
    ldr r0, [r7]
    constant r1, FC_SUBMIT_STOCK
    cmp r0, r1
    bne initialize_conflict

    constant r0, FP_CAVE_BUMP
    ldr r9, [r0]
    constant r1, FP_CAVE_BEGIN
    cmp r9, r1
    blo initialize_no_space
    tst r9, #3
    bne initialize_no_space
    adds r10, r9, #FC_VENEER_BYTES
    bcs initialize_no_space
    constant r1, FP_CAVE_END
    cmp r10, r1
    bhi initialize_no_space
    mov r1, r9
initialize_check_cave:
    ldr r2, [r1], #4
    cmp r2, #0
    bne initialize_conflict         @ a stale bump must not overwrite a user
    cmp r1, r10
    blo initialize_check_cave

    str r10, [r0]                   @ no rejection is possible beyond here
    constant r0, 0xE51FF004         @ ldr pc,[pc,#-4], preserving arguments/LR
    str r0, [r9]
    str r0, [r9, #8]
    str r0, [r9, #16]
    pc_address r0, fc_press
    str r0, [r9, #4]
    pc_address r0, fc_release
    str r0, [r9, #12]
    pc_address r0, fc_submit
    str r0, [r9, #20]
    str r9, [r4, #FC_VENEER]
    branch_word r10, r9, r5
    add r0, r9, #8
    branch_word r11, r0, r6
    add r0, r9, #16
    branch_word r1, r0, r7
    str r10, [r4, #FC_PRESS_BRANCH]
    str r11, [r4, #FC_RELEASE_BRANCH]
    str r1, [r4, #FC_SUBMIT_BRANCH]

    dmb
    call_fw FC_DCACHE               @ publish resident code/state and veneers
    call_fw FC_ICACHE
    dsb
    isb
    ldr r0, [r4, #FC_SUBMIT_BRANCH]
    str r10, [r5]
    str r11, [r6]
    str r0, [r7]
    dsb
    call_fw FC_DCACHE               @ publish all three patched prologues
    call_fw FC_ICACHE
    dsb
    isb
    mov r0, #1
    str r0, [r4, #FC_ARMED]
    dmb
    mov r0, #0
    b initialize_restore_interrupts
initialize_conflict:
    mov r0, #FC_ECONFLICT
    b initialize_restore_interrupts
initialize_no_space:
    mov r0, #FC_ENOSPACE
initialize_restore_interrupts:
    msr cpsr_c, r8
initialize_return:
    pop {r4, r5, r6, r7, r8, r9, r10, r11, ip, pc}
initialize_bad_api:
    mov r0, #FP_EABI
    bx lr

/* Read-only diagnostic service: invoke(api, record, scalar selector).
 * 0 version, 1 mode, 2 scale flag, 3 presses, 4 releases, 5 holds,
 * 6 submits, 7 painted frames, 8 resident state address, 9 armed,
 * 10 veneer address, 11 last hold duration in ms, 12 outstanding press.
 * Unknown selectors return FP_EFORMAT. No host action is needed to enable FC.
 */
.global invoke
invoke:
    cmp r2, #0
    moveq r0, #1
    bxeq lr
    pc_address r1, fc_state
    cmp r2, #1
    ldreq r0, [r1]
    bxeq lr
    cmp r2, #2
    ldreq r0, [r1, #0x0C]
    bxeq lr
    cmp r2, #3
    ldreq r0, [r1, #0x04]
    bxeq lr
    cmp r2, #4
    ldreq r0, [r1, #0x08]
    bxeq lr
    cmp r2, #5
    ldreq r0, [r1, #0x18]
    bxeq lr
    cmp r2, #6
    ldreq r0, [r1, #0x74]
    bxeq lr
    cmp r2, #7
    ldreq r0, [r1, #0x78]
    bxeq lr
    cmp r2, #8
    moveq r0, r1
    bxeq lr
    cmp r2, #11
    ldreq r0, [r1, #0x14]
    bxeq lr
    cmp r2, #12
    ldreq r0, [r1, #0x34]
    bxeq lr
    pc_address r1, fc_module_state
    cmp r2, #9
    ldreq r0, [r1, #FC_ARMED]
    bxeq lr
    cmp r2, #10
    ldreq r0, [r1, #FC_VENEER]
    bxeq lr
    mov r0, #FP_EFORMAT
    bx lr

/* Only the state-addressing macro differs from the standalone FC tool.
 * The builder supplies CLOCK/HOLD_MS/SUBMIT_RESUME/PROBE but never STATE.
 */
#ifdef STATE
#error "module_fcscale must not inherit the standalone STATE cave"
#endif
#define FCSCALE_PIC_STATE fc_state
#include "fcscale.S"

.balign 4
.global fc_state
fc_state:
    .space FC_STATE_BYTES, 0
.global fc_module_state
fc_module_state:
    .space 20, 0
.global module_end
module_end:
