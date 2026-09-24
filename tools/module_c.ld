/* Link at zero; the bootstrap adds the heap base to checked REL addends.
 * No ELF loader runs on camera. The builder materializes every allocated
 * section, including NOBITS zero data, in the ABI1 image.
 */
OUTPUT_FORMAT(elf32-littlearm)
OUTPUT_ARCH(arm)
SECTIONS
{
    . = 0;
    .fp.header : { KEEP(*(.fp.header)) }
    .text : ALIGN(4) { KEEP(*(.fp.bootstrap)) *(.text .text.*) }
    .rodata : ALIGN(4) { *(.rodata .rodata.*) }
    .data : ALIGN(8) { *(.data .data.*) }
    .got : ALIGN(4) { *(.got .got.*) }
    .dynamic : ALIGN(4) { *(.dynamic) }
    .dynsym : ALIGN(4) { *(.dynsym) }
    .dynstr : { *(.dynstr) }
    .hash : ALIGN(4) { *(.hash) }
    .gnu.hash : ALIGN(4) { *(.gnu.hash) }
    .rel.dyn : ALIGN(4)
    {
        HIDDEN(__fp_c_rel_start_offset = ABSOLUTE(.));
        *(.rel.dyn)
        HIDDEN(__fp_c_rel_end_offset = ABSOLUTE(.));
    }
    .bss : ALIGN(8) { *(.bss .bss.*) *(COMMON) }
    .fp.state : ALIGN(4)
    {
        HIDDEN(__fp_c_state_offset = ABSOLUTE(.));
        LONG(0);
    }
    . = ALIGN(4);
    HIDDEN(__fp_c_image_bytes = ABSOLUTE(.));

    /* No unwinder is supplied. Clang emits .ARM.exidx even for C functions
     * built with -fno-unwind-tables; those cantunwind entries are unnecessary.
     */
    /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .note.GNU-stack) }
}
ASSERT(ADDR(.fp.header) == 0 && SIZEOF(.fp.header) == 24,
       "ABI1 requires the header at offset zero")
ASSERT(__fp_c_initialize == 24, "C bootstrap must immediately follow the header")
ASSERT(__fp_c_image_bytes < 0x1000000, "C module image exceeds 16 MiB")
