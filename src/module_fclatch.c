/* SIGMA fp 5.02: latch the native False Color custom-button function.
 * No renderer, scale, task, or USB command is needed. Do not load alongside
 * another module that owns these same two firmware methods.
 */
#include "module_abi.h"

#define PRESS_SITE 0xC03722E8u
#define RELEASE_SITE 0xC0372330u
#define STOCK 0xE92D4010u

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

int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record)
{
    if (!api || ((uintptr_t)api & 3) || api->magic != FP_RUNTIME_MAGIC ||
        api->abi != FP_ABI || api->bytes < API_SIZE)
        return FP_EABI;
    if (armed)
        return FP_OK;

    struct fp_hook hooks[2] = {
        { PRESS_SITE, STOCK, (uintptr_t)press, 0 },
        { RELEASE_SITE, STOCK, (uintptr_t)release, 0 },
    };
    int32_t result = api->install_hooks(api, record, hooks, 2);
    if (result == FP_OK)
        armed = 1;
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
