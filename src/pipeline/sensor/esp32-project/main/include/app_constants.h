/*
 * app_constants.h - Central constants for the edge PDM sensor firmware.
 * No magic numbers in source files; all tunable values live here.
 */
#ifndef APP_CONSTANTS_H
#define APP_CONSTANTS_H

/* -------------------------------------------------------------------------
 * Buffer / queue sizes
 * ------------------------------------------------------------------------- */
#define APP_BINARY_WRITE_BUFFER_BYTES   512u
#define APP_SAMPLE_QUEUE_LENGTH         32u

/* Streaming chunk size used when converting binary records to CSV lines */
#define APP_CSV_CHUNK_BYTES             4096u

/* Maximum bytes per line when formatting one binary record as CSV text */
#define APP_CSV_LINE_MAX_BYTES          32u

/* One binary sample record: int16 x, int16 y, int16 z */
#define APP_BINARY_RECORD_BYTES         6u

/* -------------------------------------------------------------------------
 * NVS / SPIFFS paths
 * ------------------------------------------------------------------------- */
#define APP_NVS_NAMESPACE               "edge_cfg"
#define APP_NVS_KEY                     "state_json"
#define APP_SPIFFS_FALLBACK_PATH        "/spiffs/app_state.json"

/* Schema version embedded in every saved JSON config */
#define APP_CONFIG_SCHEMA_VERSION       1

/* -------------------------------------------------------------------------
 * Sensor SPI defaults
 * ------------------------------------------------------------------------- */
#define APP_LIS3DH_DEFAULT_CS_PIN       10
#define APP_LIS3DH_DEFAULT_MOSI_PIN     11
#define APP_LIS3DH_DEFAULT_MISO_PIN     13
#define APP_LIS3DH_DEFAULT_SCLK_PIN     12

#define APP_ADXL345_DEFAULT_CS_PIN      7

/* -------------------------------------------------------------------------
 * I2C defaults (OLED / INA219)
 * ------------------------------------------------------------------------- */
#define APP_DEFAULT_I2C_SDA_PIN         21
#define APP_DEFAULT_I2C_SCL_PIN         22

/* -------------------------------------------------------------------------
 * MQTT defaults
 * ------------------------------------------------------------------------- */
#define APP_MQTT_DEFAULT_HOST           "192.168.1.100"
#define APP_MQTT_DEFAULT_PORT           1883
#define APP_MQTT_DEFAULT_BATCH_SIZE     64
#define APP_MQTT_TOPIC_INPUT            "pipeline/input"
#define APP_MQTT_TOPIC_RESULT           "pipeline/result"
#define APP_MQTT_RESULT_TIMEOUT_MS      30000

/* -------------------------------------------------------------------------
 * Task priorities and stack sizes
 * ------------------------------------------------------------------------- */
#define APP_READER_PRIORITY             10
#define APP_LIS3DH_WRT_PRIORITY         4
#define APP_ADXL345_WRT_PRIORITY        4
#define APP_OLED_PRIORITY               1
#define APP_WAKEUP_PRIORITY             3

#define APP_READER_STACK_SIZE           4096u
#define APP_LIS3DH_WRT_STACK_SIZE       4096u
#define APP_ADXL345_WRT_STACK_SIZE      4096u
#define APP_OLED_STACK_SIZE             2048u
#define APP_WAKEUP_STACK_SIZE           2048u

/* -------------------------------------------------------------------------
 * Miscellaneous
 * ------------------------------------------------------------------------- */
#define APP_SHA256_HEX_LEN              65   /* 32 bytes * 2 hex chars + NUL */
#define APP_SENSOR_ID_MAX_LEN           64
#define APP_FILE_ID_MAX_LEN             64
#define APP_LABEL_MAX_LEN               64

#endif /* APP_CONSTANTS_H */
