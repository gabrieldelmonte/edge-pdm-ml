/*
 * adxl345.h - ADXL345 3-axis accelerometer SPI driver for ESP-IDF.
 *
 * Hardware connection:
 *   SPI2_HOST (shared with LIS3DH), CS=GPIO7
 *   Clock: 5 MHz (configured internally - do not expose to user)
 */
#ifndef APP_ADXL345_H
#define APP_ADXL345_H

#include <stdint.h>
#include "esp_err.h"
#include "driver/spi_master.h"

/* -------------------------------------------------------------------------
 * Register map (partial)
 * ------------------------------------------------------------------------- */
#define ADXL345_REG_DEVID       0x00
#define ADXL345_DEVID_VAL       0xE5

#define ADXL345_REG_BW_RATE     0x2C
#define ADXL345_REG_POWER_CTL   0x2D
#define ADXL345_REG_DATA_FORMAT 0x31
#define ADXL345_REG_DATAX0      0x32

/* SPI read/write/multibyte flags */
#define ADXL345_SPI_READ        0x80
#define ADXL345_SPI_MULTI       0x40

/* POWER_CTL measure bit */
#define ADXL345_MEASURE         0x08

/* -------------------------------------------------------------------------
 * Data-rate encoding (BW_RATE register bits [3:0])
 * ------------------------------------------------------------------------- */
#define ADXL345_RATE_BITS_800HZ     0x0D
#define ADXL345_RATE_BITS_1600HZ    0x0E
#define ADXL345_RATE_BITS_3200HZ    0x0F

/* -------------------------------------------------------------------------
 * Driver handle
 * ------------------------------------------------------------------------- */
typedef struct {
    spi_device_handle_t spi;
    int cs_pin;
} adxl345_handle_t;

/* -------------------------------------------------------------------------
 * Raw sample
 * ------------------------------------------------------------------------- */
typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
} adxl345_sample_t;

/* -------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------- */

/**
 * adxl345_init - Add device to existing SPI bus, verify DEVID, configure
 * data rate and start measurement mode.
 *
 * @param handle       Output handle.
 * @param cs_pin       GPIO for chip-select.
 * @param rate_bits    Data rate bits (use ADXL345_RATE_BITS_* macros).
 */
esp_err_t adxl345_init(adxl345_handle_t *handle, int cs_pin, uint8_t rate_bits);

/**
 * adxl345_read_sample - Read one X/Y/Z sample.
 */
esp_err_t adxl345_read_sample(adxl345_handle_t *handle, adxl345_sample_t *out);

/**
 * adxl345_deinit - Remove device from SPI bus.
 */
void adxl345_deinit(adxl345_handle_t *handle);

#endif /* APP_ADXL345_H */
