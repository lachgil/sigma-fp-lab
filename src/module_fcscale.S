#include "module_abi.h"

.syntax unified
.arm
.text

/* fp 5.02 ABI1 module 0x102. The runtime copies this entire image, including
 * state and the native scale, into USER memory before calling initialize.
 * The runtime owns the three hooks, their veneers and shutdown restoration.
 * Boot installs the existing fcscale behavior, never a demo or a USB command.
 */
.equ FC_MODULE_ID,       0x102
.equ FC_PRESS_SITE,      0xC03722E8
.equ FC_RELEASE_SITE,    0xC0372330
.equ FC_SUBMIT_SITE,     0xC02E8A08
.equ FC_BUTTON_STOCK,    0xE92D4010
.equ FC_SUBMIT_STOCK,    0xE92D48F0
.equ FC_ARMED,           0
.equ FC_VENEER,          4
.equ FC_STATE_BYTES,     0x90

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


.global module_start
module_start:
    .word FP_MODULE_MAGIC
    .word FP_ABI
    .word FC_MODULE_ID
    .word module_end - module_start
    .word initialize - module_start
    .word invoke - module_start

/* initialize(api, record). The runtime installs the three hooks atomically.
 * No operation after successful publication can fail or discard live targets.
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

    push {r4, lr}
    sub sp, sp, #(3 * HOOK_SIZE)
    pc_address r4, fc_module_state
    ldr r2, [r4, #FC_ARMED]
    cmp r2, #0
    movne r0, #0
    bne initialize_return           @ a second call cannot reserve or reset
    constant r2, FC_PRESS_SITE
    str r2, [sp, #HOOK_SITE]
    constant r2, FC_BUTTON_STOCK
    str r2, [sp, #HOOK_ORIGINAL]
    pc_address r2, fc_press
    str r2, [sp, #HOOK_TARGET]
    constant r2, FC_RELEASE_SITE
    str r2, [sp, #(HOOK_SIZE + HOOK_SITE)]
    constant r2, FC_BUTTON_STOCK
    str r2, [sp, #(HOOK_SIZE + HOOK_ORIGINAL)]
    pc_address r2, fc_release
    str r2, [sp, #(HOOK_SIZE + HOOK_TARGET)]
    constant r2, FC_SUBMIT_SITE
    str r2, [sp, #(2 * HOOK_SIZE + HOOK_SITE)]
    constant r2, FC_SUBMIT_STOCK
    str r2, [sp, #(2 * HOOK_SIZE + HOOK_ORIGINAL)]
    pc_address r2, fc_submit
    str r2, [sp, #(2 * HOOK_SIZE + HOOK_TARGET)]
    mov r2, sp
    mov r3, #3
    ldr ip, [r0, #API_INSTALL_HOOKS]
    blx ip
    cmp r0, #FP_OK
    bne initialize_return
    ldr r1, [sp, #HOOK_VENEER]
    str r1, [r4, #FC_VENEER]
    mov r1, #1
    str r1, [r4, #FC_ARMED]
    dmb
initialize_return:
    add sp, sp, #(3 * HOOK_SIZE)
    pop {r4, pc}
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
    .space 8, 0
.global module_end
module_end:
