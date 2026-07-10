#include "headers/ssd1306.h"
#include "headers/i2c_bus.h"
#include "headers/latin_font.h"

#include <string.h>
#include "esp_log.h"

#define SSD1306_CTRL_COMMAND 0x00
#define SSD1306_CTRL_DATA    0x40

#define SSD1306_CMD_SET_CONTRAST       0x81
#define SSD1306_CMD_SET_CHARGE_PUMP    0x8D
#define SSD1306_CMD_ENTIRE_DISPLAY_ON  0xA4
#define SSD1306_CMD_DISPLAY_NORMAL     0xA6
#define SSD1306_CMD_DISPLAY_OFF        0xAE
#define SSD1306_CMD_DISPLAY_ON         0xAF
#define SSD1306_CMD_SET_PAGE_ADDR_MODE 0x02
#define SSD1306_CMD_SET_MEM_ADDR_MODE  0x20
#define SSD1306_CMD_SET_START_LINE     0x40
#define SSD1306_CMD_SET_SEGMENT_REMAP  0xA1
#define SSD1306_CMD_SET_SCAN_MODE      0xC8
#define SSD1306_CMD_SET_DISPLAY_OFFSET 0xD3
#define SSD1306_CMD_DEACTIVATE_SCROLL  0x2E

#define SSD1306_I2C_TIMEOUT_MS 50
#define SSD1306_I2C_CLOCK_HZ   400000

static const char *TAG = "ssd1306";

/* control byte + up to 15 command bytes per transaction (init sequence is the longest). */
#define SSD1306_MAX_COMMAND_BYTES 15

static esp_err_t ssd1306_send_commands(ssd1306_handle_t *handle, const uint8_t *cmds, size_t len)
{
    if (len > SSD1306_MAX_COMMAND_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t buf[1 + SSD1306_MAX_COMMAND_BYTES];
    buf[0] = SSD1306_CTRL_COMMAND;
    memcpy(&buf[1], cmds, len);
    return i2c_master_transmit(handle->dev, buf, len + 1, SSD1306_I2C_TIMEOUT_MS);
}

static esp_err_t ssd1306_set_cursor(ssd1306_handle_t *handle, uint8_t col_px, uint8_t row)
{
    uint8_t cmds[3] = {
        (uint8_t)(0x00 | (col_px & 0x0F)),
        (uint8_t)(0x10 | ((col_px >> 4) & 0x0F)),
        (uint8_t)(0xB0 | (row & 0x0F)),
    };
    return ssd1306_send_commands(handle, cmds, sizeof(cmds));
}

esp_err_t ssd1306_init(ssd1306_handle_t *handle, int sda_pin, int scl_pin)
{
    i2c_master_bus_handle_t bus;
    esp_err_t err = i2c_bus_acquire(sda_pin, scl_pin, &bus);
    if (err != ESP_OK) {
        return err;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = SSD1306_DEFAULT_I2C_ADDRESS,
        .scl_speed_hz    = SSD1306_I2C_CLOCK_HZ,
    };
    err = i2c_master_bus_add_device(bus, &dev_cfg, &handle->dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_master_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }

    uint8_t init_cmds[] = {
        SSD1306_CMD_DISPLAY_OFF,
        SSD1306_CMD_SET_MEM_ADDR_MODE, SSD1306_CMD_SET_PAGE_ADDR_MODE,
        SSD1306_CMD_DEACTIVATE_SCROLL,
        SSD1306_CMD_ENTIRE_DISPLAY_ON,
        SSD1306_CMD_DISPLAY_NORMAL,
        SSD1306_CMD_SET_START_LINE,
        SSD1306_CMD_SET_DISPLAY_OFFSET, 0x00,
        SSD1306_CMD_SET_CHARGE_PUMP, 0x14,
        SSD1306_CMD_SET_SEGMENT_REMAP,
        SSD1306_CMD_SET_SCAN_MODE,
        SSD1306_CMD_DISPLAY_ON,
    };
    err = ssd1306_send_commands(handle, init_cmds, sizeof(init_cmds));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "init command sequence failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "initialized at 0x%02X", SSD1306_DEFAULT_I2C_ADDRESS);
    return ssd1306_clear(handle);
}

esp_err_t ssd1306_write_line(ssd1306_handle_t *handle, uint8_t row, const char *text)
{
    if (row >= SSD1306_NUM_ROWS) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = ssd1306_set_cursor(handle, 0, row);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = strlen(text);
    if (len > SSD1306_NUM_COLS) {
        len = SSD1306_NUM_COLS;
    }

    uint8_t buf[1 + SSD1306_NUM_COLS * SSD1306_FONT_SIZE];
    buf[0] = SSD1306_CTRL_DATA;
    size_t pos = 1;
    for (size_t i = 0; i < len; i++) {
        memcpy(&buf[pos], latin_font[(uint8_t)text[i]], SSD1306_FONT_SIZE);
        pos += SSD1306_FONT_SIZE;
    }
    /* Blank remaining columns so a shorter string fully overwrites the row. */
    memset(&buf[pos], 0x00, (SSD1306_NUM_COLS - len) * SSD1306_FONT_SIZE);
    pos += (SSD1306_NUM_COLS - len) * SSD1306_FONT_SIZE;

    return i2c_master_transmit(handle->dev, buf, pos, SSD1306_I2C_TIMEOUT_MS);
}

esp_err_t ssd1306_clear(ssd1306_handle_t *handle)
{
    for (uint8_t row = 0; row < SSD1306_NUM_ROWS; row++) {
        esp_err_t err = ssd1306_write_line(handle, row, "");
        if (err != ESP_OK) {
            return err;
        }
    }
    return ESP_OK;
}

void ssd1306_deinit(ssd1306_handle_t *handle)
{
    if (handle->dev) {
        i2c_master_bus_rm_device(handle->dev);
        handle->dev = NULL;
    }
}
