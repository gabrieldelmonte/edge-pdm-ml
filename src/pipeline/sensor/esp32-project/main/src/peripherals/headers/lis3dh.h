/*
 * lis3dh.h - LIS3DH 3-axis accelerometer SPI driver for ESP-IDF.
 *
 * Hardware connection:
 *   SPI2_HOST, CS=GPIO10, MOSI=GPIO11, MISO=GPIO13, SCLK=GPIO12
 *   Clock: 10 MHz (configured internally - do not expose to user)
 */
#ifndef APP_LIS3DH_H
#define APP_LIS3DH_H

#include <stdint.h>
#include "esp_err.h"
#include "driver/spi_master.h"

/* -------------------------------------------------------------------------
 * Register map (partial)
 * ------------------------------------------------------------------------- */
#define LIS3DH_REG_WHO_AM_I     0x0F
#define LIS3DH_WHO_AM_I_VAL     0x33

#define LIS3DH_REG_CTRL_REG1    0x20
#define LIS3DH_REG_CTRL_REG4    0x23
#define LIS3DH_REG_STATUS_REG   0x27
#define LIS3DH_REG_OUT_X_L      0x28

/* Read flag for SPI transactions */
#define LIS3DH_SPI_READ         0x80
/* Auto-increment flag */
#define LIS3DH_SPI_AUTOINC      0x40

/* -------------------------------------------------------------------------
 * ODR register encoding (CTRL_REG1 bits [7:4])
 * ------------------------------------------------------------------------- */
#define LIS3DH_ODR_BITS_100HZ       (0x05 << 4)
#define LIS3DH_ODR_BITS_200HZ       (0x06 << 4)
#define LIS3DH_ODR_BITS_400HZ       (0x07 << 4)
#define LIS3DH_ODR_BITS_1344HZ      (0x09 << 4)
#define LIS3DH_ODR_BITS_1620HZ_LP   (0x08 << 4)
#define LIS3DH_ODR_BITS_5376HZ_LP   (0x09 << 4)

/* -------------------------------------------------------------------------
 * Full-scale register encoding (CTRL_REG4 bits [5:4])
 * ------------------------------------------------------------------------- */
#define LIS3DH_FS_BITS_2G   (0x00 << 4)
#define LIS3DH_FS_BITS_4G   (0x01 << 4)
#define LIS3DH_FS_BITS_8G   (0x02 << 4)
#define LIS3DH_FS_BITS_16G  (0x03 << 4)

/* -------------------------------------------------------------------------
 * Driver handle
 * ------------------------------------------------------------------------- */
typedef struct {
    spi_device_handle_t spi;
    int cs_pin;
    int mosi_pin;
    int miso_pin;
    int sclk_pin;
} lis3dh_handle_t;

/* -------------------------------------------------------------------------
 * Raw sample
 * ------------------------------------------------------------------------- */
typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
} lis3dh_sample_t;

/* -------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------- */

/**
 * lis3dh_init - Initialize SPI bus and device, verify WHO_AM_I, configure
 * ODR and full-scale.
 *
 * @param handle     Output handle (caller allocates, driver fills).
 * @param cs_pin     GPIO for chip-select.
 * @param mosi_pin   GPIO for MOSI.
 * @param miso_pin   GPIO for MISO.
 * @param sclk_pin   GPIO for SCLK.
 * @param odr_bits   ODR bits for CTRL_REG1 (use LIS3DH_ODR_BITS_* macros).
 * @param fs_bits    Full-scale bits for CTRL_REG4 (use LIS3DH_FS_BITS_* macros).
 */
esp_err_t lis3dh_init(lis3dh_handle_t *handle,
                      int cs_pin, int mosi_pin, int miso_pin, int sclk_pin,
                      uint8_t odr_bits, uint8_t fs_bits);

/**
 * lis3dh_read_sample - Read one X/Y/Z sample from the output registers.
 */
esp_err_t lis3dh_read_sample(lis3dh_handle_t *handle, lis3dh_sample_t *out);

/**
 * lis3dh_data_ready - Return true when STATUS_REG indicates new data.
 */
bool lis3dh_data_ready(lis3dh_handle_t *handle);

/**
 * lis3dh_deinit - Release SPI device and free resources.
 */
void lis3dh_deinit(lis3dh_handle_t *handle);

#endif /* APP_LIS3DH_H */
