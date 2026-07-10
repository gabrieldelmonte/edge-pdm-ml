/*
 * app_init.h - BOOT and INIT state handler declarations.
 */
#ifndef APP_APP_INIT_H
#define APP_APP_INIT_H

#include "app.h"
#include "../peripherals/setup.h"
#include "../peripherals/headers/lis3dh.h"
#include "../peripherals/headers/adxl345.h"
#include "../peripherals/headers/ssd1306.h"
#include "../peripherals/headers/ina219.h"
#include "esp_err.h"

/**
 * app_do_boot - Execute APP_STATE_BOOT sub-steps:
 *   - NVS flash init
 *   - SPIFFS mount
 *   - Load config from NVS / SPIFFS fallback
 *   - If config is missing, start captive portal
 *
 * @param cfg  Output: populated config on success.
 * @return     Next state (APP_STATE_INIT on success, APP_STATE_BOOT to retry).
 */
app_state_t app_do_boot(config_t *cfg);

/**
 * app_do_init - Execute APP_STATE_INIT sub-steps:
 *   - Connect to Wi-Fi
 *   - Initialize SPI bus
 *   - Initialize LIS3DH (if cfg->lis3dh.enabled)
 *   - Initialize ADXL345 (if cfg->adxl345.enabled)
 *   - Initialize OLED (if cfg->oled.enabled, non-fatal)
 *   - Initialize INA219 (if cfg->ina219.enabled, non-fatal)
 *   - Validate at least one sensor is enabled
 *
 * @param cfg  Config loaded during BOOT.
 * @return     Next state (APP_STATE_ACQUIRE on success, APP_STATE_INIT on error).
 */
app_state_t app_do_init(config_t *cfg);

/**
 * app_init_get_lis3dh  - Return pointer to the initialized LIS3DH handle.
 * app_init_get_adxl345 - Return pointer to the initialized ADXL345 handle.
 * Valid only after a successful app_do_init().
 */
lis3dh_handle_t  *app_init_get_lis3dh(void);
adxl345_handle_t *app_init_get_adxl345(void);

/**
 * app_init_get_oled   - Return pointer to the initialized OLED handle, or
 *                       NULL if disabled or initialization failed.
 * app_init_get_ina219 - Return pointer to the initialized INA219 handle, or
 *                       NULL if disabled or initialization failed.
 * Both are non-fatal peripherals: a NULL return means "not available this
 * session", not an error the caller needs to propagate.
 */
ssd1306_handle_t *app_init_get_oled(void);
ina219_handle_t  *app_init_get_ina219(void);

#endif /* APP_APP_INIT_H */
