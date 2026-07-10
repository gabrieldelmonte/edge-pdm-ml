/*
 * app_acquire.h - ACQUIRE state handler declarations.
 */
#ifndef APP_APP_ACQUIRE_H
#define APP_APP_ACQUIRE_H

#include "app.h"
#include "../peripherals/setup.h"

/**
 * app_do_acquire - Execute APP_STATE_ACQUIRE:
 *   - Start FreeRTOS sampling tasks for enabled sensors
 *   - Wait for sampling to complete (sampling_time_ms)
 *   - Stop tasks and flush queues to binary files on SPIFFS
 *
 * @param cfg  Active configuration.
 * @return     Next state (APP_STATE_SEND_DATA on success, APP_STATE_INIT on error).
 */
app_state_t app_do_acquire(config_t *cfg);

#endif /* APP_APP_ACQUIRE_H */
