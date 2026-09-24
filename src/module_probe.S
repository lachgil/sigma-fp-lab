#include "module_abi.h"

.syntax unified
.arm
.text

/* Offline-only, registry-managed fp 5.02 module. The runtime owns allocation
 * and cache publication; all callable code and private state move together.
 */
#ifndef MODULE_ID
#error MODULE_ID is required
#endif
#ifndef INITIAL_VALUE
#error INITIAL_VALUE is required
#endif
#ifndef STEP
#error STEP is required
#endif
#ifndef MODULE_ABI
#define MODULE_ABI FP_ABI
#endif
#ifndef INIT_RESULT
#define INIT_RESULT 0
#endif

module_start:
    .word FP_MODULE_MAGIC
    .word MODULE_ABI
    .word MODULE_ID
    .word module_end - module_start
    .word initialize - module_start
    .word invoke - module_start

.global initialize
initialize:                         @ initialize(api, record) -> status
    push {r4, r5, r6, lr}
    mov r4, r0
    cmp r4, #0
    beq bad_api
    ldr r1, [r4, #API_MAGIC]
    movw r2, #:lower16:FP_RUNTIME_MAGIC
    movt r2, #:upper16:FP_RUNTIME_MAGIC
    cmp r1, r2
    bne bad_api
    ldr r1, [r4, #API_ABI]
    cmp r1, #FP_ABI
    bne bad_api
    ldr r1, [r4, #API_BYTES]
    cmp r1, #API_SIZE
    blo bad_api

    adr r5, private_state
    ldr r1, [r5]
    add r1, r1, #1
    str r1, [r5]                    @ private initialization count
    ldr ip, [r4, #API_TICKS]
    blx ip
    str r0, [r5, #8]                @ private diagnostic timestamp

    mov r0, r4
    adr r1, module_start
    ldr r1, [r1, #MOD_ID]
    ldr r2, [r5, #4]
    ldr ip, [r4, #API_REPORT]
    blx ip
    movw r0, #:lower16:INIT_RESULT
    movt r0, #:upper16:INIT_RESULT
    pop {r4, r5, r6, pc}

bad_api:
    mvn r0, #1                     @ FP_EABI
    pop {r4, r5, r6, pc}

.global invoke
invoke:                             @ invoke(api, record, argument) -> value
    push {r4, r5, r6, lr}
    mov r4, r0
    adr r6, private_state
    ldr r5, [r6, #4]
    ldr r3, [r6, #12]
    add r5, r5, r3
    add r5, r5, r2
    str r5, [r6, #4]

    adr r1, module_start
    ldr r1, [r1, #MOD_ID]
    mov r2, r5
    ldr ip, [r4, #API_REPORT]
    blx ip
    mov r0, r5
    pop {r4, r5, r6, pc}

.balign 4
private_state:
    .word 0                         @ initialization count
    .word INITIAL_VALUE             @ private mutable counter
    .word 0                         @ last initialization clock sample
    .word STEP
module_end:
