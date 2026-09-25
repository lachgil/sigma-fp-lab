.syntax unified
.arm
.text
#include "module_abi.h"

/* Experimental fp 5.02 boot-only runtime. Entry executes from upstream's
 * temporary staging allocation; only resident..resident_end survives boot.
 * All allocator/cache/clock addresses belong here, never in module artifacts.
 * Calls and initialization are serialized; trusted modules obey ARM AAPCS.
 */
.equ H_GET,       0xC001D740
.equ H_ADDR,      0xC001D7F0
.equ H_FREE,      0xC001D7A0
.equ F_DCACHE,    0xC000E91C
.equ F_ICACHE,    0xC000EABC
.equ F_TICK_US,   0xC002B6E0
.equ F_POWEROFF_MGR, 0xC0023A98
.equ F_POWEROFF_REGISTER, 0xC0024118

/* Private cave layout. Nothing below HC_CODE dereferences USER memory. */
.equ HC_MAGIC, 16
.equ HC_STATE, 20
.equ HC_API, 24
.equ HC_OBJECT, 28
.equ HC_VTABLE, 32
.equ HC_CODE, 48
.equ HC_TAG, 0x314B4846
.equ HC_INERT, 0
.equ HC_LIVE, 1
.equ HC_CLOSED, 2
.equ HE_SITE, 0
.equ HE_ORIGINAL, 4
.equ HE_BRANCH, 8
.equ HE_OWNER, 12
.equ HE_VENEER, 16
.equ HE_TARGET, 20
.equ HE_SIZE, 24

.macro constant reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.macro call_fw address
    constant ip, \address
    blx ip
.endm

/* Label differences assemble to constants, not absolute-address relocations.
 * At the add instruction ARM's PC is this local label plus sixteen bytes.
 */
.macro pc_address reg, target
.Laddress\@:
    movw \reg, #:lower16:(\target - .Laddress\@ - 16)
    movt \reg, #:upper16:(\target - .Laddress\@ - 16)
    add \reg, pc, \reg
.endm

.macro instance_address reg, base, target
    movw \reg, #:lower16:(\target - resident)
    movt \reg, #:upper16:(\target - resident)
    add \reg, \base, \reg
.endm

