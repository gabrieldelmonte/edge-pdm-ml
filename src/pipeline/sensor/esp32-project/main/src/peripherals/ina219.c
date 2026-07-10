#include "headers/ina219.h"
#include "headers/i2c_bus.h"

#include "esp_log.h"

#define INA219_REGISTER_CONFIG      0x00
#define INA219_REGISTER_CURRENT     0x04
#define INA219_REGISTER_CALIBRATION 0x05

/* BRNG=1 (32V), PG=11 (+-320mV), BADC=0011 (12-bit), SADC=0011 (12-bit),
 * MODE=111 (shunt+bus, continuous). Matches the power-on default (0x399F)
 * but is written explicitly. */
#define INA219_CONFIG_VALUE      0x399F
/* R_SHUNT=0.1 ohm, I_MAX=3.2A. */
#define INA219_CALIBRATION_VALUE 4096
#define INA219_CURRENT_LSB_MA    0.1f

#define INA219_I2C_TIMEOUT_MS 50
#define INA219_I2C_CLOCK_HZ   400000

static const char *TAG = "ina219";

static esp_err_t ina219_write_register(ina219_handle_t *handle, uint8_t reg, uint16_t value)
{
    uint8_t buf[3] = { reg, (uint8_t)(value >> 8), (uint8_t)(value & 0xFF) };
    return i2c_master_transmit(handle->dev, buf, sizeof(buf), INA219_I2C_TIMEOUT_MS);
}

static esp_err_t ina219_read_register(ina219_handle_t *handle, uint8_t reg, uint16_t *value)
{
    uint8_t rx[2] = {0};
    esp_err_t err = i2c_master_transmit_receive(handle->dev, &reg, 1, rx, sizeof(rx),
                                                INA219_I2C_TIMEOUT_MS);
    if (err != ESP_OK) {
        return err;
    }
    *value = ((uint16_t)rx[0] << 8) | rx[1];
    return ESP_OK;
}

esp_err_t ina219_init(ina219_handle_t *handle, int sda_pin, int scl_pin)
{
    i2c_master_bus_handle_t bus;
    esp_err_t err = i2c_bus_acquire(sda_pin, scl_pin, &bus);
    if (err != ESP_OK) {
        return err;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = INA219_DEFAULT_I2C_ADDRESS,
        .scl_speed_hz    = INA219_I2C_CLOCK_HZ,
    };
    err = i2c_master_bus_add_device(bus, &dev_cfg, &handle->dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_master_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }

    err = ina219_write_register(handle, INA219_REGISTER_CONFIG, INA219_CONFIG_VALUE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "write CONFIG failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Current/power registers stay zero until calibration is programmed. */
    err = ina219_write_register(handle, INA219_REGISTER_CALIBRATION, INA219_CALIBRATION_VALUE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "write CALIBRATION failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "initialized at 0x%02X (R_SHUNT=0.1 ohm, I_MAX=3.2A, LSB=0.1mA)",
             INA219_DEFAULT_I2C_ADDRESS);
    return ESP_OK;
}

esp_err_t ina219_read_current_ma(ina219_handle_t *handle, float *current_ma)
{
    uint16_t raw = 0;
    esp_err_t err = ina219_read_register(handle, INA219_REGISTER_CURRENT, &raw);
    if (err != ESP_OK) {
        return err;
    }

    /* Current register is signed 16-bit (two's complement). */
    *current_ma = (float)(int16_t)raw * INA219_CURRENT_LSB_MA;
    return ESP_OK;
}

void ina219_deinit(ina219_handle_t *handle)
{
    if (handle->dev) {
        i2c_master_bus_rm_device(handle->dev);
        handle->dev = NULL;
    }
}
