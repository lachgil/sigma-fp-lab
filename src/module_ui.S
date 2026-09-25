#include "module_ui.h"

.syntax unified
.arm
.text

/* Optional fp 5.02 provider. The runtime owns firmware hook publication and
 * shutdown restoration; UI consumers use ABI1 services. Local pointers are PIC.
 */
.equ UI_SUBMIT_SITE,  0xC02E8A08
.equ UI_SUBMIT_STOCK, 0xE92D48F0
.equ UI_PRESS_SITE,   0xC03722E8
.equ UI_PRESS_STOCK,  0xE92D4010
.equ UI_STATE_WORD,   0xC3033A44
.equ UI_H_GET,        0xC001D740
.equ UI_H_ADDR,       0xC001D7F0
.equ UI_H_FREE,       0xC001D7A0
.equ UI_LOCK,         0
.equ UI_VISIBLE,      4
.equ UI_ARMED,        8
.equ UI_ERROR,        12
.equ UI_COUNT,        16
.equ UI_VENEER,       20
.equ UI_SUBMIT_BRANCH,24
.equ UI_PRESS_BRANCH, 28
.equ UI_API,          32
.equ UI_RECORD,       36
.equ UI_PRESS_VENEER, 40
.equ SLOT_DESC,       0
.equ SLOT_BASE,       4
.equ SLOT_GEOMETRY,   8
.equ SLOT_WIDTH,      12
.equ SLOT_HEIGHT,     16
.equ SLOT_PAINTED,    20
.equ SLOT_BYTES,      32
.equ TILE_BYTES,      (FP_UI_WIDTH * FP_UI_HEIGHT * 2)
.equ TILE_OFFSET,     ((FP_UI_Y * FP_UI_SURFACE_WIDTH + FP_UI_X) * 2)
.equ SURFACE_STRIDE,  (FP_UI_SURFACE_WIDTH * 2)
.equ UI_SLOTS_OFFSET, 48
.equ UI_CANVAS_OFFSET,(UI_SLOTS_OFFSET + 3 * SLOT_BYTES)
.equ UI_PIXELS_OFFSET,(UI_CANVAS_OFFSET + TILE_BYTES)
.equ UI_STORAGE_BYTES,(UI_PIXELS_OFFSET + 3 * 2 * TILE_BYTES)

.macro constant reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

/* The add's architectural PC is the local label plus sixteen bytes. */
.macro pc_address reg, target
.Laddress\@:
    movw \reg, #:lower16:(\target - .Laddress\@ - 16)
    movt \reg, #:upper16:(\target - .Laddress\@ - 16)
    add \reg, pc, \reg
.endm

.macro load_state reg
    pc_address \reg, ui_storage
    ldr \reg, [\reg]
.endm

.macro call_fw address
    constant ip, \address
    blx ip
.endm

.global module_start
module_start:
    .word FP_MODULE_MAGIC
    .word FP_ABI
    .word FP_UI_ID
    .word module_end - module_start
    .word initialize - module_start
    .word invoke - module_start

/* Reserve the bounded USER block at initialization, not in the frame hook.
 * The compact artifact keeps upstream's 32-KiB binary-pad budget intact.
 * No init path patches firmware; the successful allocation lasts until reboot.
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
    push {r4, r5, r6, r7, r8, lr}
    mov r7, r0
    mov r8, r1
    pc_address r4, ui_allocation
    pc_address r6, ui_storage
    ldr r0, [r6]
    cmp r0, #0
    bne initialize_ready            @ repeated initialization never leaks
    mov r0, r4
    mov r1, #0                      @ USER, not a RAW/recording buffer
    constant r2, UI_STORAGE_BYTES
    mov r3, #0
    call_fw UI_H_GET
    mov r0, r4
    call_fw UI_H_ADDR
    cmp r0, #0
    beq initialize_no_memory        @ runtime convention: no owned allocation
    mov r5, r0
    tst r5, #3
    bne initialize_bad_storage
    constant r1, UI_STORAGE_BYTES
    adds r1, r5, r1
    bcs initialize_bad_storage
    mov r1, r5
    mov r2, #UI_CANVAS_OFFSET       @ planes are filled before PAINTED is set
    mov r0, #0
initialize_zero:
    str r0, [r1], #4
    subs r2, r2, #4
    bne initialize_zero
    add r1, r5, #UI_CANVAS_OFFSET
    mov r2, #TILE_BYTES
    constant r0, (FP_UI_BLACK | (FP_UI_BLACK << 16))
initialize_canvas:
    str r0, [r1], #4
    subs r2, r2, #4
    bne initialize_canvas
    str r7, [r5, #UI_API]
    str r8, [r5, #UI_RECORD]
    dmb
    str r5, [r6]                    @ publish only completely initialized state
initialize_ready:
    mov r0, #0
    pop {r4, r5, r6, r7, r8, pc}
initialize_bad_storage:
    mov r0, r4
    mov r1, #2
    call_fw UI_H_FREE               @ malformed but owned result must not leak
    mov r5, #FP_EFORMAT
    b initialize_clear_descriptor
initialize_no_memory:
    mov r5, #FP_ENOMEM
initialize_clear_descriptor:
    mov r0, #0
    str r0, [r6]
    str r0, [r4]
    str r0, [r4, #4]
    str r0, [r4, #8]
    str r0, [r4, #12]
    mov r0, r5
    pop {r4, r5, r6, r7, r8, pc}
initialize_bad_api:
    mov r0, #FP_EABI
    bx lr

/* invoke(api, record, request) returns the service value in r0. The unchanged
 * runtime moves it to public r1 and independently returns dispatch status r0.
 */
