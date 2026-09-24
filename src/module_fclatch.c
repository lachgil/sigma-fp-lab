/* SIGMA fp 5.02: latch the native False Color custom-button function.
 * No renderer, scale, task, or USB command is needed. Do not load alongside
 * another module that owns these same two firmware methods.
 */
#include "module_abi.h"

#define PRESS_SITE 0xC03722E8u
#define RELEASE_SITE 0xC0372330u
#define STOCK 0xE92D4010u
#define WORD(address) (*(volatile uint32_t *)(uintptr_t)(address))

static volatile uint32_t armed, enabled, presses, releases;

static uint32_t press(uint32_t *camera)
{
    uint32_t request[0xBC / 4];
    void *(*zero)(void *, int, uint32_t) = (void *)0xC0015058u;
    uint32_t (*post)(uint32_t, const void *) = (void *)0xC03A0798u;
    zero(request, 0, sizeof(request));
    enabled ^= 1;
    ++presses;
    request[0] = enabled ? 0x21 : 0x22;
    request[1] = 1;
    return post(camera[1], request);
}

static uint32_t release(uint32_t *camera)
{
    (void)camera;
    ++releases;
    return 1;
}

static void publish(void)
{
    __asm__ volatile("dmb" ::: "memory");
    ((void (*)(void))0xC000E91Cu)();
    ((void (*)(void))0xC000EABCu)();
    __asm__ volatile("dsb\n\tisb" ::: "memory");
}

static uint32_t branch(uint32_t site, uint32_t target)
{
    /* Replace a function prologue with B, preserving its caller's LR. */
    return 0xEA000000u | (((target - site - 8) >> 2) & 0xFFFFFFu);
}

int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record)
{
    (void)record;
    if (!api || ((uintptr_t)api & 3) || api->magic != FP_RUNTIME_MAGIC ||
        api->abi != FP_ABI || api->bytes < API_SIZE)
        return FP_EABI;
    if (armed)
        return FP_OK;

    uint32_t flags;
    __asm__ volatile("mrs %0, cpsr\n\tcpsid if" : "=r"(flags) :: "memory");
    int32_t result = -8; /* Conflicting hook or occupied cave. */
    if (WORD(PRESS_SITE) != STOCK || WORD(RELEASE_SITE) != STOCK)
        goto done;
    uint32_t cave = WORD(FP_CAVE_BUMP);
    result = -9; /* No safe veneer space. */
    if ((cave & 3) || cave < FP_CAVE_BEGIN || cave > FP_CAVE_END - 16)
        goto done;
    result = -8;
    for (uint32_t offset = 0; offset < 16; offset += 4)
        if (WORD(cave + offset))
            goto done;

    /* Nothing may fail after publication: runtime must retain live hooks. */
    WORD(FP_CAVE_BUMP) = cave + 16;
    WORD(cave) = WORD(cave + 8) = 0xE51FF004u;
    WORD(cave + 4) = (uintptr_t)press;
    WORD(cave + 12) = (uintptr_t)release;
    publish();
    WORD(PRESS_SITE) = branch(PRESS_SITE, cave);
    WORD(RELEASE_SITE) = branch(RELEASE_SITE, cave + 8);
    publish();
    armed = 1;
    result = FP_OK;
done:
    __asm__ volatile("msr cpsr_c, %0" :: "r"(flags) : "memory");
    return result;
}

/* Read-only diagnostics: 0 version, 1 requested on/off, 2 presses,
 * 3 swallowed releases, 4 armed. Enabled is our latch, not a firmware getter.
 */
uint32_t fp_module_invoke(const struct fp_api *api,
                          const struct fp_record *record, uint32_t argument)
{
    (void)api;
    (void)record;
    switch (argument) {
    case 0: return 1;
    case 1: return enabled;
    case 2: return presses;
    case 3: return releases;
    case 4: return armed;
    default: return (uint32_t)FP_EFORMAT;
    }
}
