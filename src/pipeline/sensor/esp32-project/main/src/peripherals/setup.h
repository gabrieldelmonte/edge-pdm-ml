/*
 * setup.h - Device configuration structures, NVS helpers, and captive
 * portal declarations for the edge PDM sensor firmware.
 */
#ifndef APP_SETUP_H
#define APP_SETUP_H

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

/* -------------------------------------------------------------------------
 * Transport mode
 * ------------------------------------------------------------------------- */
typedef enum {
    TRANSPORT_HTTPS = 0,
    TRANSPORT_MQTT  = 1,
} transport_mode_t;

/* -------------------------------------------------------------------------
 * LIS3DH configuration
 * ------------------------------------------------------------------------- */
typedef enum {
    LIS3DH_ODR_100HZ   = 0,
    LIS3DH_ODR_200HZ   = 1,
    LIS3DH_ODR_400HZ   = 2,
    LIS3DH_ODR_1344HZ  = 3,
    LIS3DH_ODR_1620HZ_LP = 4,
    LIS3DH_ODR_5376HZ_LP = 5,
} lis3dh_odr_t;

typedef enum {
    LIS3DH_FS_2G  = 0,
    LIS3DH_FS_4G  = 1,
    LIS3DH_FS_8G  = 2,
    LIS3DH_FS_16G = 3,
} lis3dh_fs_t;

typedef struct {
    bool        enabled;
    lis3dh_odr_t odr;
    lis3dh_fs_t  full_scale;
    int          cs_pin;
    int          mosi_pin;
    int          miso_pin;
    int          sclk_pin;
} lis3dh_config_t;

/* -------------------------------------------------------------------------
 * ADXL345 configuration
 * ------------------------------------------------------------------------- */
typedef enum {
    ADXL345_RATE_800HZ  = 0,
    ADXL345_RATE_1600HZ = 1,
    ADXL345_RATE_3200HZ = 2,
} adxl345_rate_t;

typedef struct {
    bool           enabled;
    adxl345_rate_t data_rate;
    int            cs_pin;
} adxl345_config_t;

/* -------------------------------------------------------------------------
 * Optional peripheral configuration
 * ------------------------------------------------------------------------- */
typedef struct {
    bool enabled;
    int  sda_pin;
    int  scl_pin;
} i2c_periph_config_t;

/* -------------------------------------------------------------------------
 * Top-level device config
 * ------------------------------------------------------------------------- */
typedef struct {
    /* Network */
    char wifi_ssid[64];
    char wifi_password[64];

    /* Identity */
    char device_name[64];

    /* Middleware (HTTPS) */
    char middleware_host[128];
    uint16_t middleware_port;

    /* Sampling */
    uint32_t sampling_time_ms;
    uint32_t deep_sleep_duration_s;

    /* Timezone */
    char timezone[32];

    /* Transport */
    transport_mode_t transport_mode;

    /* MQTT */
    char     mqtt_host[128];
    uint16_t mqtt_port;
    uint32_t mqtt_batch_size;

    /* Sensors */
    lis3dh_config_t  lis3dh;
    adxl345_config_t adxl345;

    /* Optional peripherals */
    i2c_periph_config_t oled;
    i2c_periph_config_t ina219;

    /* Schema version for NVS migration */
    int schema_version;
} config_t;

/* -------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------- */

/**
 * app_setup_init - Perform first-boot hardware init (NVS, SPIFFS, I2C).
 */
esp_err_t app_setup_init(void);

/**
 * app_config_load - Load config from NVS (or SPIFFS fallback) into *cfg.
 * Applies schema migration if schema_version < APP_CONFIG_SCHEMA_VERSION.
 */
esp_err_t app_config_load(config_t *cfg);

/**
 * app_config_save - Persist *cfg to NVS and SPIFFS fallback.
 */
esp_err_t app_config_save(const config_t *cfg);

/**
 * app_config_defaults - Fill *cfg with factory defaults.
 */
void app_config_defaults(config_t *cfg);

/**
 * app_captive_portal_start - Start the Wi-Fi AP and HTTP captive portal.
 * Blocks until the user submits a valid configuration.
 */
esp_err_t app_captive_portal_start(config_t *cfg);

/**
 * app_wifi_connect - Connect to the configured SSID.
 */
esp_err_t app_wifi_connect(const config_t *cfg);

#endif /* APP_SETUP_H */
