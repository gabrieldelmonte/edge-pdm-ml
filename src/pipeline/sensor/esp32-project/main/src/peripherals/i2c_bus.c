#include "headers/i2c_bus.h"

#include "esp_log.h"

/* ESP32-S3 has 2 I2C controllers; OLED and INA219 cover the only two
 * distinct pin pairs this firmware needs. */
#define I2C_BUS_MAX_INSTANCES 2
#define I2C_BUS_CLK_GLITCH_IGNORE_CNT 7

static const char *TAG = "i2c_bus";

typedef struct {
    int sda_pin;
    int scl_pin;
    i2c_master_bus_handle_t handle;
} i2c_bus_entry_t;

static i2c_bus_entry_t s_buses[I2C_BUS_MAX_INSTANCES];
static int             s_bus_count = 0;

esp_err_t i2c_bus_acquire(int sda_pin, int scl_pin, i2c_master_bus_handle_t *out_handle)
{
    if (!out_handle) {
        return ESP_ERR_INVALID_ARG;
    }

    for (int i = 0; i < s_bus_count; i++) {
        if (s_buses[i].sda_pin == sda_pin && s_buses[i].scl_pin == scl_pin) {
            *out_handle = s_buses[i].handle;
            return ESP_OK;
        }
    }

    if (s_bus_count >= I2C_BUS_MAX_INSTANCES) {
        ESP_LOGE(TAG, "no free bus slots for SDA=%d SCL=%d (max %d distinct pin pairs)",
                 sda_pin, scl_pin, I2C_BUS_MAX_INSTANCES);
        return ESP_ERR_NO_MEM;
    }

    i2c_master_bus_config_t bus_cfg = {
        .i2c_port          = -1, /* auto-select a free controller */
        .sda_io_num        = sda_pin,
        .scl_io_num        = scl_pin,
        .clk_source        = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = I2C_BUS_CLK_GLITCH_IGNORE_CNT,
        .flags.enable_internal_pullup = true,
    };

    i2c_master_bus_handle_t handle;
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_new_master_bus failed (SDA=%d SCL=%d): %s",
                 sda_pin, scl_pin, esp_err_to_name(err));
        return err;
    }

    s_buses[s_bus_count].sda_pin = sda_pin;
    s_buses[s_bus_count].scl_pin = scl_pin;
    s_buses[s_bus_count].handle  = handle;
    s_bus_count++;

    *out_handle = handle;
    return ESP_OK;
}
