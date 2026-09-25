/* Included by module_runtime.S. Only cave_template..cave_end is retained in
 * firmware RAM. Its callback and owner-detach entry never touch a heap pointer.
 */
.global hooks_prepare
hooks_prepare:
    push {r4-r10, lr}
    constant r0, FP_CAVE_BUMP
    ldr r4, [r0]
    constant r1, FP_CAVE_BEGIN
    cmp r4, r1
    blo hooks_prepare_refuse
    tst r4, #3
    bne hooks_prepare_refuse
    constant r2, (cave_end - cave_template)
    adds r7, r4, r2
    bcs hooks_prepare_refuse
    constant r1, FP_CAVE_END
    cmp r7, r1
    bhi hooks_prepare_refuse

    /* An all-zero extent is pristine. Otherwise accept our exact CLOSED
     * code/layout only: a magic alone is never a lifetime or identity proof.
     */
    mov r5, r4
hooks_zero_check:
    ldr r0, [r5], #4
    cmp r0, #0
    bne hooks_retained_check
    cmp r5, r7
    blo hooks_zero_check
    b hooks_check_lists
hooks_retained_check:
    ldr r0, [r4, #DIR_MAGIC]
    constant r1, FP_DIRECTORY_MAGIC
    cmp r0, r1
    bne hooks_prepare_refuse
    ldr r0, [r4, #HC_MAGIC]
    constant r1, HC_TAG
    cmp r0, r1
    bne hooks_prepare_refuse
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_CLOSED
    bne hooks_prepare_refuse
    ldr r0, [r4, #DIR_RUNTIME]
    ldr r1, [r4, #DIR_BYTES]
    orr r0, r0, r1
    ldr r1, [r4, #HC_API]
    orrs r0, r0, r1
    bne hooks_prepare_refuse
    ldr r0, [r4, #HC_OBJECT]
    add r1, r4, #HC_VTABLE
    cmp r0, r1
    bne hooks_prepare_refuse
    ldr r0, [r4, #(HC_VTABLE + 12)]
    add r1, r4, #(cave_cleanup - cave_template)
    cmp r0, r1
    bne hooks_prepare_refuse
    add r5, r4, #HC_VTABLE
    mov r6, #3
hooks_vtable_check:
    ldr r0, [r5], #4
    cmp r0, #0
    bne hooks_prepare_refuse
    subs r6, r6, #1
    bne hooks_vtable_check
    pc_address r6, cave_cleanup
    add r5, r4, #HC_CODE
    constant r9, (cave_table - cave_template)
    add r9, r4, r9
hooks_identity_check:
    ldr r0, [r5], #4
    ldr r1, [r6], #4
    cmp r0, r1
    bne hooks_prepare_refuse
    cmp r5, r9
    blo hooks_identity_check
    /* Retired veneers stay executable until the next boot. CLOSED rows have
     * no active site; their exact opcode and remaining dynamic words must
     * still have the shape produced by our own detach operation.
     */
hooks_retired_check:
    ldr r0, [r5, #HE_SITE]
    ldr r1, [r5, #HE_ORIGINAL]
    orr r0, r0, r1
    ldr r1, [r5, #HE_BRANCH]
    orrs r0, r0, r1
    bne hooks_prepare_refuse
    ldr r0, [r5, #HE_OWNER]
    ldr r1, [r5, #HE_VENEER]
    ldr r2, [r5, #HE_TARGET]
    cmp r0, #0
    bne hooks_retired_used
    orrs r1, r1, r2
    bne hooks_prepare_refuse
    b hooks_retired_next
hooks_retired_used:
    orr r0, r0, r2
    tst r0, #3
    bne hooks_prepare_refuse
    cmp r2, #0
    beq hooks_prepare_refuse
    constant r0, 0xe51ff004
    cmp r1, r0
    bne hooks_prepare_refuse
hooks_retired_next:
    add r5, r5, #HE_SIZE
    cmp r5, r7
    blo hooks_retired_check

hooks_check_lists:
    call_fw F_POWEROFF_MGR
    mov r8, r0
    mrs r10, cpsr
    cpsid if
    /* BSS must have reset before reclamation. Even a CLOSED block remains
     * owned by both lists during dispatch; never overwrite it in that boot.
     */
    add r5, r8, #0x0c
    add r6, r8, #0x5c
hooks_list_check:
    ldr r0, [r5], #4
    cmp r0, r4
    blo hooks_list_next
    cmp r0, r7
    blo hooks_prepare_unlock_refuse
hooks_list_next:
    cmp r5, r6
    blo hooks_list_check

    constant r0, FP_CAVE_BUMP
    str r7, [r0]
    mov r5, r4
    pc_address r6, cave_template
hooks_template_copy:
    ldr r0, [r6], #4
    str r0, [r5], #4
    cmp r5, r7
    blo hooks_template_copy
    add r0, r4, #HC_VTABLE
    str r0, [r4, #HC_OBJECT]
    add r0, r4, #(cave_cleanup - cave_template)
    str r0, [r4, #(HC_VTABLE + 12)]
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    msr cpsr_c, r10

    mov r0, r8
    add r1, r4, #HC_OBJECT
    mov r2, #0
    call_fw F_POWEROFF_REGISTER
    mov r9, r0
    mov r0, r8
    add r1, r4, #HC_OBJECT
    mov r2, #1
    call_fw F_POWEROFF_REGISTER
    mrs r10, cpsr
    cpsid if
    cmp r9, #1
    cmpeq r0, #1
    bne hooks_registration_failed
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_INERT
    bne hooks_prepare_unlock_refuse @ a re-entrant callback already closed it
    mov r0, #HC_LIVE
    str r0, [r4, #HC_STATE]
    mov r0, r4
    msr cpsr_c, r10
    pop {r4-r10, pc}
hooks_registration_failed:
    /* Partial registration retains a valid INERT object, never reclaimable
     * in this boot. Do not overwrite revocation by a re-entrant callback.
     */
    ldr r0, [r4, #HC_STATE]
    cmp r0, #HC_INERT
    moveq r0, #FP_EREGISTER
    streq r0, [r4, #DIR_STATUS]
hooks_prepare_unlock_refuse:
    msr cpsr_c, r10
hooks_prepare_refuse:
    mov r0, #0
    pop {r4-r10, pc}

.global service_install_hooks
service_install_hooks:
    push {r4-r11, ip, lr}
    sub sp, sp, #(FP_HOOK_CAPACITY * 4 + 8)
    mrs ip, cpsr
    str ip, [sp, #(FP_HOOK_CAPACITY * 4)]
    cpsid if                       @ validation, ownership and publication atomic
    require_live hooks_not_ready
    mov r4, r0
    mov r5, r1
    mov r6, r2
    mov r7, r3
    cmp r5, #0
    beq hooks_bad_format
    cmp r6, #0
    beq hooks_bad_format
    orr r0, r5, r6
    tst r0, #3
    bne hooks_bad_format
    cmp r7, #1
    blo hooks_bad_format
    cmp r7, #FP_HOOK_CAPACITY
    bhi hooks_bad_format
    adds r0, r6, r7, lsl #4
    bcs hooks_bad_format

    /* Compare record addresses before reading the owner supplied by a caller. */
    ldr r0, [r4, #API_RECORDS]
    ldr r1, [r4, #API_COUNT]
hooks_owner_next:
    cmp r1, #0
    beq hooks_bad_format
    cmp r0, r5
    beq hooks_owner_found
    add r0, r0, #REC_SIZE
    subs r1, r1, #1
    b hooks_owner_next
hooks_owner_found:
    ldr r0, [r5, #REC_STATUS]
    cmp r0, #FP_OK
    cmpne r0, #FP_INITIALIZING
    bne hooks_not_ready
    pc_address r8, lifecycle_root
    ldr r8, [r8]
    constant r11, (cave_table - cave_template)
    add r11, r8, r11
    mov r9, #0
    mov r10, #0
hooks_find_slots:
    ldr r0, [r11, #HE_OWNER]        @ retired slots cannot overwrite in-flight code
    cmp r0, #0
    streq r11, [sp, r10, lsl #2]
    addeq r10, r10, #1
    add r11, r11, #HE_SIZE
    add r9, r9, #1
    cmp r9, #FP_HOOK_CAPACITY
    blo hooks_find_slots
    cmp r10, r7
    blo hooks_no_space

    mov r9, #0
hooks_validate:
    add r10, r6, r9, lsl #4
    ldr r0, [r10, #HOOK_SITE]
    ldr r1, [r10, #HOOK_TARGET]
    orr r2, r0, r1
    tst r2, #3
    bne hooks_bad_format
    constant r2, 0xC0000000
    cmp r0, r2
    blo hooks_bad_format
    constant r2, 0xC2F30CC8       @ exclusive end of complete fp5.02 image words
    cmp r0, r2
    bhs hooks_bad_format
    constant r2, FP_CAVE_BUMP
    cmp r0, r2
    blo hooks_site_outside_cave
    constant r2, FP_CAVE_END
    cmp r0, r2
    blo hooks_bad_format
hooks_site_outside_cave:
    ldr r2, [r5, #REC_IMAGE]
    cmp r1, r2
    blo hooks_bad_format
    sub r1, r1, r2
    cmp r1, #MOD_HEADER_BYTES
    blo hooks_bad_format
    ldr r2, [r5, #REC_BYTES]
    cmp r1, r2
    bhs hooks_bad_format
    /* A B instruction has a signed 26-bit byte displacement. */
    ldr r2, [sp, r9, lsl #2]
    add r2, r2, #(HE_VENEER - 8)
    sub r2, r2, r0
    mov r1, r2, lsl #6
    mov r1, r1, asr #6
    cmp r1, r2
    bne hooks_bad_format
    ldr r1, [r0]
    ldr r2, [r10, #HOOK_ORIGINAL]
    cmp r1, r2
    bne hooks_conflict
    mov r1, #0
hooks_duplicate_check:
    cmp r1, r9
    bhs hooks_owned_check_start
    ldr r2, [r6, r1, lsl #4]
    cmp r0, r2
    beq hooks_conflict
    add r1, r1, #1
    b hooks_duplicate_check
hooks_owned_check_start:
    constant r11, (cave_table - cave_template)
    add r11, r8, r11
    mov r1, #FP_HOOK_CAPACITY
hooks_owned_check:
    ldr r2, [r11, #HE_SITE]
    cmp r0, r2
    beq hooks_conflict
    add r11, r11, #HE_SIZE
    subs r1, r1, #1
    bne hooks_owned_check
    add r9, r9, #1
    cmp r9, r7
    blo hooks_validate

    /* All checks precede every mutation. Save ownership, restore words and
     * fully formed LR-preserving veneers before publishing any branch. */
    mov r9, #0
hooks_save:
    add r10, r6, r9, lsl #4
    ldr r11, [sp, r9, lsl #2]
    ldr r0, [r10, #HOOK_SITE]
    str r0, [r11, #HE_SITE]
    ldr r1, [r10, #HOOK_ORIGINAL]
    str r1, [r11, #HE_ORIGINAL]
    str r5, [r11, #HE_OWNER]
    add r1, r11, #HE_VENEER
    sub r1, r1, r0
    sub r1, r1, #8
    mov r1, r1, asr #2
    bic r1, r1, #0xff000000
    orr r1, r1, #0xea000000
    str r1, [r11, #HE_BRANCH]
    constant r0, 0xe51ff004
    str r0, [r11, #HE_VENEER]
    ldr r0, [r10, #HOOK_TARGET]
    str r0, [r11, #HE_TARGET]
    add r9, r9, #1
    cmp r9, r7
    blo hooks_save
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    mov r9, #0
hooks_publish:
    ldr r11, [sp, r9, lsl #2]
    ldr r0, [r11, #HE_SITE]
    ldr r1, [r11, #HE_BRANCH]
    str r1, [r0]
    add r9, r9, #1
    cmp r9, r7
    blo hooks_publish
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    mov r9, #0
hooks_outputs:
    ldr r0, [sp, r9, lsl #2]
    add r0, r0, #HE_VENEER
    add r1, r6, r9, lsl #4
    str r0, [r1, #HOOK_VENEER]
    add r9, r9, #1
    cmp r9, r7
    blo hooks_outputs
    mov r0, #FP_OK
    b hooks_return
hooks_bad_format:
    mov r0, #FP_EFORMAT
    b hooks_return
hooks_not_ready:
    mov r0, #FP_ENOTREADY
    b hooks_return
hooks_conflict:
    mov r0, #FP_ECONFLICT
    b hooks_return
hooks_no_space:
    mov r0, #FP_ENOSPACE
hooks_return:
    ldr ip, [sp, #(FP_HOOK_CAPACITY * 4)]
    msr cpsr_c, ip
    add sp, sp, #(FP_HOOK_CAPACITY * 4 + 8)
    pop {r4-r11, ip, pc}

.balign 4
.global cave_template
cave_template:
    .word FP_DIRECTORY_MAGIC, 0, 0, FP_INITIALIZING
    .word HC_TAG, HC_INERT, 0, 0
    .word 0, 0, 0, 0               @ vtable: native dispatcher calls slot +12
.global cave_cleanup
cave_cleanup:
    push {r4-r8, lr}
    mrs r8, cpsr
    cpsid if
    sub r4, r0, #HC_OBJECT
    mov r0, #HC_CLOSED
    str r0, [r4, #HC_STATE]         @ close every service before touching hooks
    mov r0, #0
    str r0, [r4, #DIR_RUNTIME]
    str r0, [r4, #DIR_BYTES]
    str r0, [r4, #HC_API]
    mov r0, #FP_ENOTREADY
    str r0, [r4, #DIR_STATUS]
    mov r5, #0                     @ all owners; native r1 is a reason, not owner
    b cave_detach_begin
.global cave_detach_owner
cave_detach_owner:
    push {r4-r8, lr}
    mrs r8, cpsr
    cpsid if
    mov r5, r1                     @ compare only; never dereference the owner
cave_detach_begin:
    mov r4, #0                     @ r1 result: owner ever exposed executable code
    pc_address r6, cave_table
    mov r7, #FP_HOOK_CAPACITY
cave_detach_next:
    ldr r1, [r6, #HE_OWNER]
    cmp r1, #0
    beq cave_detach_advance
    cmp r5, #0
    cmpne r1, r5
    bne cave_detach_advance
    mov r4, #1
    ldr r0, [r6, #HE_SITE]
    cmp r0, #0
    beq cave_detach_advance
    ldr r1, [r0]
    ldr r2, [r6, #HE_BRANCH]
    cmp r1, r2
    ldreq r1, [r6, #HE_ORIGINAL]
    streq r1, [r0]                 @ a replacement owner's word is left alone
    mov r0, #0
    str r0, [r6, #HE_SITE]
    str r0, [r6, #HE_ORIGINAL]
    str r0, [r6, #HE_BRANCH]
    /* Keep owner, veneer and target until the next proven boot. A callback
     * may be suspended at the veneer or inside its module during shutdown.
     */
cave_detach_advance:
    add r6, r6, #HE_SIZE
    subs r7, r7, #1
    bne cave_detach_next
    call_fw F_DCACHE
    call_fw F_ICACHE
    dsb sy
    isb sy
    mov r0, #FP_OK
    mov r1, r4
    msr cpsr_c, r8
    pop {r4-r8, pc}
.global cave_table
cave_table:
    .space (FP_HOOK_CAPACITY * HE_SIZE), 0
.global cave_end
cave_end:
.if (cave_end - cave_template) > (FP_CAVE_END - FP_CAVE_BEGIN)
.error "shared hook block exceeds boot cave arena"
.endif
