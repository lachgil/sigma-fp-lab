.syntax unified
.arm
.text
/* False Color / EL Zone as a toggle on the button you already mapped, with the
 * camera's own EL Zone scale drawn across the bottom of live view.
 *
 * Self-contained: no menu, no key hook, no panel, nothing bound to RIGHT or UP.
 * The only button involved is the one the camera's own Custom Button
 * Functions menu already points at False Color.
 *
 *   press 1   mode on, and it stays on
 *   press 2   the scale appears
 *   press 3   both off
 *
 * The camera calls CameraIF vtable +0xCC on press and +0xD0 on release; both
 * are replaced outright (see docs/research/false-colour-el-zone.md).
 *
 * The scale is the firmware's own. Its band table (0xC0D1B74C: fifteen
 * x-ranges, each with a palette index), its 256-entry palette (0xC0D1B34C)
 * and its label glyphs are read out of the verified MAIN image by the card
 * builder, which writes them into fcscale_native.inc.S. Nothing about the
 * colours is invented here.
 *
 * The drawing surface is the LCD's indexed sub layer: one byte per pixel,
 * descriptor format 3, resolved through the display manager exactly as the
 * firmware's own FalseColorBarDrawer does (0xC057DA78). The palette is
 * installed through the drawable's own setter (vtable +0x18) when the scale
 * is switched on and restored (+0x1C) when the mode goes off. A palette file
 * loaded with `display osdPalette` cannot do this: that command keeps the
 * layer's existing entry count, so entries above the stock thirty never
 * exist -- which is why an earlier build drew nothing.
 *
 * Painting is done inside the firmware's own display submit: 0xC02E8A08 is
 * entered through fc_submit, which paints the scale into the exact buffer
 * being sent to the panel. The layer rotates three buffers; each one is
 * tracked with a drawn flag so a buffer carrying the scale is cleared once
 * when the scale goes away. A press presents one frame itself (back buffer,
 * rotate, submit: the shell's `display osd` sequence) so the change is
 * immediate rather than at the UI's next frame.
 *
 * State block at STATE:
 *   +0x00 0 off, 1 on, 2 on with the scale   +0x3C buffer being painted
 *   +0x04 presses                            +0x40 current colour
 *   +0x08 releases swallowed                 +0x44 our palette is installed
 *   +0x0C the scale flag                     +0x48 palette setter result
 *   +0x20 stride, while painting             +0x50..+0x58 palette descriptor
 *   +0x24 height, while painting             +0x60..+0x68 drawn flag per buffer
 *   +0x28..+0x30 the layer's three buffers   +0x6C paint lock
 *   +0x70 the LCD controller, from the hook  +0x74 submits seen
 *   +0x78 frames painted                     +0x7C next slot to evict
 */
.equ MEMSET,     0xC0015058
.equ POST,       0xC03A0798
.equ SUBMIT,     0xC02E8A08         /* submit(controller, descriptor, sub) */
.equ DRAW_MGR,   0xC0698D80         /* display manager; [r0+4]+0xC resolves a layer */
.equ SUB_LAYER,  1                  /* selector: LCD sub layer, the indexed one */
.equ FMT_INDEX8, 3                  /* descriptor format: one byte per pixel */
.equ UI_STATE,   0xC3033A44         /* 2 live view, 4 playback, 5 camera menu */
.equ UI_LIVEVIEW, 2
.equ CACHE_FN,   0xC000E91C
/* Where the firmware draws its own scale, in sub-layer pixels: the bar is
 * memset row 464 and copied down to row 582 (0xC057DC94, 0xC057DD1C); the
 * labels sit in the 25-row band above it. */
.equ SCALE_W,    1024
.equ BAR_Y,      464
.equ BAR_H,      119
.equ LABEL_Y,    430
.equ CLEAR_Y,    LABEL_Y
.equ CLEAR_H,    (BAR_Y + BAR_H - LABEL_Y)
.equ C_CLEAR,    0                  /* index 0 is transparent in both palettes */

