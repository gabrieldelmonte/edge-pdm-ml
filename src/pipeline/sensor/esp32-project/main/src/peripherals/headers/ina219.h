/*
 * ina219.h - INA219 bidirectional current/power monitor I2C driver.
 *
 * Calibrated for a 0.1 ohm shunt resistor and a 3.2 A maximum current:
 *   Current_LSB = 3.2 / 32768 ~= 97.7 uA -> rounded to 100 uA (0.1 mA)
 *   Cal         = trunc(0.04096 / (0.0001 * 0.1)) = 4096
 */
#ifndef APP_INA219_H
#define APP_INA219_H

#include "esp_err.h"
#include "driver/i2c_master.h"

#define INA219_DEFAULT_I2C_ADDRESS 0x40

typedef struct {
    i2c_master_dev_handle_t dev;
} ina219_handle_t;

/**
 * ina219_init - Attach to the shared I2C bus and program the
 * configuration/calibration registers.
 *
 * @param handle   Output handle (caller allocates, driver fills).
 * @param sda_pin  I2C SDA GPIO.
 * @param scl_pin  I2C SCL GPIO.
 */
esp_err_t ina219_init(ina219_handle_t *handle, int sda_pin, int scl_pin);

/**
 * ina219_read_current_ma - Read the shunt current in milliamps (signed;
 * negative indicates reverse current flow).
 */
esp_err_t ina219_read_current_ma(ina219_handle_t *handle, float *current_ma);

/**
 * ina219_deinit - Release the I2C device handle.
 */
void ina219_deinit(ina219_handle_t *handle);

#endif /* APP_INA219_H */
