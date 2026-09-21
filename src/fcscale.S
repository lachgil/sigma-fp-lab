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
 *   press         mode on. Press again, from either state, and it is off:
 *                 turning it off never needs a hold
 *   hold 1/2 s    mode on WITH the scale; and once the mode is on, a hold
 *                 puts the scale up or takes it down, whenever you like
 *
 * The mode itself is switched at the press, so the button feels immediate; the
 * scale follows at the release, because only the release knows how long the
 * button was held. The camera's own clock (0xC002B920, milliseconds, the one
 * the firmware's logger uses) measures it.
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
 * The drawing surface is the LCD's main OSD layer: 16-bit pixels, alpha in
 * the top nibble, descriptor format 1, resolved through the display manager
 * the way the firmware's own FalseColorBarDrawer resolves its layer
 * (0xC057DA78). Colours are direct, so no palette is involved; the builder
 * converts the firmware's A,Y,U,V scale entries with the firmware's own
 * matrix (0xC03E4C38) and packs them 4:4:4. Routines take the pixel size
 * from STATE +0x8C, so the same code draws an indexed layer with PAINT_*
 * changed.
 *
 * Painting is done inside the firmware's own display submit: 0xC02E8A08 is
 * entered through fc_submit, which paints the scale into the exact buffer
 * being sent to the panel. The layer rotates three buffers; each one is
 * tracked with a drawn flag so a buffer carrying the scale is cleared once
 * when the scale goes away. A press paints the layer's front buffer directly,
 * the one the panel is scanning, so the change is immediate rather than at
 * the UI's next frame (see fc_present for why not a submit).
 *
 * State block at STATE:
 *   +0x00 0 off, 1 on, 2 on with the scale   +0x3C buffer being painted
 *   +0x04 presses                            +0x40 current colour
 *   +0x08 releases                           +0x60..+0x68 drawn flag per buffer
 *   +0x0C the scale flag                     +0x6C paint lock
 *   +0x10 this press, in ms                  +0x74 submits seen
 *   +0x14 how long the last press was        +0x78 frames painted
 *   +0x18 holds acted on                     +0x7C next slot to evict
 *   +0x1C the mode when the press arrived    +0x8C bytes per pixel, while painting
 *   +0x20 width in pixels, while painting
 *   +0x24 height, while painting
 *   +0x28..+0x30 the layer's three buffers
 */
.equ MEMSET,     0xC0015058
.equ POST,       0xC03A0798
.equ DRAW_MGR,   0xC0698D80         /* display manager; [r0+4]+0xC resolves a layer */
/* Which OSD layer the scale goes on. Main is 16-bit direct colour (format 1,
 * alpha in the top nibble, 0xC052A528); sub is one byte per pixel through a
 * palette (format 3). Two palette installs on the sub layer did not take on
 * the camera (2026-09-20: setPalette at the press, then the descriptor's
 * palette pointer at each submit), so the scale is drawn on main. */
.equ PAINT_LAYER, 0                 /* manager selector and submit sub flag: 0 main, 1 sub */
.equ PAINT_FMT,   1                 /* the descriptor format that layer must report */
.equ PAINT_BPP,   2                 /* bytes per pixel there */
.equ UI_STATE,   0xC3033A44         /* 2 live view, 4 playback, 5 camera menu */
.equ UI_PLAYBACK, 4
.equ UI_MENU,     5
.equ CACHE_FN,   0xC000E91C
/* The scale's geometry comes out of the builder, which rescales the
 * firmware's own: see fcscale_native.inc.S for BAR_Y, BAR_H, LABEL_Y,
 * CLEAR_Y and CLEAR_H, and tools/build_fcscale_autorun.py for the knobs.
 * SURFACE_W is the layer, not the scale: the bar may be narrower. */
.equ SURFACE_W,  1024
.equ C_CLEAR,    0                  /* transparent: index 0, or alpha nibble 0 */

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

