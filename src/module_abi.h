/* Experimental fp 5.02 boot-resident ABI, little-endian ARM/AAPCS.
 * Serialized boot initialization and calls only. Trusted code, no sandbox,
 * concurrency, unload, or native menu API. Hook ownership is runtime-managed.
 */
#ifndef FP_MODULE_ABI_H
#define FP_MODULE_ABI_H
#define FP_ABI 1
#define FP_MODULE_MAGIC 0x444F4D46
#define FP_RUNTIME_MAGIC 0x54525046
#define FP_DIRECTORY_MAGIC 0x44525046
#define FP_CAPACITY 8

/* Module artifact: six words followed by position-independent code/data.
 * init(api, record) -> 0 or error; invoke(api, record, argument) -> value.
 */
#define MOD_MAGIC 0
#define MOD_ABI 4
#define MOD_ID 8
#define MOD_BYTES 12
#define MOD_INIT 16
#define MOD_INVOKE 20
#define MOD_HEADER_BYTES 24

/* Runtime service table. Every function pointer is resident and absolute.
 * lookup(api, id) -> record or NULL (first occurrence, including failures).
 * call(api, id, argument) -> r0 status, r1 value (zero on failure).
 * report(api, id, value) -> status; allowed during init and when ready.
 * ticks(api) -> firmware microsecond clock, uint32 wraparound.
 */
#define API_MAGIC 0
#define API_ABI 4
#define API_BYTES 8
#define API_COUNT 12
#define API_RECORDS 16
#define API_LOOKUP 20
#define API_CALL 24
#define API_REPORT 28
#define API_TICKS 32
#define API_INSTALL_HOOKS 36
#define API_SIZE 40

/* Atomic ARM function-entry B hooks. Input veneer is ignored; output is
 * written only after the entire batch is installed and cache-published.
 */
#define FP_HOOK_CAPACITY 8
#define HOOK_SITE 0
#define HOOK_ORIGINAL 4
#define HOOK_TARGET 8
#define HOOK_VENEER 12
#define HOOK_SIZE 16

/* Registry records are runtime-owned; modules should use services, not edit
 * these fields. Allocation descriptor belongs to the runtime for failure free.
 */
#define REC_ID 0
#define REC_STATUS 4
#define REC_IMAGE 8
#define REC_BYTES 12
#define REC_INIT 16
#define REC_INVOKE 20
#define REC_VALUE 24
#define REC_INIT_RESULT 28
#define REC_ALLOC 32
#define REC_SIZE 48

/* A 16-byte directory allocated from upstream's boot-only cave bump arena.
 * root pointer stays zero until initialization finishes. Module failures are
 * recorded individually and do not prevent a ready directory.
 */
#define DIR_MAGIC 0
#define DIR_RUNTIME 4
#define DIR_BYTES 8
#define DIR_STATUS 12
#define DIR_SIZE 16
#define FP_CAVE_BUMP 0xC072E060
#define FP_CAVE_BEGIN 0xC072EC60
#define FP_CAVE_END 0xC072EFB4

#define FP_OK 0
#define FP_INITIALIZING 1
#define FP_ENOMEM -1
#define FP_EABI -2
#define FP_EFORMAT -3
#define FP_EDUPLICATE -4
#define FP_EINIT -5
#define FP_ENOENT -6
#define FP_ENOTREADY -7
#define FP_ECONFLICT -8
#define FP_ENOSPACE -9
#define FP_EREGISTER -10

#ifndef __ASSEMBLER__
#include <stddef.h>
#include <stdint.h>

struct fp_record {
    uint32_t id;
    int32_t status;
    uint32_t image;
    uint32_t image_bytes;
    uint32_t initialize;
    uint32_t invoke;
    uint32_t value;
    int32_t init_result;
    uint32_t allocation[4];
};

struct fp_hook {
    uint32_t site;
    uint32_t original;
    uint32_t target;
    uint32_t veneer;
};

struct fp_api {
    uint32_t magic;
    uint32_t abi;
    uint32_t bytes;
    uint32_t count;
    const struct fp_record *records;
    const struct fp_record *(*lookup)(const struct fp_api *, uint32_t id);
    /* ARM AAPCS uint64: status in low/r0, value in high/r1. */
    uint64_t (*call)(const struct fp_api *, uint32_t id, uint32_t argument);
    int32_t (*report)(const struct fp_api *, uint32_t id, uint32_t value);
    uint32_t (*ticks)(const struct fp_api *);
    int32_t (*install_hooks)(const struct fp_api *, const struct fp_record *,
                             struct fp_hook *, uint32_t count);
};

static inline int32_t fp_call_status(uint64_t result) {
    return (int32_t)(uint32_t)result;
}

static inline uint32_t fp_call_value(uint64_t result) {
    return (uint32_t)(result >> 32);
}

_Static_assert(sizeof(void *) == 4, "fp module ABI requires a 32-bit target");
_Static_assert(sizeof(struct fp_record) == REC_SIZE, "record ABI size");
_Static_assert(sizeof(struct fp_api) == API_SIZE, "service ABI size");
_Static_assert(sizeof(struct fp_hook) == HOOK_SIZE, "hook ABI size");
_Static_assert(offsetof(struct fp_api, install_hooks) == API_INSTALL_HOOKS, "hook service ABI offset");
_Static_assert(offsetof(struct fp_api, call) == API_CALL, "call ABI offset");
_Static_assert(offsetof(struct fp_record, allocation) == REC_ALLOC, "allocation ABI offset");
#endif
#endif
