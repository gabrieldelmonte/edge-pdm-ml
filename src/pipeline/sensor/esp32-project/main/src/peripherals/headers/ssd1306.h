/*
 * ssd1306.h - SSD1306 0.96" 128x64 OLED display I2C driver.
 */
#ifndef APP_SSD1306_H
#define APP_SSD1306_H

#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c_master.h"

#define SSD1306_DEFAULT_I2C_ADDRESS 0x3C

#define SSD1306_WIDTH      128
#define SSD1306_HEIGHT     64
#define SSD1306_FONT_SIZE  8
#define SSD1306_PAGE_SIZE  8

/* Text grid: 8 rows of 16 columns at one 8x8 glyph per character cell. */
#define SSD1306_NUM_ROWS (SSD1306_HEIGHT / SSD1306_PAGE_SIZE)
#define SSD1306_NUM_COLS (SSD1306_WIDTH  / SSD1306_FONT_SIZE)

typedef struct {
    i2c_master_dev_handle_t dev;
} ssd1306_handle_t;

/**
 * ssd1306_init - Attach to the shared I2C bus, run the display's power-on
 * command sequence, and clear the screen.
 *
 * @param handle   Output handle (caller allocates, driver fills).
 * @param sda_pin  I2C SDA GPIO.
 * @param scl_pin  I2C SCL GPIO.
 */
esp_err_t ssd1306_init(ssd1306_handle_t *handle, int sda_pin, int scl_pin);

/**
 * ssd1306_write_line - Overwrite text row `row` with `text`.
 *
 * Text is truncated to SSD1306_NUM_COLS characters; any remaining columns
 * on the row are blanked so a shorter string fully replaces the old one.
 *
 * @param handle  Initialized display handle.
 * @param row     Text row, 0 to SSD1306_NUM_ROWS - 1.
 * @param text    Null-terminated ASCII string.
 */
esp_err_t ssd1306_write_line(ssd1306_handle_t *handle, uint8_t row, const char *text);

/**
 * ssd1306_clear - Blank every text row.
 */
esp_err_t ssd1306_clear(ssd1306_handle_t *handle);

/**
 * ssd1306_deinit - Release the I2C device handle.
 */
void ssd1306_deinit(ssd1306_handle_t *handle);

#endif /* APP_SSD1306_H */
