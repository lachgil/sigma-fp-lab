/* Optional fp 5.02 UI provider, not an extension of the runtime ABI.
 * Load provider 0x100 separately, then use api->lookup/api->call by ID.
 * Public call returns runtime status in r0 and the service result in r1.
 * A successful dispatch can therefore contain a negative service error.
 * The argument is a trusted, aligned pointer to six writable module-owned
 * uint32 words. No caller pointer is retained and no module callback is made.
 */
#ifndef FP_MODULE_UI_H
#define FP_MODULE_UI_H
#include "module_abi.h"

#define FP_UI_ID 0x100
#define FP_UI_DEMO_ID 0x101
#define FP_UI_VERSION 1
#define FP_UI_QUERY 0
#define FP_UI_FILL 1
#define FP_UI_PRESENT 2
#define FP_UI_HIDE 3
#define FP_UI_BUTTONS 4
#define FP_UI_STATUS 5

#define UI_REQ_OP 0
#define UI_REQ_A 4
#define UI_REQ_B 8
#define UI_REQ_C 12
#define UI_REQ_D 16
#define UI_REQ_E 20
#define UI_REQ_SIZE 24

#define FP_UI_WIDTH 96
#define FP_UI_HEIGHT 32
#define FP_UI_X 464
#define FP_UI_Y 560
#define FP_UI_WHITE 0xFFFF
#define FP_UI_BLACK 0xF000
#define FP_UI_SURFACE_WIDTH 1024
#define FP_UI_SURFACE_MAX_HEIGHT 2048

#define FP_UI_EBUSY -16
#define FP_UI_EINVAL -17
#define FP_UI_ECONFLICT -18
#define FP_UI_ENOSPACE -19
#define FP_UI_ESLOTS -20
#define FP_UI_EIDENTITY -21

/* QUERY -> version 1. FILL(a=x,b=y,c=w,d=h,e=packed 16-bit color) -> 0.
 * Coordinates are unsigned and clipped by subtraction, so huge sizes cannot
 * wrap. Empty or outside rectangles are successful no-ops. High color bits
 * are ignored. Only white/black avoid the unverified color-channel order.
 * FILL changes one rectangle atomically against the submit hook, not an
 * entire multi-call drawing transaction. Competing mutation returns EBUSY
 * without changing the canvas or visibility. A busy frame hook skips a frame.
 *
 * PRESENT -> 0 after explicitly installing both hooks and enabling drawing.
 * It checks stock instructions before installation and never overwrites a
 * conflicting owner. Initialization does not arm hooks. Once armed, hooks
 * remain resident until reboot; HIDE does not unpatch them or stop counting.
 * PRESENT/HIDE do not force a display refresh. The next eligible firmware
 * submission paints/restores the tile. No cached framebuffer is ever written
 * by a command. HIDE -> 0 after disabling painting.
 *
 * BUTTONS -> wrapping uint32 count of observed assigned FalseColor custom
 * function press callbacks since arming, not a general key API. Native press
 * and release behavior is unchanged. Reads of BUTTONS/STATUS are word-sized
 * snapshots and do not take the render lock. STATUS -> 0 initially/ready or
 * the latest installation/render error, cleared by a successful PRESENT or
 * handled render. Rejected geometry and busy frame skips leave it unchanged.
 * STATUS does not mean that PRESENT has been requested or a frame was shown.
 *
 * Only main/subflag=0, format=1, width=1024, height=592..2048 submissions are
 * eligible. Painting requires known live-view state 2. A matching eligible
 * submission restores instead in menu/playback/other states or after HIDE.
 * Three rotating identities retain descriptor and geometry addresses, base,
 * width and height. Painted slots are never evicted. A fourth buffer returns
 * ESLOTS through STATUS; overlapping changed identities return EIDENTITY.
 * Such submissions are skipped until a tracked identity can be restored.
 *
 * Each saved pixel is restored only if it still equals the provider's last
 * painted value. Different native pixels are preserved, and native changes
 * become the new background on repaint. A native write equal to our last
 * value is indistinguishable and cannot be protected. Old painted buffers
 * that never return with their exact eligible identity cannot be restored;
 * HIDE can leave a visible tile until that buffer is submitted again. This
 * bounded ownership policy deliberately avoids writing stale cached pointers.
 * No allocation, callbacks into consumers, or blocking locks occur on frames.
 * Initialization reserves one 43,136-byte USER block outside the compact
 * module image. Allocation failure leaves no hooks; malformed owned storage
 * is freed before failure. Successful storage and code remain until reboot,
 * as the runtime has no unload contract. Service requests never allocate.
 */
#ifndef __ASSEMBLER__
struct fp_ui_request {
    uint32_t op;
    uint32_t a;
    uint32_t b;
    uint32_t c;
    uint32_t d;
    uint32_t e;
};
_Static_assert(sizeof(struct fp_ui_request) == UI_REQ_SIZE, "UI request ABI size");
#endif
#endif
