/*
 * latin_font.h - 8x8 bitmap font covering U+0000 to U+00FF, used by ssd1306.c.
 */
#ifndef APP_LATIN_FONT_H
#define APP_LATIN_FONT_H

#include <stdint.h>

#define LATIN_FONT_COLUMNS_SIZE 8
#define LATIN_FONT_ROWS_SIZE    256

extern const uint8_t latin_font[LATIN_FONT_ROWS_SIZE][LATIN_FONT_COLUMNS_SIZE];

#endif /* APP_LATIN_FONT_H */
