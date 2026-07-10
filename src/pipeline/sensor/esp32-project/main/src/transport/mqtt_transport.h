/*
 * mqtt_transport.h - MQTT transport layer for the edge PDM sensor firmware.
 *
 * Publishes CSV data in batches to "pipeline/input" and waits for the
 * inference result on "pipeline/result".
 */
#ifndef APP_MQTT_TRANSPORT_H
#define APP_MQTT_TRANSPORT_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "../peripherals/setup.h"

/**
 * app_mqtt_init - Connect to the MQTT broker specified in config.
 *
 * @param config  Active device configuration.
 * @return        ESP_OK on successful connection, error otherwise.
 */
esp_err_t app_mqtt_init(config_t *config);

/**
 * app_mqtt_publish_batch - Publish one CSV chunk as a JSON message to
 * "pipeline/input".
 *
 * JSON format:
 * {
 *   "file_id":        "<file_id>",
 *   "capture_sequence": N,
 *   "sensor_id":      "<sensor_id>",
 *   "csv_data":       "<csv_chunk>",
 *   "checksum":       "<sha256_hex_of_chunk>",
 *   "current_ma":     X.X,
 *   "sample_rate_hz": N,
 *   "batch_index":    N,
 *   "is_last_batch":  true|false
 * }
 *
 * @param file_id         Unique identifier for the capture file.
 * @param capture_seq     Monotonic counter for this capture session.
 * @param sensor_id       Identifier string for the sensor (e.g. device name).
 * @param csv_chunk       Null-terminated CSV text for this batch.
 * @param checksum        SHA-256 hex string for this chunk (64 chars + NUL).
 *                        Pass an empty string "" for intermediate batches where
 *                        the hash is not yet finalised.
 * @param batch_index     Zero-based index of this batch.
 * @param is_last_batch   True if this is the final batch.
 * @param current_ma      INA219 reading in milliamps (0.0 if unavailable).
 * @param sample_rate_hz  Configured sample rate.
 * @return                ESP_OK on publish, error otherwise.
 */
esp_err_t app_mqtt_publish_batch(const char    *file_id,
                                 uint64_t       capture_seq,
                                 const char    *sensor_id,
                                 const char    *csv_chunk,
                                 const char    *checksum,
                                 int            batch_index,
                                 bool           is_last_batch,
                                 float          current_ma,
                                 uint32_t       sample_rate_hz);

/**
 * app_mqtt_subscribe_result - Subscribe to the "pipeline/result" topic.
 *
 * Must be called once after app_mqtt_init() and before
 * app_mqtt_wait_result().
 *
 * @return  ESP_OK on success.
 */
esp_err_t app_mqtt_subscribe_result(void);

/**
 * app_mqtt_wait_result - Block until an inference result arrives on
 * "pipeline/result" or timeout elapses.
 *
 * Expected payload: {"inference":"<label>"}
 *
 * @param out_label   Buffer to receive the null-terminated label string.
 * @param len         Size of out_label buffer.
 * @param timeout_ms  Maximum wait time in milliseconds.
 * @return            ESP_OK if label received, ESP_ERR_TIMEOUT on timeout,
 *                    error otherwise.
 */
esp_err_t app_mqtt_wait_result(char *out_label, size_t len, int timeout_ms);

/**
 * app_mqtt_deinit - Disconnect and free MQTT client resources.
 */
void app_mqtt_deinit(void);

#endif /* APP_MQTT_TRANSPORT_H */
