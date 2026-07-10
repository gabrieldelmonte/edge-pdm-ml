/*
 * app_send.c - SEND_DATA state handler.
 *
 * Streams binary records from SPIFFS as CSV without holding the full file
 * in RAM.  Computes SHA-256 incrementally per chunk using the PSA Crypto API
 * (mbedtls_sha256_context is not in the public include path in IDF v6.0).
 * Branches on config->transport_mode:
 *
 *   TRANSPORT_HTTPS - Two-pass POST to
 *       http://<middleware_host>:<middleware_port>/api/ingest.
 *       Pass 1 reads the binary file to compute SHA-256 and total escaped
 *       CSV byte count so the request can be opened with an exact
 *       Content-Length header (avoids chunked transfer encoding, which
 *       proved unreliable over the ESP32 WiFi → Docker bridge path).
 *       Pass 2 streams the same data to the open connection.
 *       file_id and sensor_id must not contain JSON-special characters (the
 *       portal enforces alphanumeric-only names).
 *
 *   TRANSPORT_MQTT  - Publishes batches of cfg->mqtt_batch_size CSV rows
 *       (configurable via the captive portal, default APP_MQTT_DEFAULT_
 *       BATCH_SIZE) to the broker topic; each intermediate batch carries an
 *       empty checksum and the final batch carries the completed SHA-256
 *       digest. A long capture is split into many small messages rather
 *       than one large one to stay well under typical MQTT broker/client
 *       packet-size limits.
 */
#include "app_send.h"
#include "app_init.h"
#include "../../include/app_constants.h"
#include "../../include/memory_utils.h"
#include "../transport/mqtt_transport.h"
#include "../peripherals/setup.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <inttypes.h>
#include <unistd.h>

#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_timer.h"
#include "psa/crypto.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "app_send";

/* -------------------------------------------------------------------------
 * Static helpers
 * ------------------------------------------------------------------------- */

/* Convert 32-byte SHA-256 digest to 64-char lowercase hex string + NUL. */
static void sha256_to_hex(const uint8_t digest[32],
                           char out[APP_SHA256_HEX_LEN])
{
    static const char HEX[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2]     = HEX[(digest[i] >> 4) & 0x0F];
        out[i * 2 + 1] = HEX[digest[i] & 0x0F];
    }
    out[64] = '\0';
}

/* Extract the "inference" field from a server JSON response, e.g.
 * {"inference":"Normal", ...}. Mirrors the parsing already used on the
 * MQTT result topic so both transports log the verdict the same way. */
static bool extract_inference_label(const char *json, char *out, size_t out_len)
{
    const char *key = "\"inference\":\"";
    const char *pos = strstr(json, key);
    if (!pos) {
        return false;
    }
    pos += strlen(key);
    const char *end = strchr(pos, '"');
    if (!end) {
        return false;
    }
    size_t llen = (size_t)(end - pos);
    if (llen >= out_len) {
        llen = out_len - 1;
    }
    memcpy(out, pos, llen);
    out[llen] = '\0';
    return true;
}

/*
 * send_sensor_file - Send one sensor's binary file as a JSON POST.
 *
 * HTTPS path: two passes over the binary file.
 *   Pass 1 computes SHA-256 and the total escaped CSV byte count so the
 *   request can be opened with an exact Content-Length (no chunked TE).
 *   Pass 2 streams the data through 4 KB buffers to the open connection.
 *
 * MQTT path: single pass; publishes 4 KB batches to the broker.
 */