/* Replaces 0xC03722E8, the press. r0 = the CameraIF object.
 *
 * The press does two things and no more: it timestamps itself, and if the mode
 * was off it switches the mode on straight away. Everything that depends on
 * how long the button was held happens in fc_release. */
.global fc_press
fc_press:
    push    {r4, r5, lr}
    sub     sp, sp, #4              @ three registers: keep sp 8-aligned
    mov     r4, r0
    LDA     r5, STATE
    ldr     r1, [r5, #4]
    add     r1, r1, #1
    str     r1, [r5, #4]
    LDA     ip, CLOCK
    blx     ip
    str     r0, [r5, #0x10]
    ldr     r0, [r5]
    str     r0, [r5, #0x1C]         @ the mode this press started from
    cmp     r0, #0
    bne     1f                      @ already on: the release decides
    mov     r1, #1
    str     r1, [r5]                @ on, without the scale, now
    mov     r1, #0
    str     r1, [r5, #0xC]
    mov     r0, r4
    mov     r1, #0x21
    bl      fc_post
1:  add     sp, sp, #4
    pop     {r4, r5, pc}

/* Replaces 0xC0372330, the release. Letting go never cancels the mode; what it
 * does depends on how long the button was down and on what the press found:
 *
 *   started off, short   nothing: the press already switched the mode on
 *   started off, hold    the scale comes up with it
 *   started on,  short    off, and the scale with it
 *   started on,  hold    the scale toggles, the mode stays on
 *
 * The stock method returns the post's result and callers read it as a success
 * flag, so this returns 1 on every path. */
.global fc_release
fc_release:
    push    {r4, r5, lr}
    sub     sp, sp, #4
    mov     r4, r0
    LDA     r5, STATE
    ldr     r1, [r5, #8]
    add     r1, r1, #1
    str     r1, [r5, #8]
    LDA     ip, CLOCK
    blx     ip
    ldr     r1, [r5, #0x10]
    sub     r0, r0, r1              @ how long it was held, in ms
    str     r0, [r5, #0x14]
    movw    r1, #HOLD_MS
    cmp     r0, r1
    blo     2f

    ldr     r0, [r5, #0x18]         @ a hold
    add     r0, r0, #1
    str     r0, [r5, #0x18]
    ldr     r0, [r5, #0xC]          @ started off: the scale comes up.
    ldr     r1, [r5, #0x1C]         @ already on: the scale flips
    cmp     r1, #0
    moveq   r0, #1
    eorne   r0, r0, #1
    str     r0, [r5, #0xC]
    cmp     r0, #0
    movne   r1, #2
    moveq   r1, #1
    str     r1, [r5]
    bl      fc_present              @ put it up, or take it down, now
    ldr     r0, [r5, #0xC]          @ taken down: get it off the buffers the
    cmp     r0, #0                  @ panel is not showing as well, before the
    bne     3f                      @ camera menu can claim one of them
    bl      clear_all
    b       3f

2:  ldr     r0, [r5, #0x1C]         @ a press. Started off: the press already
    cmp     r0, #0                  @ did it. Started on: off, both parts
    beq     3f
    mov     r1, #0
    str     r1, [r5]
    str     r1, [r5, #0xC]
    bl      fc_present              @ take the scale down now
    bl      clear_all
    mov     r0, r4
    mov     r1, #0x22
    bl      fc_post
3:  mov     r0, #1
    add     sp, sp, #4
    pop     {r4, r5, pc}

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
layer_drawable:
    push    {r4, lr}
    LDA     ip, DRAW_MGR
    blx     ip
    cmp     r0, #0
    beq     9f
    ldr     r1, [r0, #4]
    ldr     r3, [r1, #0xC]
    mov     r1, #PAINT_LAYER
    blx     r3
9:  pop     {r4, pc}


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

/* fc_on_submit(controller, descriptor, sub): paint the frame on its way out. */
fc_on_submit:
    push    {r4, lr}
    LDA     r4, STATE
    ldr     r3, [r4, #0x74]
    add     r3, r3, #1
    str     r3, [r4, #0x74]
    mov     r0, r1
    bl      fc_paint
    pop     {r4, pc}

/* clear_all: take the scale off every buffer we have painted, not just the one
 * the panel is showing.
 *
 * Without this, a buffer that still carried the scale when the scale went off
 * keeps it until that buffer comes round again IN LIVE VIEW, because that is
 * the only path that clears it. If the camera menu claims the buffer first,
 * the menu draws over our rows, and the scale shows up inside the menu
 * (camera, 2026-09-21). Clearing at the moment the scale goes off, while we
 * still know which buffers carry it, closes that window.
 *
 * Geometry comes from the last paint (+0x20/+0x24): if nothing has ever been
 * painted there is nothing of ours to clear, and it returns. */
clear_all:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r8, STATE
    ldr     r5, [r8, #0x20]
    cmp     r5, #0
    beq     9f
    add     r3, r8, #0x6C           @ the same paint lock the hook takes
    mov     r1, #1
    swp     r2, r1, [r3]
    cmp     r2, #0
    bne     9f
    dmb
    mov     r4, #0
1:  add     r6, r8, r4, lsl #2
    ldr     r7, [r6, #0x28]
    cmp     r7, #0
    beq     2f
    mov     r0, r7
    mov     r1, #0
    bl      draw_scale
    mov     r0, #0
    str     r0, [r6, #0x60]
    ldr     r5, [r8, #0x20]
    ldr     r2, [r8, #0x8C]
    movw    r1, #CLEAR_Y
    mul     r1, r5, r1
    mla     r0, r1, r2, r7
    mov     r1, #CLEAR_H
    mul     r1, r5, r1
    mul     r1, r2, r1
    bl      clean_range
2:  add     r4, r4, #1
    cmp     r4, #3
    blo     1b
    mov     r0, #0
    str     r0, [r8, #0x6C]
9:  pop     {r4, r5, r6, r7, r8, pc}

/* fc_paint(descriptor). Acts only on a descriptor of PAINT_FMT, of the
 * scale's width and at least its height; anything else (the other layer,
 * another monitor) is left alone.
 *
 * Each of the layer's buffers is tracked with a drawn flag: the scale is
 * painted whenever it is wanted, and a buffer that carries it is cleared once
 * when it is not, so nothing of ours stays on a buffer the UI recycles. A
 * word at +0x6C serialises the hook against a press. */
fc_paint:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r8, STATE
    cmp     r0, #0
    beq     9f
    ldr     r3, [r0]
    cmp     r3, #PAINT_FMT
    bne     9f
    mov     r2, #PAINT_BPP
    str     r2, [r8, #0x8C]
    ldr     r4, [r0, #4]            @ base
    cmp     r4, #0
    beq     9f
    ldr     r3, [r0, #8]
    cmp     r3, #0
    beq     9f
    ldr     r5, [r3]                @ width, in pixels
    ldr     r6, [r3, #4]            @ height
    movw    ip, #SURFACE_W
    cmp     r5, ip
    bne     9f
    movw    ip, #(CLEAR_Y + CLEAR_H)
    cmp     r6, ip
    blo     9f

    ldr     r0, [r8]                @ wanted: the scale is up, and the screen is
    cmp     r0, #2                  @ live view. UI_STATE reads 2 in live view
    moveq   r7, #1                  @ with the mode and the scale both on, 5 in
    movne   r7, #0                  @ the camera menu and 4 in playback (camera,
    LDA     r0, UI_STATE            @ 2026-09-21, read off the --probe build).
    ldr     r0, [r0]
    cmp     r0, #UI_PLAYBACK
    cmpne   r0, #UI_MENU
    moveq   r7, #0

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
    cmp     r7, #0
    bne     4f                      @ wanted: paint this buffer below
    cmp     r2, #0
    beq     5f                      @ nothing of ours on it either
    push    {r0, r1}                @ the gate has closed and this buffer still
    bl      clear_all               @ carries the scale: take it off ALL of them
    pop     {r0, r1}                @ now. One buffer per frame means the scale
    mov     r2, #0                  @ hangs around in the menu until each one
5:                                  @ happens to come round (camera, 2026-09-21)
.if PROBE != 0
    orr     r2, r2, #1              @ the probe draws on every frame
.endif
    cmp     r2, #0
    beq     9f                      @ nothing there and nothing wanted

4:

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
    mul     r1, r5, r1              @ out of the data cache, and only ours --
    ldr     r2, [r8, #0x8C]         @ the firmware's 0xC000E91C cleans every
    mla     r0, r1, r2, r4          @ set and way of both levels with
    mov     r1, #CLEAR_H            @ interrupts off, too much for every frame
    mul     r1, r5, r1
    mul     r1, r2, r1
    bl      clean_range
.if PROBE != 0
    bl      draw_probe
.endif
6:  mov     r0, #0
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

/* fc_present: paint the layer's front buffer, the one the panel is scanning
 * now, so a press shows or clears the scale immediately instead of at the
 * UI's next frame. No rotate and no submit: a rotated-and-submitted frame
 * from the press was not shown once the UI had gone quiet after the mode
 * change (camera, 2026-09-20; a second press during the UI's own redraw
 * was), while direct writes into the layer's buffers were seen on the panel
 * from the first build. The hook keeps repainting whatever the UI submits
 * afterwards. */
fc_present:
    push    {r4, lr}
    bl      layer_drawable
    cmp     r0, #0
    beq     9f
    mov     r4, r0
    ldr     r3, [r4]
    ldr     r3, [r3, #0x0C]         @ front descriptor
    mov     r0, r4
    blx     r3
    cmp     r0, #0
    beq     9f
    bl      fc_paint
9:  pop     {r4, pc}

.if PROBE != 0
/* draw_probe: a row of ticks at the top left, one per unit of the UI state
 * word at UI_STATE, drawn on every frame whatever the gate decides.
 *
 * It exists to answer one question without a USB shell: what value that word
 * holds while the camera menu is open. The gate treats 4 as playback and 5 as
 * the menu, and those were read on one build in one mode; if the menu uses
 * another value the gate never closes and the scale is painted over the menu.
 * Count the ticks on screen and the value is known.
 *
 * Built only with `--probe`. The shipped card has PROBE = 0 and none of this
 * is assembled. */
.equ TICK_X,    16
.equ TICK_Y,    16
.equ TICK_W,    10
.equ TICK_H,    14
.equ TICK_STEP, 14
.equ TICK_MAX,  20
draw_probe:
    push    {r4, r5, r6, lr}
    LDA     r6, STATE
    mov     r0, #C_CLEAR            @ the whole strip, so a smaller count
    str     r0, [r6, #0x40]         @ cannot leave the previous one's tail
    mov     r0, #TICK_X
    mov     r1, #TICK_Y
    movw    r2, #(TICK_STEP * TICK_MAX)
    mov     r3, #TICK_H
    bl      fill_rect
    LDA     r0, UI_STATE
    ldr     r5, [r0]
    cmp     r5, #TICK_MAX
    movhi   r5, #TICK_MAX
    cmp     r5, #0
    beq     2f
    ldr     r0, [r6, #0x8C]
    cmp     r0, #2
    movweq  r0, #C_LABEL16
    movne   r0, #C_LABEL
    str     r0, [r6, #0x40]
    mov     r4, #0
1:  mov     r0, #TICK_STEP
    mul     r0, r4, r0
    add     r0, r0, #TICK_X
    mov     r1, #TICK_Y
    mov     r2, #TICK_W
    mov     r3, #TICK_H
    bl      fill_rect
    add     r4, r4, #1
    cmp     r4, r5
    blo     1b
2:  ldr     r5, [r6, #0x20]         @ the panel reads memory: clean the strip
    ldr     r2, [r6, #0x8C]
    mov     r1, #TICK_Y
    mul     r1, r5, r1
    ldr     r0, [r6, #0x3C]
    mla     r0, r1, r2, r0
    mov     r1, #TICK_H
    mul     r1, r5, r1
    mul     r1, r2, r1
    bl      clean_range
    pop     {r4, r5, r6, pc}
.endif

/* draw_scale(base, wanted): clear the scale's rows, then draw it if wanted.
 * Refuses a surface the firmware's own geometry does not fit on. */
draw_scale:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    mov     r9, r1
    ldr     r7, [r10, #0x20]        @ stride
    movw    r0, #SURFACE_W
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
    movw    r2, #SURFACE_W
    mov     r3, #CLEAR_H
    bl      fill_rect
    cmp     r9, #0
    beq     9f

    PTR     r4, bands               @ {u16 x0, u16 x1, u8 index, u8 0, u16 colour16}
    mov     r5, #BAND_N
1:  ldrh    r0, [r4]
    ldrh    r1, [r4, #2]
    ldr     r2, [r10, #0x8C]
    cmp     r2, #2
    ldrheq  r2, [r4, #6]            @ direct colour on a 16-bit layer
    ldrbne  r2, [r4, #4]            @ the palette index on an indexed one
    str     r2, [r10, #0x40]
    sub     r2, r1, r0
    add     r2, r2, #1              @ x1 is inclusive
    movw    r1, #BAR_Y
    mov     r3, #BAR_H
    bl      fill_rect
    add     r4, r4, #8
    subs    r5, r5, #1
    bne     1b

    ldr     r0, [r10, #0x8C]
    cmp     r0, #2
    movweq  r0, #C_LABEL16
    movne   r0, #C_LABEL
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
 * Pixels are one or two bytes (STATE +0x8C), a pixel is at
 * base + (y * width + x) * size. Refuses anything that would leave the
 * buffer, and refuses entirely if no buffer or geometry is known. This is
 * the last line of defence and it exists because it was missing: a chart
 * drawn from a fixed origin, with no check, wrote past the end of the buffer
 * and froze the camera.
 */
fill_rect:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ width, in pixels
    ldr     r9, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r7, #0
    beq     9f
    cmp     r3, #0                  @ empty height or width: nothing to do
    beq     9f
    cmp     r2, #0
    beq     9f
    adds    r4, r0, r2              @ x + w must stay inside the width
    bcs     9f
    cmp     r4, r7
    bhi     9f
    adds    r4, r1, r3              @ y + h must stay inside the height
    bcs     9f
    cmp     r4, r9
    bhi     9f
    ldr     r9, [r10, #0x40]        @ colour
    ldr     r10, [r10, #0x8C]       @ bytes per pixel
    mov     r4, r0
    mov     r5, r1
    mov     r8, r2
1:  mla     ip, r5, r7, r4          @ y * width + x
    mla     ip, ip, r10, r6         @ ... * size + base
    mov     lr, r8
2:  cmp     r10, #2
    strheq  r9, [ip]
    strbne  r9, [ip]
    add     ip, ip, r10
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
    ldr     r7, [r10, #0x20]        @ width, in pixels
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
    ldr     r10, [r10, #0x8C]       @ bytes per pixel
    mla     ip, r1, r7, r0          @ y * width + x
    mla     r6, ip, r10, r6         @ the glyph's top-left pixel, in bytes...
    mul     r7, r7, r10             @ ... and the row step
    mov     r4, #GLYPH_H
1:  mov     ip, r6
    mov     lr, r5                  @ pixels left in this row
    mov     r0, r9                  @ words left in this row
2:  ldr     r1, [r3], #4
    mov     r2, #32
3:  cmp     lr, #0
    beq     4f                      @ row complete; still consume its words
    tst     r1, #1
    beq     5f
    cmp     r10, #2
    strheq  r8, [ip]
    strbne  r8, [ip]
5:  lsr     r1, r1, #1
    add     ip, ip, r10
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
