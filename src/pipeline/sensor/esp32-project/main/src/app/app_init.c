/*
 * app_init.c - BOOT and INIT state handlers.
 */
#include "app_init.h"
#include "../../include/app_constants.h"

#include <string.h>
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "../peripherals/setup.h"
#include "../peripherals/headers/lis3dh.h"
#include "../peripherals/headers/adxl345.h"
#include "../peripherals/headers/ssd1306.h"
#include "../peripherals/headers/ina219.h"

static const char *TAG = "app_init";

/* Sensor handles shared across INIT/ACQUIRE/SEND. */
static lis3dh_handle_t  s_lis3dh;
static adxl345_handle_t s_adxl345;

/* OLED/INA219 are non-fatal, so their readiness is tracked separately from
 * cfg->{oled,ina219}.enabled: "enabled" means the user asked for it,
 * "ready" means it actually initialized this session. */
static ssd1306_handle_t s_oled;
static ina219_handle_t  s_ina219;
static bool s_oled_ready   = false;
static bool s_ina219_ready = false;

/* -------------------------------------------------------------------------
 * ODR bits helper
 * ------------------------------------------------------------------------- */
static uint8_t lis3dh_odr_to_bits(lis3dh_odr_t odr)
{
    switch (odr) {
    case LIS3DH_ODR_100HZ:    return LIS3DH_ODR_BITS_100HZ;
    case LIS3DH_ODR_200HZ:    return LIS3DH_ODR_BITS_200HZ;
    case LIS3DH_ODR_400HZ:    return LIS3DH_ODR_BITS_400HZ;
    case LIS3DH_ODR_1344HZ:   return LIS3DH_ODR_BITS_1344HZ;
    case LIS3DH_ODR_1620HZ_LP: return LIS3DH_ODR_BITS_1620HZ_LP;
    case LIS3DH_ODR_5376HZ_LP: return LIS3DH_ODR_BITS_5376HZ_LP;
    default:                   return LIS3DH_ODR_BITS_400HZ;
    }
}

static uint8_t lis3dh_fs_to_bits(lis3dh_fs_t fs)
{
    switch (fs) {
    case LIS3DH_FS_2G:  return LIS3DH_FS_BITS_2G;
    case LIS3DH_FS_4G:  return LIS3DH_FS_BITS_4G;
    case LIS3DH_FS_8G:  return LIS3DH_FS_BITS_8G;
    case LIS3DH_FS_16G: return LIS3DH_FS_BITS_16G;
    default:            return LIS3DH_FS_BITS_2G;
    }
}

static uint8_t adxl345_rate_to_bits(adxl345_rate_t rate)
{
    switch (rate) {
    case ADXL345_RATE_800HZ:  return ADXL345_RATE_BITS_800HZ;
    case ADXL345_RATE_1600HZ: return ADXL345_RATE_BITS_1600HZ;
    case ADXL345_RATE_3200HZ: return ADXL345_RATE_BITS_3200HZ;
    default:                  return ADXL345_RATE_BITS_800HZ;
    }
}

/* -------------------------------------------------------------------------
 * Public: app_do_boot
 * ------------------------------------------------------------------------- */
app_state_t app_do_boot(config_t *cfg)
{
    esp_err_t err = app_setup_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "app_setup_init failed: %s", esp_err_to_name(err));
        return APP_STATE_BOOT;
    }

    /* Initialize TCP/IP stack once. */
    esp_netif_init();
    esp_event_loop_create_default();

    err = app_config_load(cfg);
    if (err == ESP_ERR_NOT_FOUND) {
        /* No config saved - run captive portal. */
        ESP_LOGI(TAG, "No config found, starting captive portal");
        err = app_captive_portal_start(cfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Captive portal failed: %s", esp_err_to_name(err));
            return APP_STATE_BOOT;
        }
    } else if (err != ESP_OK) {
        ESP_LOGE(TAG, "Config load failed: %s", esp_err_to_name(err));
        return APP_STATE_BOOT;
    }

    return APP_STATE_INIT;
}

/* -------------------------------------------------------------------------
 * Public: app_do_init
 * ------------------------------------------------------------------------- */
app_state_t app_do_init(config_t *cfg)
{
    /* Validate: at least one sensor must be enabled. */
    if (!cfg->lis3dh.enabled && !cfg->adxl345.enabled) {
        ESP_LOGE(TAG, "No sensors enabled - staying in INIT");
        return APP_STATE_INIT;
    }

    /* Connect to WiFi. */
    esp_err_t err = app_wifi_connect(cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WiFi connect failed: %s", esp_err_to_name(err));
        return APP_STATE_INIT;
    }

    /* Initialize LIS3DH if enabled. */
    if (cfg->lis3dh.enabled) {
        uint8_t odr_bits = lis3dh_odr_to_bits(cfg->lis3dh.odr);
        uint8_t fs_bits  = lis3dh_fs_to_bits(cfg->lis3dh.full_scale);

        err = lis3dh_init(&s_lis3dh,
                          cfg->lis3dh.cs_pin,
                          cfg->lis3dh.mosi_pin,
                          cfg->lis3dh.miso_pin,
                          cfg->lis3dh.sclk_pin,
                          odr_bits, fs_bits);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "LIS3DH init failed: %s", esp_err_to_name(err));
            return APP_STATE_INIT;
        }
    }

    /* Initialize ADXL345 if enabled. */
    if (cfg->adxl345.enabled) {
        uint8_t rate_bits = adxl345_rate_to_bits(cfg->adxl345.data_rate);
        err = adxl345_init(&s_adxl345, cfg->adxl345.cs_pin, rate_bits);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ADXL345 init failed: %s", esp_err_to_name(err));
            return APP_STATE_INIT;
        }
    }

    /* OLED init is non-fatal: log and continue on failure. */
    if (cfg->oled.enabled) {
        esp_err_t oled_err = ssd1306_init(&s_oled, cfg->oled.sda_pin, cfg->oled.scl_pin);
        if (oled_err == ESP_OK) {
            s_oled_ready = true;
            ESP_LOGI(TAG, "OLED ready on SDA=%d SCL=%d",
                     cfg->oled.sda_pin, cfg->oled.scl_pin);
        } else {
            ESP_LOGW(TAG, "OLED init failed (non-fatal): %s", esp_err_to_name(oled_err));
        }
    }

    /* INA219 init is non-fatal: log and continue on failure. */
    if (cfg->ina219.enabled) {
        esp_err_t ina219_err = ina219_init(&s_ina219, cfg->ina219.sda_pin, cfg->ina219.scl_pin);
        if (ina219_err == ESP_OK) {
            s_ina219_ready = true;
            ESP_LOGI(TAG, "INA219 ready on SDA=%d SCL=%d",
                     cfg->ina219.sda_pin, cfg->ina219.scl_pin);
        } else {
            ESP_LOGW(TAG, "INA219 init failed (non-fatal): %s", esp_err_to_name(ina219_err));
        }
    }

    ESP_LOGI(TAG, "INIT complete");
    return APP_STATE_ACQUIRE;
}

/* -------------------------------------------------------------------------
 * Accessor for sensor handles (used by app_acquire.c)
 * ------------------------------------------------------------------------- */
lis3dh_handle_t  *app_init_get_lis3dh(void)  { return &s_lis3dh;  }
adxl345_handle_t *app_init_get_adxl345(void) { return &s_adxl345; }
ssd1306_handle_t *app_init_get_oled(void)    { return s_oled_ready   ? &s_oled   : NULL; }
ina219_handle_t  *app_init_get_ina219(void)  { return s_ina219_ready ? &s_ina219 : NULL; }
