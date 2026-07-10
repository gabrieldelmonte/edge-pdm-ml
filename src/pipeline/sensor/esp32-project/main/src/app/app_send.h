/*
 * app_send.h - SEND_DATA state handler declarations.
 */
#ifndef APP_APP_SEND_H
#define APP_APP_SEND_H

#include "app.h"
#include "../peripherals/setup.h"

/**
 * app_do_send - Execute APP_STATE_SEND_DATA:
 *   - Stream binary records as CSV, never holding full CSV in RAM
 *   - Compute incremental SHA-256 per chunk
 *   - Branch on config->transport_mode (HTTPS or MQTT)
 *   - Wait for inference result
 *   - Display result on OLED if enabled
 *
 * @param cfg  Active configuration.
 * @return     Next state (APP_STATE_SLEEP on success, APP_STATE_INIT on error).
 */
app_state_t app_do_send(config_t *cfg);

#endif /* APP_APP_SEND_H */