.global invoke
invoke:
    push {r4, r5, r6, lr}
    load_state r4
    cmp r2, #0
    beq invoke_invalid
    tst r2, #3
    bne invoke_invalid
    adds r3, r2, #UI_REQ_SIZE
    bcs invoke_invalid
    mov r5, r2
    ldr r6, [r5, #UI_REQ_OP]
    cmp r6, #FP_UI_STATUS
    bhi invoke_invalid
    cmp r6, #FP_UI_QUERY
    moveq r0, #FP_UI_VERSION
    beq invoke_return
    cmp r6, #FP_UI_BUTTONS
    ldreq r0, [r4, #UI_COUNT]
    beq invoke_return
    cmp r6, #FP_UI_STATUS
    ldreq r0, [r4, #UI_ERROR]
    beq invoke_return

    mov r0, #1
    swp r1, r0, [r4]
    cmp r1, #0
    bne invoke_busy
    dmb
    cmp r6, #FP_UI_FILL
    beq invoke_fill
    cmp r6, #FP_UI_PRESENT
    beq invoke_present
    mov r0, #0                       @ HIDE never dereferences a buffer slot
    str r0, [r4, #UI_VISIBLE]
    b invoke_unlock
invoke_fill:
    mov r0, r5
    bl ui_fill
    b invoke_unlock
invoke_present:
    bl ui_install
    str r0, [r4, #UI_ERROR]
    cmp r0, #0
    moveq r1, #1
    streq r1, [r4, #UI_VISIBLE]
invoke_unlock:
    dmb
    mov r1, #0
    str r1, [r4, #UI_LOCK]
    b invoke_return
invoke_invalid:
    mov r0, #FP_UI_EINVAL
    b invoke_return
invoke_busy:
    mov r0, #FP_UI_EBUSY
invoke_return:
    pop {r4, r5, r6, pc}

/* ui_fill(request), with UI_LOCK owned. Clip using remaining dimensions,
 * never x+w or y+h. The store loop visits at most the 3,072 canvas pixels.
 */
.global ui_fill
ui_fill:
    push {r4, r5, r6, r7, r8, lr}
    ldr r1, [r0, #UI_REQ_A]
    ldr r2, [r0, #UI_REQ_B]
    ldr r3, [r0, #UI_REQ_C]
    ldr r4, [r0, #UI_REQ_D]
    ldr r5, [r0, #UI_REQ_E]
    cmp r1, #FP_UI_WIDTH
    bhs fill_done
    cmp r2, #FP_UI_HEIGHT
    bhs fill_done
    cmp r3, #0
    beq fill_done
    cmp r4, #0
    beq fill_done
    rsb r6, r1, #FP_UI_WIDTH
    cmp r3, r6
    movhi r3, r6
    rsb r6, r2, #FP_UI_HEIGHT
    cmp r4, r6
    movhi r4, r6
    load_state r7
    add r7, r7, #UI_CANVAS_OFFSET
    add r6, r2, r2, lsl #1
    add r7, r7, r6, lsl #6           @ 192 bytes per canvas row
    add r7, r7, r1, lsl #1
    rsb r8, r3, #FP_UI_WIDTH
    mov r8, r8, lsl #1
fill_row:
    mov r6, r3
fill_pixel:
    strh r5, [r7], #2
    subs r6, r6, #1
    bne fill_pixel
    add r7, r7, r8
    subs r4, r4, #1
    bne fill_row
fill_done:
    mov r0, #0
    pop {r4, r5, r6, r7, r8, pc}

/* ui_install(), with UI_LOCK owned. PRESENT lazily requests one atomic batch
 * using the API and owner retained at initialization. The runtime owns every
 * firmware/cave write and cache publication. Repeated PRESENT is read-only.
 */
.global ui_install
ui_install:
    push {r4, r5, r6, lr}
    sub sp, sp, #(2 * HOOK_SIZE)
    load_state r4
    constant r5, UI_SUBMIT_SITE
    constant r6, UI_PRESS_SITE
    ldr r0, [r4, #UI_ARMED]
    cmp r0, #0
    beq install_first
    ldr r0, [r5]
    ldr r1, [r4, #UI_SUBMIT_BRANCH]
    cmp r0, r1
    bne install_conflict
    ldr r0, [r6]
    ldr r1, [r4, #UI_PRESS_BRANCH]
    cmp r0, r1
    bne install_conflict
    ldr r2, [r4, #UI_VENEER]
    ldr r3, [r4, #UI_PRESS_VENEER]
    ldr r0, [r2]
    constant r1, 0xE51FF004
    cmp r0, r1
    bne install_conflict
    ldr r0, [r3]
    cmp r0, r1
    bne install_conflict
    ldr r0, [r2, #4]
    pc_address r1, ui_submit
    cmp r0, r1
    bne install_conflict
    ldr r0, [r3, #4]
    pc_address r1, ui_press
    cmp r0, r1
    bne install_conflict
    mov r0, #0
    b install_return
install_conflict:
    mov r0, #FP_UI_ECONFLICT
    b install_return
install_first:
    str r5, [sp, #HOOK_SITE]
    constant r2, UI_SUBMIT_STOCK
    str r2, [sp, #HOOK_ORIGINAL]
    pc_address r2, ui_submit
    str r2, [sp, #HOOK_TARGET]
    str r6, [sp, #(HOOK_SIZE + HOOK_SITE)]
    constant r2, UI_PRESS_STOCK
    str r2, [sp, #(HOOK_SIZE + HOOK_ORIGINAL)]
    pc_address r2, ui_press
    str r2, [sp, #(HOOK_SIZE + HOOK_TARGET)]
    ldr r0, [r4, #UI_API]
    ldr r1, [r4, #UI_RECORD]
    mov r2, sp
    mov r3, #2
    ldr ip, [r0, #API_INSTALL_HOOKS]
    blx ip
    cmp r0, #FP_ECONFLICT
    beq install_conflict
    cmp r0, #FP_ENOSPACE
    moveq r0, #FP_UI_ENOSPACE
    beq install_return
    cmp r0, #FP_OK
    bne install_return
    ldr r1, [sp, #HOOK_VENEER]
    str r1, [r4, #UI_VENEER]
    ldr r1, [sp, #(HOOK_SIZE + HOOK_VENEER)]
    str r1, [r4, #UI_PRESS_VENEER]
    ldr r1, [r5]
    str r1, [r4, #UI_SUBMIT_BRANCH]
    ldr r1, [r6]
    str r1, [r4, #UI_PRESS_BRANCH]
    mov r0, #1
    str r0, [r4, #UI_ARMED]
    dmb
    mov r0, #0
install_return:
    add sp, sp, #(2 * HOOK_SIZE)
    pop {r4, r5, r6, pc}

/* Observer only. The tiny IRQ/FIQ-masked increment has no calls, canvas lock,
 * wait, or dropped press on render contention. All entry registers and flags
 * survive, followed by the exact stock push and its next instruction.
 * Release is never hooked, and no FalseColor event is consumed or changed.
 */
.global ui_press
ui_press:
    push {r0, r1, r2, r3, ip, lr}
    mrs r3, cpsr
    cpsid if
    load_state r0
    ldr r1, [r0, #UI_COUNT]
    add r1, r1, #1
    str r1, [r0, #UI_COUNT]
    dmb
    msr cpsr_cxsf, r3
    pop {r0, r1, r2, r3, ip, lr}
    push {r4, lr}                    @ original 0xE92D4010
    ldr pc, [pc, #-4]
    .word UI_PRESS_SITE + 4

/* Preserve even r3/ip and condition flags, then replay the displaced stock
 * submit prologue. The continuation literal changes no argument register.
 */
.global ui_submit
ui_submit:
    push {r0, r1, r2, r3, ip, lr}
    mrs ip, cpsr
    push {ip, lr}                    @ retain 8-byte alignment across the call
    bl ui_on_submit
    pop {ip, lr}
    msr cpsr_f, ip
    pop {r0, r1, r2, r3, ip, lr}
    push {r4, r5, r6, r7, fp, lr}    @ original 0xE92D48F0
    ldr pc, [pc, #-4]
    .word UI_SUBMIT_SITE + 4

/* ui_on_submit(controller, descriptor, sub). Every framebuffer write derives
 * from this submission, never a cached pointer. Descriptor memory itself is
 * trusted firmware input; alignment, geometry and extent arithmetic are
 * checked before pixels are touched. Invalid surfaces are ordinary no-ops.
 */
.global ui_on_submit
ui_on_submit:
    push {r4, r5, r6, r7, r8, r9, r10, r11, ip, lr}
    sub sp, sp, #16
    cmp r2, #0
    bne submit_return
    cmp r1, #0
    beq submit_return
    tst r1, #3
    bne submit_return
    adds r0, r1, #12
    bcs submit_return
    str r1, [sp]
    ldr r0, [r1]
    cmp r0, #1
    bne submit_return
    ldr r5, [r1, #4]
    cmp r5, #0
    beq submit_return
    tst r5, #1
    bne submit_return
    ldr r6, [r1, #8]
    cmp r6, #0
    beq submit_return
    tst r6, #3
    bne submit_return
    adds r0, r6, #8
    bcs submit_return
    ldr r0, [r6]
    cmp r0, #FP_UI_SURFACE_WIDTH
    bne submit_return
    ldr r7, [r6, #4]
    movw r0, #(FP_UI_Y + FP_UI_HEIGHT)
    cmp r7, r0
    blo submit_return
    cmp r7, #FP_UI_SURFACE_MAX_HEIGHT
    bhi submit_return
    adds r0, r5, r7, lsl #11         @ validated width is 1024, bpp is two
    bcs submit_return
    str r0, [sp, #8]                @ current surface end, for overlap checks

    load_state r4
    mov r0, #1
    swp r1, r0, [r4]
    cmp r1, #0
    bne submit_return                @ never wait inside firmware submission
    dmb
    ldr r8, [r4, #UI_VISIBLE]
    constant r0, UI_STATE_WORD
    ldr r0, [r0]
    cmp r0, #2                      @ only the positively identified live state
    movne r8, #0                    @ menu/playback still reach restoration
    add r9, r4, #UI_SLOTS_OFFSET
    add r11, r9, #(3 * SLOT_BYTES)
    mov r10, #0                     @ first reusable, unpainted slot
    str r10, [sp, #4]               @ exact identity, after all overlap checks
submit_find_slot:
    ldr r0, [r9, #SLOT_BASE]
    cmp r0, r5
    bne submit_other_identity
    ldr r0, [r9, #SLOT_DESC]
    ldr r1, [sp]
    cmp r0, r1
    bne submit_other_identity
    ldr r0, [r9, #SLOT_GEOMETRY]
    cmp r0, r6
    bne submit_other_identity
    ldr r0, [r9, #SLOT_WIDTH]
    cmp r0, #FP_UI_SURFACE_WIDTH
    bne submit_other_identity
    ldr r0, [r9, #SLOT_HEIGHT]
    cmp r0, r7
    bne submit_other_identity
    str r9, [sp, #4]
    b submit_next_slot
submit_other_identity:
    ldr r0, [r9, #SLOT_PAINTED]
    cmp r0, #0
    bne submit_check_overlap
    cmp r10, #0
    moveq r10, r9
    b submit_next_slot
submit_check_overlap:
    /* A changed descriptor for the same memory cannot be mistaken for a new
     * rotation slot. Also reject partial overlap with any painted surface.
     */
    ldr r0, [r9, #SLOT_BASE]
    ldr r1, [r9, #SLOT_HEIGHT]
    add r1, r0, r1, lsl #11
    cmp r5, r1
    bhs submit_next_slot
    ldr r1, [sp, #8]
    cmp r0, r1
    blo submit_identity_error
submit_next_slot:
    add r9, r9, #SLOT_BYTES
    cmp r9, r11
    blo submit_find_slot
    ldr r9, [sp, #4]
    cmp r9, #0
    bne submit_selected
    cmp r8, #0
    beq submit_ready                @ hiding never reserves another slot
    cmp r10, #0
    beq submit_slots_error          @ never evict a still-painted buffer
    mov r9, r10
submit_selected:
    ldr r3, [r9, #SLOT_PAINTED]
    orrs r0, r3, r8
    beq submit_ready
    ldr r0, [sp]
    str r0, [r9, #SLOT_DESC]
    str r5, [r9, #SLOT_BASE]
    str r6, [r9, #SLOT_GEOMETRY]
    mov r0, #FP_UI_SURFACE_WIDTH
    str r0, [r9, #SLOT_WIDTH]
    str r7, [r9, #SLOT_HEIGHT]

    add r0, r4, #UI_SLOTS_OFFSET
    sub r0, r9, r0
    mov r0, r0, lsr #5              @ slot number, bounded 0..2
    add r0, r0, r0, lsl #1
    constant r1, UI_PIXELS_OFFSET
    add r1, r4, r1
    add r1, r1, r0, lsl #12         @ two 6,144-byte planes per slot
    mov r2, #0
    cmp r8, #0
    beq submit_pixels
    add r2, r4, #UI_CANVAS_OFFSET
submit_pixels:
    constant r0, TILE_OFFSET
    add r0, r5, r0
    bl ui_pixels
    str r8, [r9, #SLOT_PAINTED]
submit_ready:
    mov r0, #0
    b submit_status
submit_slots_error:
    mov r0, #FP_UI_ESLOTS
    b submit_status
submit_identity_error:
    mov r0, #FP_UI_EIDENTITY
submit_status:
    str r0, [r4, #UI_ERROR]
    dmb
    mov r0, #0
    str r0, [r4, #UI_LOCK]
submit_return:
    add sp, sp, #16
    pop {r4, r5, r6, r7, r8, r9, r10, r11, ip, pc}

/* ui_pixels(tile address, background plane, canvas or NULL, was_painted).
 * Lock held. If firmware changed a previously painted pixel, that new value
 * is its background. Otherwise keep the older saved background. Restoration
 * writes ONLY pixels that still match last-paint, preserving native changes.
 * Row-local cache cleaning avoids full-cache work in the frame path.
 */
.global ui_pixels
ui_pixels:
    push {r4, r5, r6, r7, r8, r9, r10, r11, ip, lr}
    mov r4, r0
    mov r5, r1
    add r6, r1, #TILE_BYTES
    mov r7, r2
    mov r8, r3
    mov r9, #FP_UI_HEIGHT
pixels_row:
    mov r11, r4
    mov r10, #FP_UI_WIDTH
pixels_next:
    ldrh r0, [r4]
    cmp r7, #0
    beq pixels_restore
    cmp r8, #0
    beq pixels_save_native
    ldrh r1, [r6]
    cmp r0, r1
    beq pixels_paint
pixels_save_native:
    strh r0, [r5]
pixels_paint:
    ldrh r1, [r7], #2
    strh r1, [r6]
    strh r1, [r4]
    b pixels_advance
pixels_restore:
    ldrh r1, [r6]
    cmp r0, r1
    ldrheq r1, [r5]
    strheq r1, [r4]
pixels_advance:
    add r4, r4, #2
    add r5, r5, #2
    add r6, r6, #2
    subs r10, r10, #1
    bne pixels_next
    bic r0, r11, #31
    add r1, r11, #(FP_UI_WIDTH * 2)
pixels_clean:
    mcr p15, #0, r0, c7, c10, #1
    add r0, r0, #32
    cmp r0, r1
    blo pixels_clean
    add r4, r4, #(SURFACE_STRIDE - FP_UI_WIDTH * 2)
    subs r9, r9, #1
    bne pixels_row
    dsb
    pop {r4, r5, r6, r7, r8, r9, r10, r11, ip, pc}

.balign 4
.global ui_storage
ui_storage:
    .word 0
.global ui_allocation
ui_allocation:
    .space 16, 0
.global ui_storage_bytes
.equ ui_storage_bytes, UI_STORAGE_BYTES
.global ui_slots_offset
.equ ui_slots_offset, UI_SLOTS_OFFSET
.global ui_canvas_offset
.equ ui_canvas_offset, UI_CANVAS_OFFSET
.global ui_pixels_offset
.equ ui_pixels_offset, UI_PIXELS_OFFSET
.balign 4
.global module_end
module_end:
