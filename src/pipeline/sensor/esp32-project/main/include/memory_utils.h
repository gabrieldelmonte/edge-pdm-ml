/*
 * memory_utils.h - Heap allocation helpers.
 *
 * spiram_malloc() tries PSRAM first when CONFIG_SPIRAM is enabled,
 * then falls back to internal heap.  On targets without PSRAM it is
 * a direct alias for malloc().
 *
 * Use spiram_malloc() for any buffer larger than 256 bytes to keep
 * internal SRAM free for stack and DMA descriptors.
 */
#ifndef APP_MEMORY_UTILS_H
#define APP_MEMORY_UTILS_H

#ifdef CONFIG_SPIRAM
#include "esp_heap_caps.h"
static inline void *spiram_malloc(size_t size)
{
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
    if (!ptr) {
        ptr = malloc(size);
    }
    return ptr;
}
#else
#include <stdlib.h>
static inline void *spiram_malloc(size_t size)
{
    return malloc(size);
}
#endif /* CONFIG_SPIRAM */

#endif /* APP_MEMORY_UTILS_H */