#include "fcscale_native.inc.S"

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

/* Forward label addresses stay position independent. */
.macro PTR reg, label
    ldr \reg, 991f
990: add \reg, pc, \reg
    b 992f
991: .word \label - (990b + 8)
992:
.endm

/* ---------------------------------------------------------------- the hooks */

/* Replaces 0xC03722E8, the press. r0 = the CameraIF object. */
.global fc_press
fc_press:
    push    {r4, r5, lr}
    sub     sp, sp, #4              @ three registers: keep sp 8-aligned
    mov     r4, r0
    LDA     r5, STATE
    ldr     r1, [r5, #4]
    add     r1, r1, #1
    str     r1, [r5, #4]
    ldr     r0, [r5]
    add     r0, r0, #1
    cmp     r0, #3
    moveq   r0, #0
    str     r0, [r5]
    cmp     r0, #2                  @ the scale step: the mode is already on
    bne     1f
    mov     r1, #1
    str     r1, [r5, #0xC]
    mov     r0, #1
    bl      fc_palette
    bl      fc_present
    add     sp, sp, #4
    pop     {r4, r5, pc}
1:  mov     r1, #0
    str     r1, [r5, #0xC]
    cmp     r0, #0
    bne     2f
    bl      fc_present              @ take the scale down now
    mov     r0, #0
    bl      fc_palette              @ give the layer its palette back
    mov     r1, #0x22               @ off
    b       3f
2:  mov     r1, #0x21               @ on
3:  mov     r0, r4
    bl      fc_post
    add     sp, sp, #4
    pop     {r4, r5, pc}

/* Replaces 0xC0372330, the release: swallowed, so letting go does not cancel.
 * The stock method returns the post's result and callers read it as a success
 * flag, so this returns 1. */
.global fc_release
fc_release:
    LDA     r1, STATE
    ldr     r0, [r1, #8]
    add     r0, r0, #1
    str     r0, [r1, #8]
    mov     r0, #1
    bx      lr

/* r0 = the CameraIF object, r1 = event id. The stock method body, reproduced,
 * so nothing calls back into a patched method. */
fc_post:
    push    {r4, r5, lr}
    mov     r4, r0
    mov     r5, r1
    sub     sp, sp, #0xBC
    mov     r0, sp
    mov     r1, #0
    mov     r2, #0xBC
    LDA     r3, MEMSET
    blx     r3
    str     r5, [sp]
    mov     r1, #1
    strb    r1, [sp, #4]
    ldr     r0, [r4, #4]
    mov     r1, sp
    LDA     r3, POST
    blx     r3
    add     sp, sp, #0xBC
    pop     {r4, r5, pc}

/* ------------------------------------------------------------- the surfaces */

/* The sub layer's drawable, the way the firmware's own bar drawer gets it:
 * display manager, then its interface's slot +0xC with the layer selector.
 * Returns 0 if the manager is not up. */
sub_drawable:
    push    {r4, lr}
    LDA     ip, DRAW_MGR
    blx     ip
    cmp     r0, #0
    beq     9f
    ldr     r1, [r0, #4]
    ldr     r3, [r1, #0xC]
    mov     r1, #SUB_LAYER
    blx     r3
9:  pop     {r4, pc}

/* fc_palette(r0): 1 installs the firmware's 256-entry scale palette on the
 * sub layer through the drawable's own setter, 0 restores the layer's
 * default through its own restore. Both take the layer's mutex themselves.
 * Restoring when nothing was installed does nothing. */
fc_palette:
    push    {r4, r5, r6, lr}
    mov     r4, r0
    LDA     r6, STATE
    bl      sub_drawable
    cmp     r0, #0
    beq     9f
    mov     r5, r0
    ldr     r3, [r5]
    cmp     r4, #0
    beq     1f
    LDA     r1, NATIVE_PAL
    str     r1, [r6, #0x50]
    mov     r1, #NATIVE_PAL_N
    str     r1, [r6, #0x54]
    mov     r1, #0
    str     r1, [r6, #0x58]
    add     r1, r6, #0x50
    ldr     r3, [r3, #0x18]         @ setPalette(drawable, &descriptor)
    mov     r0, r5
    blx     r3
    str     r0, [r6, #0x48]
    mov     r0, #1
    str     r0, [r6, #0x44]
    b       9f
1:  ldr     r0, [r6, #0x44]
    cmp     r0, #0
    beq     9f
    ldr     r3, [r3, #0x1C]         @ restoreDefaultPalette(drawable)
    mov     r0, r5
    blx     r3
    mov     r0, #0
    str     r0, [r6, #0x44]
9:  pop     {r4, r5, r6, pc}

/* ------------------------------------------------------------- the painting */

/* Placed over the first instruction of 0xC02E8A08, the display submit:
 * submit(controller, descriptor, sub). Every buffer the UI sends to the panel
 * passes through here first, so the scale is painted into exactly the buffer
 * about to be shown, once per frame, with no thread and no cached buffer
 * addresses. The displaced push {r4, r5, r6, r7, fp, lr} is replayed and
 * control returns to the instruction after it. */
.global fc_submit
fc_submit:
    push    {r0, r1, r2, r3, ip, lr}
    bl      fc_on_submit
    pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, r7, fp, lr}
    LDA     ip, SUBMIT_RESUME
    bx      ip

/* fc_on_submit(controller, descriptor, sub). Acts only on a one-byte-per-pixel
 * descriptor of the scale's width and at least its height; anything else
 * (the 16-bit main layer, another monitor) is left alone. Remembers the
 * controller so a press can present a frame itself.
 *
 * Each of the layer's buffers is tracked with a drawn flag: the scale is
 * painted whenever it is wanted, and a buffer that carries it is cleared once
 * when it is not, so nothing of ours stays on a buffer the UI recycles. A
 * word at +0x6C serialises this against fc_present. */
fc_on_submit:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r8, STATE
    ldr     r3, [r8, #0x74]
    add     r3, r3, #1
    str     r3, [r8, #0x74]
    cmp     r1, #0
    beq     9f
    ldr     r3, [r1]
    cmp     r3, #FMT_INDEX8
    bne     9f
    ldr     r4, [r1, #4]            @ base
    cmp     r4, #0
    beq     9f
    ldr     r3, [r1, #8]
    cmp     r3, #0
    beq     9f
    ldr     r5, [r3]                @ width, which is the byte stride
    ldr     r6, [r3, #4]            @ height
    movw    ip, #SCALE_W
    cmp     r5, ip
    bne     9f
    movw    ip, #(CLEAR_Y + CLEAR_H)
    cmp     r6, ip
    blo     9f
    str     r0, [r8, #0x70]

    ldr     r0, [r8]                @ wanted: state 2, in live view
    cmp     r0, #2
    moveq   r7, #1
    movne   r7, #0
    LDA     r0, UI_STATE
    ldr     r0, [r0]
    cmp     r0, #UI_LIVEVIEW
    movne   r7, #0

    mov     r0, #0                  @ find this buffer's slot, or take one
1:  add     r1, r8, r0, lsl #2
    ldr     r2, [r1, #0x28]
    cmp     r2, r4
    beq     2f
    cmp     r2, #0
    beq     3f
    add     r0, r0, #1
    cmp     r0, #3
    blo     1b
    ldr     r0, [r8, #0x7C]         @ all three taken by others: evict in turn
    add     r2, r0, #1
    cmp     r2, #3
    moveq   r2, #0
    str     r2, [r8, #0x7C]
    add     r1, r8, r0, lsl #2
3:  str     r4, [r1, #0x28]
    mov     r2, #0
    str     r2, [r1, #0x60]
2:  ldr     r2, [r1, #0x60]         @ drawn on this buffer
    orrs    r2, r2, r7
    beq     9f                      @ nothing there and nothing wanted

    add     r3, r8, #0x6C           @ take the paint lock, or leave this frame.
    mov     r1, #1                  @ swp, which is what this firmware uses
    swp     r2, r1, [r3]            @ (106 sites, no ldrex/strex anywhere)
    cmp     r2, #0
    bne     9f
    dmb
    add     r1, r8, r0, lsl #2      @ the slot again; r1 was the swap value
    str     r4, [r8, #0x3C]
    str     r5, [r8, #0x20]
    str     r6, [r8, #0x24]
    mov     r6, r1                  @ the slot, across the calls below
    mov     r0, r4
    mov     r1, r7
    bl      draw_scale
    str     r7, [r6, #0x60]
    ldr     r0, [r8, #0x78]
    add     r0, r0, #1
    str     r0, [r8, #0x78]
    movw    r1, #CLEAR_Y            @ the panel reads memory: clean our rows
    mla     r0, r1, r5, r4          @ out of the data cache, and only ours --
    mov     r1, #CLEAR_H            @ the firmware's 0xC000E91C cleans every
    mul     r1, r5, r1              @ set and way of both levels with
    bl      clean_range             @ interrupts off, too much for every frame
    mov     r0, #0
    str     r0, [r8, #0x6C]
9:  pop     {r4, r5, r6, r7, r8, pc}

/* clean_range(address, length): data cache clean by address to the point of
 * coherency, 32-byte steps (a 64-byte line is simply cleaned twice). */
clean_range:
    add     r1, r0, r1
    bic     r0, r0, #31
1:  mcr     p15, #0, r0, c7, c10, #1
    add     r0, r0, #32
    cmp     r0, r1
    blo     1b
    dsb
    bx      lr

/* Present one frame of the sub layer from a press, so the scale changes at
 * the press and not at the UI's next frame: the layer's back buffer, rotated
 * and submitted, which is the shell's own `display osd` sequence. The submit
 * runs through fc_submit, which does the painting. Needs a controller seen
 * by the hook; before the first UI frame there is nothing to present to. */
fc_present:
    push    {r4, r5, r6, lr}
    LDA     r6, STATE
    ldr     r4, [r6, #0x70]
    cmp     r4, #0
    beq     9f
    bl      sub_drawable
    cmp     r0, #0
    beq     9f
    mov     r5, r0
    ldr     r3, [r5]
    ldr     r3, [r3, #0x10]         @ back descriptor
    mov     r0, r5
    blx     r3
    cmp     r0, #0
    beq     9f
    mov     r6, r0
    ldr     r3, [r5]
    ldr     r3, [r3, #0x14]         @ rotate: it becomes the front
    mov     r0, r5
    blx     r3
    mov     r0, r4
    mov     r1, r6
    mov     r2, #SUB_LAYER
    LDA     r3, SUBMIT
    blx     r3
9:  pop     {r4, r5, r6, pc}
/* draw_scale(base, wanted): clear the scale's rows, then draw it if wanted.
 * Refuses a surface the firmware's own geometry does not fit on. */
draw_scale:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    mov     r9, r1
    ldr     r7, [r10, #0x20]        @ stride
    movw    r0, #SCALE_W
    cmp     r7, r0
    blo     9f
    ldr     r8, [r10, #0x24]        @ height
    movw    r0, #(CLEAR_Y + CLEAR_H)
    cmp     r8, r0
    blo     9f

    mov     r0, #C_CLEAR
    str     r0, [r10, #0x40]
    mov     r0, #0
    movw    r1, #CLEAR_Y
    movw    r2, #SCALE_W
    mov     r3, #CLEAR_H
    bl      fill_rect
    cmp     r9, #0
    beq     9f

    PTR     r4, bands               @ {u16 x0, u16 x1, u8 index}, as the firmware's
    mov     r5, #BAND_N
1:  ldrh    r0, [r4]
    ldrh    r1, [r4, #2]
    ldrb    r2, [r4, #4]
    str     r2, [r10, #0x40]
    sub     r2, r1, r0
    add     r2, r2, #1              @ x1 is inclusive
    movw    r1, #BAR_Y
    mov     r3, #BAR_H
    bl      fill_rect
    add     r4, r4, #8
    subs    r5, r5, #1
    bne     1b

    mov     r0, #C_LABEL
    str     r0, [r10, #0x40]
    PTR     r4, labels              @ {u16 x, u8 glyph, u8 glyph or 0xFF}
    mov     r5, #LABEL_N
2:  ldrh    r0, [r4]
    movw    r1, #LABEL_Y
    ldrb    r2, [r4, #2]
    bl      draw_glyph
    ldrb    r2, [r4, #3]
    cmp     r2, #0xFF
    beq     3f
    ldrh    r0, [r4]
    add     r0, r0, #GLYPH_SIGN_W   @ the second glyph follows the sign
    movw    r1, #LABEL_Y
    bl      draw_glyph
3:  add     r4, r4, #4
    subs    r5, r5, #1
    bne     2b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* fill_rect(x, y, w, h) in the current colour.
 *
 * Refuses anything that would leave the buffer, and refuses entirely if no
 * buffer or geometry is known yet. This is the last line of defence and it
 * exists because it was missing: a chart drawn from a fixed origin, with no
 * check, wrote past the end of the buffer and froze the camera.
 */
fill_rect:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ stride
    ldr     r9, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r7, #0
    beq     9f
    cmp     r3, #0                  @ empty height or width: nothing to do
    beq     9f
    cmp     r2, #0
    beq     9f
    adds    r4, r0, r2              @ x + w must stay inside the stride
    bcs     9f
    cmp     r4, r7
    bhi     9f
    adds    r4, r1, r3              @ y + h must stay inside the height
    bcs     9f
    cmp     r4, r9
    bhi     9f
    ldr     r9, [r10, #0x40]        @ colour
    mov     r4, r0
    mov     r5, r1
    mov     r8, r2
1:  mla     ip, r5, r7, r6
    add     ip, ip, r4
    mov     lr, r8
2:  strb    r9, [ip], #1
    subs    lr, lr, #1
    bne     2b
    add     r5, r5, #1
    subs    r3, r3, #1
    bne     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* draw_glyph(x, y, glyph): one of the firmware's own label glyphs, in the
 * current colour. Each glyph is GLYPH_H rows of whole words, bit 0 the
 * leftmost pixel; set bits are drawn, clear bits leave the bar showing.
 * Refuses a glyph that would leave the buffer. */
draw_glyph:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ stride
    ldr     r8, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r2, #GLYPH_N
    bhs     9f
    PTR     r3, glyph_hdr           @ {u16 offset in words, u8 width, u8 words per row}
    add     r3, r3, r2, lsl #2
    ldrh    r4, [r3]
    ldrb    r5, [r3, #2]
    ldrb    r9, [r3, #3]
    add     ip, r0, r5
    cmp     ip, r7
    bhi     9f
    add     ip, r1, #GLYPH_H
    cmp     ip, r8
    bhi     9f
    PTR     r3, glyph_bits
    add     r3, r3, r4, lsl #2
    ldr     r8, [r10, #0x40]        @ colour
    mla     r6, r1, r7, r6
    add     r6, r6, r0              @ the glyph's top-left pixel
    mov     r4, #GLYPH_H
1:  mov     ip, r6
    mov     lr, r5                  @ pixels left in this row
    mov     r0, r9                  @ words left in this row
2:  ldr     r1, [r3], #4
    mov     r2, #32
3:  cmp     lr, #0
    beq     4f                      @ row complete; still consume its words
    tst     r1, #1
    strbne  r8, [ip]
    lsr     r1, r1, #1
    add     ip, ip, #1
    sub     lr, lr, #1
    subs    r2, r2, #1
    bne     3b
4:  subs    r0, r0, #1
    bne     2b
    add     r6, r6, r7
    subs    r4, r4, #1
    bne     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

.balign 4
thread_name:
    .asciz "fpFCsc"
    .balign 4