/* Reject closed and previous-boot APIs before dereferencing caller memory. */
.macro require_live failure
    cmp r0, #0
    beq \failure
    pc_address ip, lifecycle_root
    ldr ip, [ip]
    cmp ip, #0
    beq \failure
    ldr ip, [ip, #HC_STATE]
    cmp ip, #HC_LIVE
    bne \failure
    pc_address ip, lifecycle_root
    ldr ip, [ip]
    ldr ip, [ip, #HC_API]
    cmp r0, ip
    bne \failure
.endm

.global entry
entry:
    push {r4, r5, r6, lr}
    sub sp, sp, #16                  @ private runtime allocation descriptor

    /* The cleanup object is cave-resident and published before either native
     * registration. Failure never releases or overwrites registered storage.
     */
    bl hooks_prepare
    mov r4, r0
    cmp r4, #0
    beq boot_no_directory
    mov r0, #0
    str r0, [sp]
    str r0, [sp, #4]
    str r0, [sp, #8]
    str r0, [sp, #12]

    mov r0, sp
    mov r1, #0                      @ USER, not RAW/recording buffers
    constant r2, (resident_end - resident)
    mov r3, #0
    call_fw H_GET
    mov r0, sp
    call_fw H_ADDR
    mov r5, r0
    cmp r5, #0
    beq boot_no_memory              @ no owned allocation: do not call H_FREE
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_LIVE
    bne boot_no_directory           @ shutdown during allocation: retain, do not init

    pc_address r1, resident
    mov r2, r5
    constant r3, (resident_end - resident)
boot_copy:
    ldr r0, [r1], #4
    str r0, [r2], #4
    subs r3, r3, #4
    bne boot_copy
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    mrs r6, cpsr
    cpsid if
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_LIVE
    bne boot_publish_closed
    instance_address r0, r5, lifecycle_root
    str r4, [r0]
    str r5, [r4, #HC_API]
    msr cpsr_c, r6

    mov r0, r5
    pc_address r1, catalog
    instance_address ip, r5, initialize
    blx ip
    cmp r0, #FP_ENOTREADY
    beq boot_no_directory           @ some module code may already be in flight
    cmp r0, #FP_OK
    bne boot_bad_catalog
    /* Shutdown may have revoked startup while module initialization ran.
     * Successful images are not freed: a callback can still be in flight.
     */
    mrs r6, cpsr
    cpsid if
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_LIVE
    bne boot_publish_closed

    /* No staging pointer is published. Status becomes ready only after all
     * records, service pointers, module copies and init results are final.
     */
    dsb sy
    str r5, [r4, #DIR_RUNTIME]
    constant r0, (resident_end - resident)
    str r0, [r4, #DIR_BYTES]
    mov r0, #FP_OK
    dsb sy
    str r0, [r4, #DIR_STATUS]
    mov r0, r5
    msr cpsr_c, r6
    b boot_return
boot_publish_closed:
    msr cpsr_c, r6
    b boot_no_directory

boot_bad_catalog:
    mov r6, r0
    add r0, r4, #HC_OBJECT
    add ip, r4, #(cave_cleanup - cave_template)
    blx ip
    mov r0, sp
    mov r1, #2
    call_fw H_FREE                  @ executing staging, not the freed runtime
    str r6, [r4, #DIR_STATUS]
    b boot_no_directory

boot_no_memory:
    add r0, r4, #HC_OBJECT
    add ip, r4, #(cave_cleanup - cave_template)
    blx ip
    mov r0, #FP_ENOMEM
    str r0, [r4, #DIR_STATUS]
boot_no_directory:
    mov r0, #0
boot_return:
    add sp, sp, #16
    pop {r4, r5, r6, pc}

.balign 4
.global resident
resident:
    .word FP_RUNTIME_MAGIC
    .word FP_ABI
    .word API_SIZE
    .word 0                         @ API_COUNT
    .word 0                         @ API_RECORDS, fixed up after copying
    .word 0                         @ API_LOOKUP
    .word 0                         @ API_CALL
    .word 0                         @ API_REPORT
    .word 0                         @ API_TICKS
    .word 0                         @ API_INSTALL_HOOKS
.global lifecycle_root
lifecycle_root:
    .word 0                         @ cave pointer; never read by cave cleanup
.global records
records:
    .space (FP_CAPACITY * REC_SIZE), 0

/* initialize(resident API, staging catalog) -> status. Validate the entire
 * table envelope before creating any module allocation. An invalid individual
 * extent instead creates an ID-zero failed record, without reading its header.
 * Catalog offsets and lengths are checked by subtraction, never off+len.
 */
.global initialize
initialize:
    push {r4, r5, r6, r7, r8, r9, r10, lr}
    mov r4, r0
    mov r5, r1
    ldr r6, [r5]                    @ count
    ldr r7, [r5, #4]                @ total catalog bytes, including table
    cmp r6, #FP_CAPACITY
    bhi catalog_invalid
    tst r7, #3
    bne catalog_invalid
    mov r0, r6, lsl #3              @ count was bounded before multiplication
    add r0, r0, #8
    cmp r7, r0
    blo catalog_invalid
    adds r0, r5, r7                 @ a catalog cannot wrap address space
    bcs catalog_invalid

    instance_address r8, r4, records
    str r8, [r4, #API_RECORDS]
    instance_address r0, r4, service_lookup
    str r0, [r4, #API_LOOKUP]
    instance_address r0, r4, service_call
    str r0, [r4, #API_CALL]
    instance_address r0, r4, service_report
    str r0, [r4, #API_REPORT]
    instance_address r0, r4, service_ticks
    str r0, [r4, #API_TICKS]
    instance_address r0, r4, service_install_hooks
    str r0, [r4, #API_INSTALL_HOOKS]
    mov r10, #0
    str r10, [r4, #API_COUNT]
    add r9, r5, #8

catalog_next:
    mov r0, r4
    require_live catalog_closed    @ initialization must not continue after revocation
    cmp r10, r6
    bhs catalog_done
    ldr r2, [r9]                    @ offset relative to catalog, not runtime
    ldr r3, [r9, #4]
    mov r0, #FP_INITIALIZING
    str r0, [r8, #REC_STATUS]
    add r10, r10, #1
    str r10, [r4, #API_COUNT]        @ own record visible to report during init

    tst r2, #3
    bne catalog_bad_extent
    tst r3, #3
    bne catalog_bad_extent
    cmp r3, #MOD_HEADER_BYTES
    blo catalog_bad_extent
    mov r0, r6, lsl #3
    add r0, r0, #8
    cmp r2, r0                      @ modules may not overlap the catalog table
    blo catalog_bad_extent
    cmp r2, r7
    bhi catalog_bad_extent
    sub r0, r7, r2
    cmp r3, r0
    bhi catalog_bad_extent

    add r2, r5, r2                  @ safe because catalog+total did not wrap
    mov r0, r4
    mov r1, r8
    bl module_load
    b catalog_advance

catalog_bad_extent:
    mov r0, #FP_EFORMAT             @ copied zero block retains ID=0, no pointers
    str r0, [r8, #REC_STATUS]
catalog_advance:
    add r8, r8, #REC_SIZE
    add r9, r9, #8
    b catalog_next

catalog_done:
    mov r0, #FP_OK                  @ module failures do not fail the directory
    pop {r4, r5, r6, r7, r8, r9, r10, pc}
catalog_invalid:
    mov r0, #FP_EFORMAT
    pop {r4, r5, r6, r7, r8, r9, r10, pc}
catalog_closed:
    mov r0, #FP_ENOTREADY
    pop {r4, r5, r6, r7, r8, r9, r10, pc}

/* module_load(api, record, bounded source, bytes) -> record status.
 * This source has a complete aligned header. Preserve the ID even when header
 * validation fails; an earlier nonzero ID reserves its name, including errors.
 */
module_load:
    push {r4, r5, r6, r7, r8, r9, r10, lr}
    mov r4, r0
    mov r5, r1
    mov r6, r2
    mov r7, r3
    require_live module_not_ready
    ldr r1, [r6, #MOD_ID]
    str r1, [r5, #REC_ID]
    cmp r1, #0
    beq module_bad_format
    bl service_lookup              @ count already includes this record
    cmp r0, r5
    bne module_duplicate

    ldr r0, [r6, #MOD_MAGIC]
    constant r1, FP_MODULE_MAGIC
    cmp r0, r1
    bne module_bad_format
    ldr r0, [r6, #MOD_ABI]
    cmp r0, #FP_ABI
    bne module_bad_abi
    ldr r0, [r6, #MOD_BYTES]
    cmp r0, r7
    bne module_bad_format
    ldr r9, [r6, #MOD_INIT]
    ldr r10, [r6, #MOD_INVOKE]
    tst r9, #3
    bne module_bad_format
    tst r10, #3
    bne module_bad_format
    cmp r9, #MOD_HEADER_BYTES
    blo module_bad_format
    cmp r10, #MOD_HEADER_BYTES
    blo module_bad_format
    cmp r9, r7
    bhs module_bad_format
    cmp r10, r7
    bhs module_bad_format

    add r0, r5, #REC_ALLOC          @ descriptor was zeroed by resident copy
    mov r1, #0
    mov r2, r7
    mov r3, #0
    call_fw H_GET
    add r0, r5, #REC_ALLOC
    call_fw H_ADDR
    mov r8, r0
    cmp r8, #0
    beq module_no_memory

    mov r0, r6
    mov r1, r8
    mov r2, r7
module_copy:
    ldr r3, [r0], #4
    str r3, [r1], #4
    subs r2, r2, #4
    bne module_copy
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    mov r0, r4
    require_live module_not_ready  @ shutdown in allocator/cache calls forbids init

    str r8, [r5, #REC_IMAGE]
    str r7, [r5, #REC_BYTES]
    add r9, r8, r9
    add r10, r8, r10
    str r9, [r5, #REC_INIT]
    str r10, [r5, #REC_INVOKE]
    mov r0, #FP_INITIALIZING
    str r0, [r5, #REC_STATUS]
    mov r0, r4
    mov r1, r5
    blx r9
    str r0, [r5, #REC_INIT_RESULT]
    cmp r0, #0
    bne module_init_failed
    mov r0, r4
    require_live module_not_ready
    mov r0, #FP_OK
    b module_set_status

module_init_failed:
    mov r0, #FP_EINIT
    str r0, [r5, #REC_STATUS]        @ make the failed module uncallable first
    /* Detach before considering free. A published hook can already have a
     * suspended callback, so its image and retired veneer survive this boot.
     */
    pc_address r0, lifecycle_root
    ldr r0, [r0]
    add ip, r0, #(cave_detach_owner - cave_template)
    add r0, r0, #HC_OBJECT
    mov r1, r5
    blx ip
    mov r0, #0
    str r0, [r5, #REC_IMAGE]
    str r0, [r5, #REC_BYTES]
    str r0, [r5, #REC_INIT]
    str r0, [r5, #REC_INVOKE]
    cmp r1, #0                     @ cave detach reports current OR retired ownership
    bne module_return              @ keep descriptor/image allocation for in-flight code
    add r0, r5, #REC_ALLOC
    mov r1, #2
    call_fw H_FREE
    b module_clear_descriptor

module_no_memory:
    mov r0, #FP_ENOMEM              @ H_ADDR refused: no owned allocation to free
    str r0, [r5, #REC_STATUS]
module_clear_descriptor:
    mov r0, #0
    str r0, [r5, #REC_ALLOC]
    str r0, [r5, #(REC_ALLOC + 4)]
    str r0, [r5, #(REC_ALLOC + 8)]
    str r0, [r5, #(REC_ALLOC + 12)]
    b module_return

module_not_ready:
    mov r0, #FP_ENOTREADY
    b module_set_status

module_duplicate:
    mov r0, #FP_EDUPLICATE
    b module_set_status
module_bad_abi:
    mov r0, #FP_EABI
    b module_set_status
module_bad_format:
    mov r0, #FP_EFORMAT
module_set_status:
    str r0, [r5, #REC_STATUS]
module_return:
    ldr r0, [r5, #REC_STATUS]
    pop {r4, r5, r6, r7, r8, r9, r10, pc}

/* lookup(api, id) -> first record, including failed records; ID zero and
 * absent IDs return NULL. Leaf helper deliberately uses caller-save regs only.
 */
.global service_lookup
service_lookup:
    require_live lookup_missing
    cmp r1, #0
    beq lookup_missing
    ldr r2, [r0, #API_COUNT]
    ldr r0, [r0, #API_RECORDS]
lookup_next:
    cmp r2, #0
    beq lookup_missing
    ldr r3, [r0, #REC_ID]
    cmp r3, r1
    bxeq lr
    add r0, r0, #REC_SIZE
    sub r2, r2, #1
    b lookup_next
lookup_missing:
    mov r0, #0
    bx lr

/* call(api, id, argument) -> r0 status, r1 value. Keep API/argument across
 * lookup and the module callback; failed/init records cannot expose callbacks.
 */
.global service_call
service_call:
    push {r4, r5, r6, lr}
    mov r4, r0
    mov r5, r2
    require_live call_not_ready
    bl service_lookup
    cmp r0, #0
    beq call_missing
    ldr r1, [r0, #REC_STATUS]
    cmp r1, #FP_OK
    bne call_not_ready
    mov r1, r0
    ldr ip, [r1, #REC_INVOKE]
    mov r0, r4
    mov r2, r5
    blx ip
    mov r1, r0
    mov r0, #FP_OK
    pop {r4, r5, r6, pc}
call_missing:
    mov r0, #FP_ENOENT
    b call_failure
call_not_ready:
    mov r0, #FP_ENOTREADY
call_failure:
    mov r1, #0
    pop {r4, r5, r6, pc}

/* report(api, id, value) -> status. Initialization can publish a diagnostic
 * before succeeding; a failed init retains that value and its init result.
 */
.global service_report
service_report:
    push {r4, lr}
    mov r4, r2
    require_live report_not_ready
    bl service_lookup
    cmp r0, #0
    beq report_missing
    ldr r1, [r0, #REC_STATUS]
    cmp r1, #FP_OK
    beq report_store
    cmp r1, #FP_INITIALIZING
    bne report_not_ready
report_store:
    str r4, [r0, #REC_VALUE]
    mov r0, #FP_OK
    pop {r4, pc}
report_missing:
    mov r0, #FP_ENOENT
    pop {r4, pc}
report_not_ready:
    mov r0, #FP_ENOTREADY
    pop {r4, pc}

/* ticks(api) -> wrapping uint32 microseconds. The API contains this resident
 * wrapper, not a staging address; the firmware clock returns directly to LR.
 */
.global service_ticks
service_ticks:
    require_live ticks_not_ready
    constant ip, F_TICK_US
    bx ip
ticks_not_ready:
    mov r0, #FP_ENOTREADY
    bx lr

#include "module_hooks.S"
.balign 4
.global resident_end
resident_end:

/* The packager replaces these two words and appends its bounded module table
 * and artifacts. Nothing from catalog onward is part of the resident copy.
 */
.global catalog
catalog:
    .word 0
    .word 0
