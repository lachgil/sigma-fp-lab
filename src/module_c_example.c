/* Offline ABI1 example. The compiler supplies the module header and entry
 * wrappers; this source knows only the shared API, never firmware addresses.
 */
#include "module_abi.h"

#ifndef MODULE_INIT_RESULT
#define MODULE_INIT_RESULT 0
#endif

static uint32_t counter = 100;
static uint32_t calls;
static uint32_t last_tick;
static const uint32_t increments[] = {3, 5, 11, 17};
static const char label[] = "Bei C";

/* Volatile pointer objects keep these actual data relocations and indirect
 * accesses, rather than allowing constant folding to hide a broken linker.
 * The label pointer also exercises a relocation with a nonzero addend.
 */
static uint32_t *volatile counter_pointer = &counter;
static const uint32_t *volatile table_pointer = increments;
static const char *volatile letter_pointer = label + 4;

static uint32_t step(uint32_t argument)
{
    return argument + table_pointer[argument & 3] + (uint8_t)*letter_pointer;
}

static uint32_t (*volatile step_pointer)(uint32_t) = step;

int32_t fp_module_init(const struct fp_api *api, const struct fp_record *record)
{
    if (!api || api->magic != FP_RUNTIME_MAGIC || api->abi != FP_ABI ||
        api->bytes < sizeof(*api))
        return FP_EABI;
    if (calls != 0 || last_tick != 0 || *counter_pointer != 100 ||
        step_pointer(2) != 80)
        return FP_EFORMAT;
    last_tick = api->ticks(api);
    int32_t result = api->report(api, record->id, *counter_pointer);
    return result != FP_OK ? result : MODULE_INIT_RESULT;
}

uint32_t fp_module_invoke(const struct fp_api *api,
                          const struct fp_record *record, uint32_t argument)
{
    uint32_t now = api->ticks(api);
    uint32_t elapsed = now - last_tick;
    last_tick = now;
    ++calls;
    *counter_pointer += step_pointer(argument) + calls + elapsed;
    api->report(api, record->id, *counter_pointer);
    return *counter_pointer;
}
