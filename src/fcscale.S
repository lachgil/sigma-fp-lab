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
 * The layer rotates three buffers and the UI paints over whatever it finds,
 * so the press handler learns the buffers once (it runs in key context, the
 * same context the camera's own function key runs in) and a drawing thread
 * repaints every 150 ms into every buffer it has seen.
 *
 * State block at STATE:
 *   +0x00 0 off, 1 on, 2 on with the scale   +0x20 stride
 *   +0x04 presses                            +0x24 height
 *   +0x08 releases swallowed                 +0x28..+0x30 the three OSD buffers
 *   +0x0C the scale flag                     +0x34 thread attach result
 *   +0x10 buffer-discovery attempts          +0x38 passes
 *   +0x14 scale drawn on the last pass       +0x3C current buffer, while drawing
 *   +0x18 thread create result               +0x40 current colour
 *   +0x44 our palette is installed           +0x48 palette setter result
 *   +0x50..+0x58 the palette descriptor {entries, count, generation}
 *   +0x80.. the thread object and its body object
 */
.equ MEMSET,     0xC0015058
.equ POST,       0xC03A0798
.equ DRAW_MGR,   0xC0698D80         /* display manager; [r0+4]+0xC resolves a layer */
.equ SUB_LAYER,  1                  /* selector: LCD sub layer, the indexed one */
.equ FMT_INDEX8, 3                  /* descriptor format: one byte per pixel */
.equ UI_STATE,   0xC3033A44         /* 2 live view, 4 playback, 5 camera menu */
.equ UI_LIVEVIEW, 2
.equ CACHE_FN,   0xC000E91C
.equ TK_DLY,     0xC01F89C4
.equ XT_CREATE,  0xC036E108
.equ XT_ATTACH,  0xC036E1B8
.equ PERIOD_MS,  150
.equ PRIORITY,   20
.equ STACK,      0x2000
.equ FETCH_MAX,  8                  /* the layer is asked for its buffer at most
                                     * this often, and never again once all
                                     * three buffers are known */
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
    bl      learn_surface           @ clobbers r0-r3, keeps r4 and r5
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
    add     sp, sp, #4
    pop     {r4, r5, pc}
1:  mov     r1, #0
    str     r1, [r5, #0xC]
    cmp     r0, #0
    bne     2f
    bl      fc_palette              @ r0 = 0: give the layer its palette back
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

/* Learn the sub layer's buffers. Runs from the press handler, which is key
 * context -- the same context the camera's own function key runs in. The
 * layer rotates three buffers and only the UI decides which is live, so every
 * one seen is remembered and all of them are painted.
 *
 * Refuses a descriptor that is not one byte per pixel: every pixel write
 * here is a byte, and on a 16-bit layer that would be garbage.
 *
 * Bounded twice: nothing happens once the third buffer is known, and never
 * more than FETCH_MAX attempts in case this body only ever presents one.
 * r5 = STATE on entry and exit.
 */
learn_surface:
    push    {r4, r6, r7, lr}
    ldr     r0, [r5, #0x30]
    cmp     r0, #0
    bne     9f                      @ all three known: nothing left to ask for
    ldr     r0, [r5, #0x10]
    cmp     r0, #FETCH_MAX
    bhs     9f
    add     r0, r0, #1
    str     r0, [r5, #0x10]
    bl      sub_drawable
    cmp     r0, #0
    beq     9f
    mov     r7, r0
    ldr     r1, [r7]
    ldr     r1, [r1, #0x10]         @ getBackbuffer
    mov     r0, r7
    blx     r1
    cmp     r0, #0
    beq     9f
    ldr     r1, [r0]
    cmp     r1, #FMT_INDEX8
    bne     9f
    ldr     r2, [r0, #4]            @ the buffer's base
    cmp     r2, #0
    beq     9f
    ldr     r1, [r0, #8]
    cmp     r1, #0
    beq     9f
    ldr     r6, [r1]                @ width, which is also the byte stride
    str     r6, [r5, #0x20]
    ldr     r6, [r1, #4]
    str     r6, [r5, #0x24]
    mov     r6, #0x28               @ remember this base if it is new
1:  ldr     r0, [r5, r6]
    cmp     r0, r2
    beq     9f
    cmp     r0, #0
    streq   r2, [r5, r6]
    beq     9f
    add     r6, r6, #4
    cmp     r6, #0x30
    bls     1b
9:  pop     {r4, r6, r7, pc}

/* ------------------------------------------------------------- the painting */

.global fc_spawn
fc_spawn:
    push    {r4, lr}
    LDA     r4, STATE
    add     r0, r4, #0x80
    mov     r1, #PRIORITY
    LDA     r2, STACK
    PTR     r3, thread_name
    LDA     ip, XT_CREATE
    blx     ip
    str     r0, [r4, #0x18]
    add     r0, r4, #0x80
    add     r1, r4, #0x140
    LDA     ip, XT_ATTACH
    blx     ip
    str     r0, [r4, #0x34]
    mov     r0, #0
    pop     {r4, pc}

.global fc_body
fc_body:
    push    {r4, lr}
    LDA     r4, STATE
1:  bl      fc_pass
    ldr     r0, [r4, #0x38]
    add     r0, r0, #1
    str     r0, [r4, #0x38]
    mov     r0, #PERIOD_MS
    LDA     ip, TK_DLY
    blx     ip
    b       1b

/* One repaint. Draws into every buffer it has seen, and takes the scale down
 * again on the first pass after it is switched off or live view is left, so
 * nothing of ours is ever left on a screen that is not ours. */
fc_pass:
    push    {r4, r5, r6, lr}
    LDA     r4, STATE
    ldr     r0, [r4]                @ state 2 is the only one that draws
    cmp     r0, #2
    moveq   r0, #1
    movne   r0, #0
    mov     r5, r0
    LDA     r1, UI_STATE
    ldr     r1, [r1]
    cmp     r1, #UI_LIVEVIEW
    movne   r5, #0                  @ playback or the camera's menu: not ours
    ldr     r0, [r4, #0x14]
    orrs    r0, r0, r5
    beq     9f                      @ not up, and nothing to take down
    mov     r6, #0x28
1:  ldr     r0, [r4, r6]
    cmp     r0, #0
    beq     2f
    mov     r1, r5
    bl      draw_scale
2:  add     r6, r6, #4
    cmp     r6, #0x30
    bls     1b
    str     r5, [r4, #0x14]
    LDA     ip, CACHE_FN            @ our writes are only data to the caches
    blx     ip
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