static esp_err_t send_sensor_file(const char  *bin_path,
                                  config_t    *cfg,
                                  const char  *file_id,
                                  const char  *sensor_id,
                                  uint64_t     capture_seq,
                                  float        current_ma,
                                  uint32_t     sample_rate_hz)
{
    char    line[APP_CSV_LINE_MAX_BYTES];
    uint8_t rec[APP_BINARY_RECORD_BYTES];

    /* =========================================================
     * HTTPS: two-pass, Content-Length
     * ========================================================= */
    if (cfg->transport_mode == TRANSPORT_HTTPS) {
        int64_t t_start = esp_timer_get_time();

        char url[256];
        snprintf(url, sizeof(url), "http://%s:%" PRIu16 "/api/ingest",
                 cfg->middleware_host, cfg->middleware_port);
        ESP_LOGI(TAG, "send_sensor_file: connecting to %s", url);

        /* Build JSON prefix. Fixed — does not depend on CSV data. */
        char prefix[512];
        int prefix_len = snprintf(prefix, sizeof(prefix),
            "{\"file_id\":\"%s\",\"capture_sequence\":%" PRIu64
            ",\"sensor_id\":\"%s\",\"current_ma\":%.2f"
            ",\"sample_rate_hz\":%" PRIu32 ",\"csv_data\":\"",
            file_id, capture_seq, sensor_id,
            (double)current_ma, sample_rate_hz);
        if (prefix_len <= 0 || (size_t)prefix_len >= sizeof(prefix)) {
            ESP_LOGE(TAG, "send_sensor_file: prefix format error");
            return ESP_FAIL;
        }

        /* --- Pass 1: SHA-256 + total escaped CSV length ------------------- */
        psa_hash_operation_t hash_op = PSA_HASH_OPERATION_INIT;
        psa_status_t psa_st = psa_crypto_init();
        if (psa_st != PSA_SUCCESS) {
            ESP_LOGE(TAG, "psa_crypto_init failed: %d", (int)psa_st);
            return ESP_FAIL;
        }
        psa_st = psa_hash_setup(&hash_op, PSA_ALG_SHA_256);
        if (psa_st != PSA_SUCCESS) {
            ESP_LOGE(TAG, "psa_hash_setup failed: %d", (int)psa_st);
            return ESP_FAIL;
        }

        FILE *f = fopen(bin_path, "rb");
        if (!f) {
            ESP_LOGE(TAG, "send_sensor_file: cannot open %s (pass 1)", bin_path);
            psa_hash_abort(&hash_op);
            return ESP_FAIL;
        }

        size_t total_csv_len = 0;
        int    pass1_ok      = 1;
        while (fread(rec, APP_BINARY_RECORD_BYTES, 1, f) == 1) {
            int16_t x = (int16_t)(((uint16_t)rec[1] << 8) | rec[0]);
            int16_t y = (int16_t)(((uint16_t)rec[3] << 8) | rec[2]);
            int16_t z = (int16_t)(((uint16_t)rec[5] << 8) | rec[4]);

            int raw_len = snprintf(line, sizeof(line), "%d,%d,%d\n", x, y, z);
            if (raw_len <= 0 || (size_t)raw_len >= sizeof(line)) {
                pass1_ok = 0; break;
            }
            psa_hash_update(&hash_op, (const uint8_t *)line, (size_t)raw_len);

            int esc_len = snprintf(line, sizeof(line), "%d,%d,%d\\n", x, y, z);
            if (esc_len <= 0 || (size_t)esc_len >= sizeof(line)) {
                pass1_ok = 0; break;
            }
            total_csv_len += (size_t)esc_len;
        }
        fclose(f);

        if (!pass1_ok) {
            ESP_LOGE(TAG, "send_sensor_file: pass 1 format error");
            psa_hash_abort(&hash_op);
            return ESP_FAIL;
        }

        uint8_t digest[32];
        size_t  hash_len = 0;
        psa_hash_finish(&hash_op, digest, sizeof(digest), &hash_len);
        char hex[APP_SHA256_HEX_LEN];
        sha256_to_hex(digest, hex);
        ESP_LOGI(TAG, "SHA-256 of %s: %s", sensor_id, hex);

        char suffix[128];
        int  suffix_len = snprintf(suffix, sizeof(suffix),
                                   "\",\"checksum\":\"%s\"}", hex);

        int total_body_len = prefix_len + (int)total_csv_len + suffix_len;
        ESP_LOGI(TAG, "send_sensor_file: body %d B "
                 "(prefix=%d csv=%u suffix=%d)",
                 total_body_len, prefix_len, (unsigned)total_csv_len, suffix_len);

        /* --- Open HTTP with exact Content-Length -------------------------- */
        esp_http_client_config_t http_cfg = {
            .url    = url,
            .method = HTTP_METHOD_POST,
        };
        esp_http_client_handle_t http_client = esp_http_client_init(&http_cfg);
        if (!http_client) {
            ESP_LOGE(TAG, "send_sensor_file: esp_http_client_init failed");
            return ESP_FAIL;
        }
        esp_http_client_set_header(http_client, "Content-Type", "application/json");

        esp_err_t open_err = esp_http_client_open(http_client, total_body_len);
        if (open_err != ESP_OK) {
            ESP_LOGE(TAG, "send_sensor_file: open failed: %s",
                     esp_err_to_name(open_err));
            esp_http_client_cleanup(http_client);
            return open_err;
        }

        if (esp_http_client_write(http_client, prefix, prefix_len) < 0) {
            ESP_LOGE(TAG, "send_sensor_file: prefix write failed");
            esp_http_client_cleanup(http_client);
            return ESP_FAIL;
        }

        /* --- Pass 2: stream CSV in 4 KB chunks ---------------------------- */
        char *chunk = (char *)spiram_malloc(APP_CSV_CHUNK_BYTES + 1);
        if (!chunk) {
            ESP_LOGE(TAG, "send_sensor_file: chunk alloc failed");
            esp_http_client_cleanup(http_client);
            return ESP_ERR_NO_MEM;
        }

        f = fopen(bin_path, "rb");
        if (!f) {
            ESP_LOGE(TAG, "send_sensor_file: cannot open %s (pass 2)", bin_path);
            free(chunk);
            esp_http_client_cleanup(http_client);
            return ESP_FAIL;
        }

        size_t    chunk_pos = 0;
        int       batch_idx = 0;
        esp_err_t result    = ESP_OK;

        while (fread(rec, APP_BINARY_RECORD_BYTES, 1, f) == 1) {
            int16_t x = (int16_t)(((uint16_t)rec[1] << 8) | rec[0]);
            int16_t y = (int16_t)(((uint16_t)rec[3] << 8) | rec[2]);
            int16_t z = (int16_t)(((uint16_t)rec[5] << 8) | rec[4]);

            int esc_len = snprintf(line, sizeof(line), "%d,%d,%d\\n", x, y, z);
            if (esc_len <= 0 || (size_t)esc_len >= sizeof(line)) {
                result = ESP_FAIL; break;
            }

            if (chunk_pos + (size_t)esc_len >= APP_CSV_CHUNK_BYTES) {
                if (esp_http_client_write(http_client, chunk, (int)chunk_pos) < 0) {
                    ESP_LOGE(TAG, "send_sensor_file: chunk %d write failed", batch_idx);
                    result = ESP_FAIL; break;
                }
                vTaskDelay(pdMS_TO_TICKS(10));
                batch_idx++;
                chunk_pos = 0;
            }
            memcpy(chunk + chunk_pos, line, (size_t)esc_len);
            chunk_pos += (size_t)esc_len;
        }
        fclose(f);

        /* Write last partial chunk. */
        if (result == ESP_OK && chunk_pos > 0) {
            if (esp_http_client_write(http_client, chunk, (int)chunk_pos) < 0) {
                ESP_LOGE(TAG, "send_sensor_file: final chunk write failed");
                result = ESP_FAIL;
            }
        }
        free(chunk);

        /* Write suffix to close the JSON string and object. */
        if (result == ESP_OK) {
            if (esp_http_client_write(http_client, suffix, suffix_len) < 0) {
                ESP_LOGE(TAG, "send_sensor_file: suffix write failed");
                result = ESP_FAIL;
            }
        }

        /* Read server response. Heap-allocated to avoid stack pressure at
         * the deepest call point (HTTP client is already open). */
        if (result == ESP_OK) {
            int64_t content_len = esp_http_client_fetch_headers(http_client);
            int     status      = esp_http_client_get_status_code(http_client);
            ESP_LOGI(TAG, "HTTPS response: status=%d len=%" PRId64,
                     status, content_len);

            char *resp_buf = (char *)spiram_malloc(512);
            if (resp_buf) {
                memset(resp_buf, 0, 512);
                int data_read = esp_http_client_read(
                    http_client, resp_buf, 511);
                if (data_read > 0) {
                    resp_buf[data_read] = '\0';
                    ESP_LOGI(TAG, "Inference: %s", resp_buf);

                    char label[APP_LABEL_MAX_LEN] = {0};
                    if (extract_inference_label(resp_buf, label, sizeof(label))) {
                        ESP_LOGI(TAG, "Inference result (%s): %s", sensor_id, label);
                    }
                }
                free(resp_buf);
            }

            if (status < 200 || status >= 300) {
                ESP_LOGE(TAG, "HTTPS ingest rejected, status=%d", status);
                result = ESP_FAIL;
            }
        }

        esp_http_client_cleanup(http_client);

        int64_t elapsed_ms = (esp_timer_get_time() - t_start) / 1000;
        ESP_LOGI(TAG, "send_sensor_file: %s HTTPS transfer complete in %lld ms",
                 sensor_id, (long long)elapsed_ms);
        return result;
    }

    /* =========================================================
     * MQTT: single-pass, batched by row count (cfg->mqtt_batch_size)
     * ========================================================= */
    int64_t t_start = esp_timer_get_time();

    /* mqtt_batch_size is rows per MQTT message, configurable via the
     * captive portal. Fall back to the firmware default if unset (e.g. a
     * config loaded before this field existed). */
    uint32_t batch_rows = (cfg->mqtt_batch_size > 0)
                          ? cfg->mqtt_batch_size
                          : APP_MQTT_DEFAULT_BATCH_SIZE;
    size_t   chunk_capacity = (size_t)batch_rows * APP_CSV_LINE_MAX_BYTES + 1;

    FILE *f = fopen(bin_path, "rb");
    if (!f) {
        ESP_LOGE(TAG, "send_sensor_file: cannot open %s", bin_path);
        return ESP_FAIL;
    }

    char *chunk = (char *)spiram_malloc(chunk_capacity);
    if (!chunk) {
        ESP_LOGE(TAG, "send_sensor_file: chunk alloc failed");
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    psa_hash_operation_t hash_op = PSA_HASH_OPERATION_INIT;
    psa_status_t psa_st = psa_crypto_init();
    if (psa_st != PSA_SUCCESS) {
        ESP_LOGE(TAG, "psa_crypto_init failed: %d", (int)psa_st);
        free(chunk);
        fclose(f);
        return ESP_FAIL;
    }
    psa_st = psa_hash_setup(&hash_op, PSA_ALG_SHA_256);
    if (psa_st != PSA_SUCCESS) {
        ESP_LOGE(TAG, "psa_hash_setup failed: %d", (int)psa_st);
        free(chunk);
        fclose(f);
        return ESP_FAIL;
    }

    size_t    chunk_pos   = 0;
    uint32_t  chunk_rows  = 0;
    int       batch_index = 0;
    esp_err_t result      = ESP_OK;

    while (fread(rec, APP_BINARY_RECORD_BYTES, 1, f) == 1) {
        int16_t x = (int16_t)(((uint16_t)rec[1] << 8) | rec[0]);
        int16_t y = (int16_t)(((uint16_t)rec[3] << 8) | rec[2]);
        int16_t z = (int16_t)(((uint16_t)rec[5] << 8) | rec[4]);

        int raw_len = snprintf(line, sizeof(line), "%d,%d,%d\n", x, y, z);
        if (raw_len <= 0 || (size_t)raw_len >= sizeof(line)) {
            result = ESP_FAIL; break;
        }
        psa_hash_update(&hash_op, (const uint8_t *)line, (size_t)raw_len);

        memcpy(chunk + chunk_pos, line, (size_t)raw_len);
        chunk_pos += (size_t)raw_len;
        chunk_rows++;

        if (chunk_rows >= batch_rows) {
            chunk[chunk_pos] = '\0';
            result = app_mqtt_publish_batch(file_id, capture_seq,
                                            sensor_id, chunk, "",
                                            batch_index, false,
                                            current_ma, sample_rate_hz);
            if (result != ESP_OK) {
                ESP_LOGE(TAG, "send_sensor_file: MQTT batch %d send failed",
                         batch_index);
                break;
            }
            batch_index++;
            chunk_pos  = 0;
            chunk_rows = 0;
        }
    }
    fclose(f);

    /* Always publish a closing message carrying the final SHA-256 digest,
     * even when the last flush above landed exactly on a batch boundary
     * (chunk_pos == 0). Without this, a capture whose row count is an exact
     * multiple of batch_rows would never send is_last_batch=true, leaving
     * the middleware's accumulator waiting until its TTL evicts it. */
    if (result == ESP_OK) {
        uint8_t digest[32];
        size_t  hash_len = 0;
        psa_hash_finish(&hash_op, digest, sizeof(digest), &hash_len);
        char hex[APP_SHA256_HEX_LEN];
        sha256_to_hex(digest, hex);
        ESP_LOGI(TAG, "SHA-256 of %s: %s", sensor_id, hex);

        chunk[chunk_pos] = '\0';
        result = app_mqtt_publish_batch(file_id, capture_seq,
                                        sensor_id, chunk, hex,
                                        batch_index, true,
                                        current_ma, sample_rate_hz);
    } else {
        psa_hash_abort(&hash_op);
    }

    free(chunk);

    int64_t elapsed_ms = (esp_timer_get_time() - t_start) / 1000;
    ESP_LOGI(TAG, "send_sensor_file: %s MQTT publish complete in %lld ms "
             "(%d batches, %u rows/batch)",
             sensor_id, (long long)elapsed_ms, batch_index + 1, batch_rows);
    return result;
}

/* -------------------------------------------------------------------------
 * Public: app_do_send
 * ------------------------------------------------------------------------- */
app_state_t app_do_send(config_t *cfg)
{
    esp_err_t err = ESP_OK;
    int64_t   t_cycle_start = esp_timer_get_time();
    const char *transport_str = (cfg->transport_mode == TRANSPORT_HTTPS) ? "HTTPS" : "MQTT";

    uint64_t capture_seq = (uint64_t)xTaskGetTickCount();

    float             current_ma = 0.0f;
    ina219_handle_t  *ina219     = cfg->ina219.enabled ? app_init_get_ina219() : NULL;
    if (ina219) {
        esp_err_t ina219_err = ina219_read_current_ma(ina219, &current_ma);
        if (ina219_err != ESP_OK) {
            ESP_LOGW(TAG, "ina219_read_current_ma failed: %s", esp_err_to_name(ina219_err));
            current_ma = 0.0f;
        }
    }

    /* Determine sample rate from LIS3DH ODR config (approximate). */
    static const uint32_t ODR_HZ[] = {100, 200, 400, 1344, 1620, 5376};
    uint32_t sample_rate_hz = (cfg->lis3dh.enabled &&
                               (int)cfg->lis3dh.odr < 6)
                              ? ODR_HZ[(int)cfg->lis3dh.odr]
                              : 800;

    /* Initialize MQTT transport if selected. */
    if (cfg->transport_mode == TRANSPORT_MQTT) {
        err = app_mqtt_init(cfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "app_mqtt_init failed: %s", esp_err_to_name(err));
            return APP_STATE_INIT;
        }
        err = app_mqtt_subscribe_result();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "app_mqtt_subscribe_result failed");
            app_mqtt_deinit();
            return APP_STATE_INIT;
        }
    }

    /* Send LIS3DH data if enabled. */
    if (cfg->lis3dh.enabled) {
        char lis3dh_file_id[APP_FILE_ID_MAX_LEN];
        char lis3dh_sensor_id[APP_SENSOR_ID_MAX_LEN];
        snprintf(lis3dh_file_id, sizeof(lis3dh_file_id), "%.32s-%lu-lis3dh",
                 cfg->device_name, (unsigned long)xTaskGetTickCount());
        snprintf(lis3dh_sensor_id, sizeof(lis3dh_sensor_id), "%.32s-lis3dh",
                 cfg->device_name);

        esp_err_t lis_err = send_sensor_file("/spiffs/lis3dh_samples.bin", cfg,
                                             lis3dh_file_id, lis3dh_sensor_id,
                                             capture_seq, current_ma, sample_rate_hz);
        if (lis_err == ESP_OK) {
            unlink("/spiffs/lis3dh_samples.bin");
        } else {
            ESP_LOGE(TAG, "LIS3DH send failed: %s", esp_err_to_name(lis_err));
            err = lis_err;
        }
    }

    /* Send ADXL345 data if enabled. */
    if (cfg->adxl345.enabled) {
        uint32_t adxl_rate_hz;
        switch (cfg->adxl345.data_rate) {
        case ADXL345_RATE_3200HZ: adxl_rate_hz = 3200u; break;
        case ADXL345_RATE_1600HZ: adxl_rate_hz = 1600u; break;
        default:                   adxl_rate_hz = 800u;  break;
        }
        char adxl345_file_id[APP_FILE_ID_MAX_LEN];
        char adxl345_sensor_id[APP_SENSOR_ID_MAX_LEN];
        snprintf(adxl345_file_id, sizeof(adxl345_file_id), "%.32s-%lu-adxl345",
                 cfg->device_name, (unsigned long)xTaskGetTickCount());
        snprintf(adxl345_sensor_id, sizeof(adxl345_sensor_id), "%.32s-adxl345",
                 cfg->device_name);

        esp_err_t adxl_err = send_sensor_file("/spiffs/adxl345_samples.bin", cfg,
                                              adxl345_file_id, adxl345_sensor_id,
                                              capture_seq, current_ma, adxl_rate_hz);
        if (adxl_err == ESP_OK) {
            unlink("/spiffs/adxl345_samples.bin");
        } else {
            ESP_LOGE(TAG, "ADXL345 send failed: %s", esp_err_to_name(adxl_err));
            err = adxl_err;
        }
    }

    /* Wait for inference result (MQTT only). */
    if (cfg->transport_mode == TRANSPORT_MQTT) {
        char label[APP_LABEL_MAX_LEN] = {0};
        err = app_mqtt_wait_result(label, sizeof(label), APP_MQTT_RESULT_TIMEOUT_MS);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "Inference result: %s", label);
        } else {
            ESP_LOGW(TAG, "No inference result received within timeout");
        }
        app_mqtt_deinit();
    }

    int64_t cycle_elapsed_ms = (esp_timer_get_time() - t_cycle_start) / 1000;
    ESP_LOGI(TAG, "app_do_send: total %s cycle (send + result) = %lld ms",
             transport_str, (long long)cycle_elapsed_ms);

    return APP_STATE_SLEEP;
}
