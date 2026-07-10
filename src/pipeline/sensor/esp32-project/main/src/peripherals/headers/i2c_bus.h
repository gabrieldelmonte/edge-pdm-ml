/*
 * i2c_bus.h - Shared I2C master bus singleton for the edge PDM sensor firmware.
 *
 * The OLED (SSD1306) and current monitor (INA219) are typically wired to the
 * same SDA/SCL pins, so they must share one bus handle rather than each
 * creating its own (the ESP-IDF I2C master driver only allows one bus per
 * controller). i2c_bus_acquire() lazily creates a bus for a given pin pair
 * and returns the same handle on subsequent calls with the same pins.
 */
#ifndef APP_I2C_BUS_H
#define APP_I2C_BUS_H

#include "esp_err.h"
#include "driver/i2c_master.h"

/**
 * i2c_bus_acquire - Return the master bus handle for the given SDA/SCL
 * pins, creating it on first use.
 *
 * @param sda_pin     I2C SDA GPIO.
 * @param scl_pin     I2C SCL GPIO.
 * @param out_handle  Output: bus handle (shared across callers using the
 *                    same pin pair).
 * @return            ESP_OK on success, ESP_ERR_NO_MEM if the bus slot
 *                    table is full (see I2C_BUS_MAX_INSTANCES).
 */
esp_err_t i2c_bus_acquire(int sda_pin, int scl_pin, i2c_master_bus_handle_t *out_handle);

#endif /* APP_I2C_BUS_H */
