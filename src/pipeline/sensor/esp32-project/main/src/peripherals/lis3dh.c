/*
 * lis3dh.c - LIS3DH SPI driver implementation for ESP-IDF.
 *
 * SPI clock is fixed at 10 MHz internally.
 */
#include "headers/lis3dh.h"

#include <string.h>
#include "esp_log.h"
#include "driver/spi_master.h"

static const char *TAG = "lis3dh";

/* SPI clock speed: 10 MHz (not user-configurable) */
#define LIS3DH_SPI_CLOCK_HZ     (10 * 1000 * 1000)

/* STATUS_REG: bit 3 = ZYXDA (new data available on all axes) */
#define LIS3DH_STATUS_ZYXDA     0x08

/* -------------------------------------------------------------------------
 * Static helpers
 * ------------------------------------------------------------------------- */
static esp_err_t spi_write_reg(lis3dh_handle_t *h, uint8_t reg, uint8_t val)
{
    spi_transaction_t tx = {
        .length    = 16,
        .tx_buffer = (uint8_t[]){reg & 0x3F, val},
    };
    return spi_device_transmit(h->spi, &tx);
}

static esp_err_t spi_read_reg(lis3dh_handle_t *h, uint8_t reg, uint8_t *out)
{
    uint8_t tx_buf[2] = {(uint8_t)(reg | LIS3DH_SPI_READ), 0x00};
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

static esp_err_t spi_read_burst(lis3dh_handle_t *h, uint8_t reg,
                                 uint8_t *out, size_t len)
{
    uint8_t cmd = (uint8_t)(reg | LIS3DH_SPI_READ | LIS3DH_SPI_AUTOINC);
    /* +1 for the command byte */
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
 * Public: lis3dh_init
 * ------------------------------------------------------------------------- */
esp_err_t lis3dh_init(lis3dh_handle_t *handle,
                      int cs_pin, int mosi_pin, int miso_pin, int sclk_pin,
                      uint8_t odr_bits, uint8_t fs_bits)
{
    memset(handle, 0, sizeof(*handle));
    handle->cs_pin   = cs_pin;
    handle->mosi_pin = mosi_pin;
    handle->miso_pin = miso_pin;
    handle->sclk_pin = sclk_pin;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num     = mosi_pin,
        .miso_io_num     = miso_pin,
        .sclk_io_num     = sclk_pin,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = 64,
    };

    /* SPI2_HOST may already be initialized; ignore ESP_ERR_INVALID_STATE. */
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "spi_bus_initialize failed: %s", esp_err_to_name(err));
        return err;
    }

    spi_device_interface_config_t dev_cfg = {
        .clock_speed_hz  = LIS3DH_SPI_CLOCK_HZ,
        .mode            = 3,
        .spics_io_num    = cs_pin,
        .queue_size      = 4,
    };
    err = spi_bus_add_device(SPI2_HOST, &dev_cfg, &handle->spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Verify WHO_AM_I */
    uint8_t who_am_i = 0;
    err = spi_read_reg(handle, LIS3DH_REG_WHO_AM_I, &who_am_i);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WHO_AM_I read failed: %s", esp_err_to_name(err));
        return err;
    }
    if (who_am_i != LIS3DH_WHO_AM_I_VAL) {
        ESP_LOGE(TAG, "Unexpected WHO_AM_I: 0x%02X (expected 0x%02X)",
                 who_am_i, LIS3DH_WHO_AM_I_VAL);
        return ESP_ERR_NOT_FOUND;
    }

    /* CTRL_REG1: set ODR and enable all axes (XYZ) */
    err = spi_write_reg(handle, LIS3DH_REG_CTRL_REG1,
                        (uint8_t)(odr_bits | 0x07));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CTRL_REG1 write failed: %s", esp_err_to_name(err));
        return err;
    }

    /* CTRL_REG4: set full-scale and enable BDU (block data update) */
    err = spi_write_reg(handle, LIS3DH_REG_CTRL_REG4,
                        (uint8_t)(fs_bits | 0x80));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CTRL_REG4 write failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "LIS3DH initialized (WHO_AM_I=0x%02X, ODR bits=0x%02X, FS bits=0x%02X)",
             who_am_i, odr_bits, fs_bits);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: lis3dh_data_ready
 * ------------------------------------------------------------------------- */
bool lis3dh_data_ready(lis3dh_handle_t *handle)
{
    uint8_t status = 0;
    if (spi_read_reg(handle, LIS3DH_REG_STATUS_REG, &status) != ESP_OK) {
        return false;
    }
    return (status & LIS3DH_STATUS_ZYXDA) != 0;
}

/* -------------------------------------------------------------------------
 * Public: lis3dh_read_sample
 * ------------------------------------------------------------------------- */
esp_err_t lis3dh_read_sample(lis3dh_handle_t *handle, lis3dh_sample_t *out)
{
    uint8_t raw[6] = {0};
    esp_err_t err = spi_read_burst(handle, LIS3DH_REG_OUT_X_L, raw, 6);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "lis3dh_read_sample: burst read failed: %s", esp_err_to_name(err));
        return err;
    }
    out->x = (int16_t)((raw[1] << 8) | raw[0]);
    out->y = (int16_t)((raw[3] << 8) | raw[2]);
    out->z = (int16_t)((raw[5] << 8) | raw[4]);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: lis3dh_deinit
 * ------------------------------------------------------------------------- */
void lis3dh_deinit(lis3dh_handle_t *handle)
{
    if (handle->spi) {
        spi_bus_remove_device(handle->spi);
        handle->spi = NULL;
    }
}
