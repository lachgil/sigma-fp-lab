#include "module_abi.h"
#include "module_ui.h"

.syntax unified
.arm
.text

/* Independent ABI1 UI consumer. Load FP_UI_ID before this module. All drawing
 * and custom-function observation use the runtime's ID-based call service.
 * init never presents or arms hooks. Calls are trusted and serialized.
 *
 * invoke argument 0 increments the successful-display count, draws C (calls)
 * and B (observed custom-function presses), then presents. Argument 1 hides.
 * Return: displayed count for argument 0, zero for hide, negative service or
 * runtime error otherwise. The runtime wraps this as status r0, value r1;
 * provider invocation errors therefore appear in the returned value, not the
 * outer status. Counts wrap as uint32, including the high bit.
 *
 * Each provider FILL is atomic, not the whole multi-call drawing transaction.
 * On-frame rendering may observe a partly updated number. No callbacks enter
 * this module, and no firmware addresses or provider private memory are used.
 */
module_start:
    .word FP_MODULE_MAGIC
    .word FP_ABI
    .word FP_UI_DEMO_ID
    .word module_end - module_start
    .word initialize - module_start
    .word invoke - module_start

.global initialize
initialize:                         @ init(api, record) -> zero or error
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
    ldr r1, [r4, #API_LOOKUP]
    cmp r1, #0
    beq bad_api
    ldr r1, [r4, #API_CALL]
    cmp r1, #0
    beq bad_api
    ldr r1, [r4, #API_REPORT]
    cmp r1, #0
    beq bad_api

    bl state_address
    mov r5, r0
    mov r0, r4
    mov r1, #FP_UI_ID
    ldr ip, [r4, #API_LOOKUP]
    blx ip
    cmp r0, #0
    beq provider_missing
    ldr r1, [r0, #REC_STATUS]
    cmp r1, #FP_OK
    bne provider_not_ready

    mov r0, #FP_UI_QUERY
    bl command
    cmp r0, #0
    blt initialize_error
    cmp r0, #FP_UI_VERSION
    bne provider_version
    mov r0, #FP_UI_STATUS
    bl command
    cmp r0, #0
    bne initialize_error
    mov r0, #FP_UI_VERSION
    bl report_value                   @ ready version, without arming hooks
    mov r0, #FP_OK
    pop {r4, r5, r6, pc}
provider_missing:
    mov r0, #FP_ENOENT                @ missing or later catalog entry
    b initialize_error
provider_not_ready:
    mov r0, #FP_ENOTREADY             @ earlier provider failed initialization
    b initialize_error
provider_version:
    mov r0, #FP_EABI
initialize_error:
    bl report_value
    pop {r4, r5, r6, pc}
bad_api:
    mov r0, #FP_EABI                  @ cannot safely report through bad table
    pop {r4, r5, r6, pc}

.global invoke
invoke:                              @ invoke(api, record, argument) -> value
    push {r4, r5, r6, r7, r8, lr}
    mov r4, r0
    mov r8, r2
    bl state_address
    mov r5, r0
    cmp r8, #1
    beq hide
    cmp r8, #0
    bne bad_argument

    mov r0, #FP_UI_BUTTONS
    bl provider_call                  @ BUTTONS is uint32, not a signed status
    cmp r0, #0
    bne invoke_return
    str r1, [r5, #4]
    mov r7, r1
    ldr r6, [r5]
    add r6, r6, #1

    mov r0, #0
    str r0, [r5, #(8 + UI_REQ_A)]
    str r0, [r5, #(8 + UI_REQ_B)]
    mov r0, #FP_UI_WIDTH
    str r0, [r5, #(8 + UI_REQ_C)]
    mov r0, #FP_UI_HEIGHT
    str r0, [r5, #(8 + UI_REQ_D)]
    movw r0, #FP_UI_BLACK
    str r0, [r5, #(8 + UI_REQ_E)]
    mov r0, #FP_UI_FILL
    bl command
    cmp r0, #0
    bne invoke_return

    movw r0, #FP_UI_WHITE
    str r0, [r5, #(8 + UI_REQ_E)]
    mov r0, #10                       @ C glyph, successful display count
    mov r1, #2
    mov r2, #3
    bl draw_glyph
    cmp r0, #0
    bne invoke_return
    mov r0, r6
    mov r1, #3
    bl draw_decimal
    cmp r0, #0
    bne invoke_return
    mov r0, #11                       @ B glyph, observed button presses
    mov r1, #2
    mov r2, #19
    bl draw_glyph
    cmp r0, #0
    bne invoke_return
    mov r0, r7
    mov r1, #19
    bl draw_decimal
    cmp r0, #0
    bne invoke_return
    mov r0, #FP_UI_PRESENT
    bl command
    cmp r0, #0
    bne invoke_return
    str r6, [r5]                      @ count only completed presentations
    mov r0, r6
    b invoke_return
hide:
    mov r0, #FP_UI_HIDE
    bl command
    b invoke_return
bad_argument:
    mov r0, #FP_UI_EINVAL
invoke_return:
    bl report_value                   @ preserve provider error or result
    pop {r4, r5, r6, r7, r8, pc}

/* Internal helpers use r4=api, r5=private state and preserve both. The mutable
 * request belongs to this image. Runtime lookup is performed on every call;
 * there is no cached provider entrypoint or direct call into provider code.
 */
provider_call:                        @ op -> runtime status r0, value r1
    push {r6, lr}
    str r0, [r5, #(8 + UI_REQ_OP)]
    mov r0, r4
    mov r1, #FP_UI_ID
    add r2, r5, #8
    ldr ip, [r4, #API_CALL]
    blx ip
    pop {r6, pc}
command:                              @ op -> runtime error or service result
    push {r6, lr}
    bl provider_call
    cmp r0, #0
    moveq r0, r1
    pop {r6, pc}
report_value:                         @ diagnostic only; retain actual result
    push {r6, lr}
    mov r6, r0
    mov r2, r0
    mov r0, r4
    movw r1, #FP_UI_DEMO_ID
    ldr ip, [r4, #API_REPORT]
    blx ip
    mov r0, r6
    pop {r6, pc}

/* Right-aligned decimal uint32, no division instruction or runtime library.
 * Ten 8-pixel cells begin at x14. The rightmost glyph ends at x91, and each
 * 3x5 glyph is doubled to 6x10 pixels. Leading zeroes are omitted except zero.
 */
draw_decimal:                         @ r0=value, r1=top y -> zero or error
    push {r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
    mov r6, r0
    mov r11, r1
    adr r7, decimal_powers
    mov r8, #14
    mov r9, #0
next_digit:
    ldr r3, [r7]
    mov r10, #0
subtract_digit:
    cmp r6, r3
    blo digit_ready
    sub r6, r6, r3
    add r10, r10, #1
    b subtract_digit
digit_ready:
    cmp r10, #0
    bne visible_digit
    cmp r9, #0
    bne visible_digit
    cmp r3, #1
    bne advance_digit
visible_digit:
    mov r9, #1
    mov r0, r10
    mov r1, r8
    mov r2, r11
    bl draw_glyph
    cmp r0, #0
    bne decimal_return
advance_digit:
    ldr r3, [r7], #4
    add r8, r8, #8
    cmp r3, #1
    bne next_digit
    mov r0, #0
decimal_return:
    pop {r4, r5, r6, r7, r8, r9, r10, r11, r12, pc}
.balign 4
decimal_powers:
    .word 1000000000, 100000000, 10000000, 1000000, 100000
    .word 10000, 1000, 100, 10, 1

/* Bitmap bits 14..0, top to bottom and left to right. Only foreground pixels
 * issue FILL calls. The earlier full-tile FILL erases old and leading digits.
 */
draw_glyph:                           @ r0=index, r1=x, r2=y -> zero or error
    push {r4, r5, r6, r7, r8, r9, r10, lr}
    mov r6, r1
    mov r7, r2
    adr r3, glyphs
    ldr r8, [r3, r0, lsl #2]
    mov r9, #0
next_row:
    mov r10, #0
next_pixel:
    tst r8, #0x4000
    beq skip_pixel
    add r0, r6, r10, lsl #1
    str r0, [r5, #(8 + UI_REQ_A)]
    add r0, r7, r9, lsl #1
    str r0, [r5, #(8 + UI_REQ_B)]
    mov r0, #2
    str r0, [r5, #(8 + UI_REQ_C)]
    str r0, [r5, #(8 + UI_REQ_D)]
    mov r0, #FP_UI_FILL
    bl command
    cmp r0, #0
    bne glyph_return
skip_pixel:
    lsl r8, r8, #1
    add r10, r10, #1
    cmp r10, #3
    blo next_pixel
    add r9, r9, #1
    cmp r9, #5
    blo next_row
    mov r0, #0
glyph_return:
    pop {r4, r5, r6, r7, r8, r9, r10, pc}
.balign 4
glyphs:
    .word 0b111101101101111           @ 0
    .word 0b010110010010111           @ 1
    .word 0b111001111100111           @ 2
    .word 0b111001111001111           @ 3
    .word 0b101101111001001           @ 4
    .word 0b111100111001111           @ 5
    .word 0b111100111101111           @ 6
    .word 0b111001010010010           @ 7
    .word 0b111101111101111           @ 8
    .word 0b111101111001111           @ 9
    .word 0b111100100100111           @ C
    .word 0b110101110101110           @ B

/* Keep this ADR beside its target so it always fits the ARM immediate. */
state_address:
    adr r0, private_state
    bx lr
.balign 4
private_state:
    .word 0                          @ successful display count, uint32 wrap
    .word 0                          @ latest observed button count
request:
    .space UI_REQ_SIZE, 0             @ op, a, b, c, d, e; never shared storage
module_end:
