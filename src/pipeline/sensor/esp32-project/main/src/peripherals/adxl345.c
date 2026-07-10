/*
 * adxl345.c - ADXL345 SPI driver implementation for ESP-IDF.
 *
 * SPI clock is fixed at 5 MHz internally.
 */
#include "headers/adxl345.h"

#include <string.h>
#include <stdlib.h>
#include "esp_log.h"
#include "driver/spi_master.h"

static const char *TAG = "adxl345";

/* SPI clock: 5 MHz (not user-configurable) */
#define ADXL345_SPI_CLOCK_HZ    (5 * 1000 * 1000)

/* -------------------------------------------------------------------------
 * Static helpers
 * ------------------------------------------------------------------------- */
static esp_err_t spi_write_reg(adxl345_handle_t *h, uint8_t reg, uint8_t val)
{
    spi_transaction_t tx = {
        .length    = 16,
        .tx_buffer = (uint8_t[]){reg & 0x3F, val},
    };
    return spi_device_transmit(h->spi, &tx);
}

static esp_err_t spi_read_reg(adxl345_handle_t *h, uint8_t reg, uint8_t *out)
{
    uint8_t tx_buf[2] = {(uint8_t)(reg | ADXL345_SPI_READ), 0x00};
    uint8_t rx_buf[2] = {0};
    spi_transaction_t tx = {
        .length    = 16,
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
    };
    esp_err_t err = spi_device_transmit(h->spi, &tx);
    if (err == ESP_OK) {
        *out = rx_buf[1];
    }
    return err;
}

static esp_err_t spi_read_burst(adxl345_handle_t *h, uint8_t reg,
                                 uint8_t *out, size_t len)
{
    uint8_t cmd = (uint8_t)(reg | ADXL345_SPI_READ | ADXL345_SPI_MULTI);
    size_t total = len + 1;
    uint8_t *tx_buf = calloc(total, 1);
    uint8_t *rx_buf = calloc(total, 1);
    if (!tx_buf || !rx_buf) {
        free(tx_buf);
        free(rx_buf);
        return ESP_ERR_NO_MEM;
    }
    tx_buf[0] = cmd;

    spi_transaction_t t = {
        .length    = total * 8,
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
    };
    esp_err_t err = spi_device_transmit(h->spi, &t);
    if (err == ESP_OK) {
        memcpy(out, rx_buf + 1, len);
    }
    free(tx_buf);
    free(rx_buf);
    return err;
}

/* -------------------------------------------------------------------------
 * Public: adxl345_init
 * ------------------------------------------------------------------------- */
esp_err_t adxl345_init(adxl345_handle_t *handle, int cs_pin, uint8_t rate_bits)
{
    memset(handle, 0, sizeof(*handle));
    handle->cs_pin = cs_pin;

    /* SPI2_HOST bus should already be initialized by LIS3DH or app. */
    spi_device_interface_config_t dev_cfg = {
        .clock_speed_hz  = ADXL345_SPI_CLOCK_HZ,
        .mode            = 3,
        .spics_io_num    = cs_pin,
        .queue_size      = 4,
    };
    esp_err_t err = spi_bus_add_device(SPI2_HOST, &dev_cfg, &handle->spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Verify DEVID */
    uint8_t devid = 0;
    err = spi_read_reg(handle, ADXL345_REG_DEVID, &devid);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "DEVID read failed: %s", esp_err_to_name(err));
        return err;
    }
    if (devid != ADXL345_DEVID_VAL) {
        ESP_LOGE(TAG, "Unexpected DEVID: 0x%02X (expected 0x%02X)",
                 devid, ADXL345_DEVID_VAL);
        return ESP_ERR_NOT_FOUND;
    }

    /* Set data rate */
    err = spi_write_reg(handle, ADXL345_REG_BW_RATE, rate_bits);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BW_RATE write failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Set full-resolution mode (DATA_FORMAT) */
    err = spi_write_reg(handle, ADXL345_REG_DATA_FORMAT, 0x08);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "DATA_FORMAT write failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Enable measurement */
    err = spi_write_reg(handle, ADXL345_REG_POWER_CTL, ADXL345_MEASURE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "POWER_CTL write failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "ADXL345 initialized (DEVID=0x%02X, rate=0x%02X)", devid, rate_bits);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: adxl345_read_sample
 * ------------------------------------------------------------------------- */
esp_err_t adxl345_read_sample(adxl345_handle_t *handle, adxl345_sample_t *out)
{
    uint8_t raw[6] = {0};
    esp_err_t err = spi_read_burst(handle, ADXL345_REG_DATAX0, raw, 6);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "adxl345_read_sample: burst read failed: %s", esp_err_to_name(err));
        return err;
    }
    out->x = (int16_t)((raw[1] << 8) | raw[0]);
    out->y = (int16_t)((raw[3] << 8) | raw[2]);
    out->z = (int16_t)((raw[5] << 8) | raw[4]);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: adxl345_deinit
 * ------------------------------------------------------------------------- */
void adxl345_deinit(adxl345_handle_t *handle)
{
    if (handle->spi) {
        spi_bus_remove_device(handle->spi);
        handle->spi = NULL;
    }
}
