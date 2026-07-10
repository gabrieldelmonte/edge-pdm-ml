/*
 * mqtt_transport.c - MQTT transport layer implementation.
 *
 * Publishes CSV data in batches to "pipeline/input".
 * Subscribes to "pipeline/result" and waits for inference labels.
 */
#include "mqtt_transport.h"
#include "../../include/app_constants.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_err.h"
#include "mqtt_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "mqtt_transport";

/* -------------------------------------------------------------------------
 * Module state
 * ------------------------------------------------------------------------- */
static esp_mqtt_client_handle_t s_client    = NULL;
static SemaphoreHandle_t        s_connected = NULL;
static SemaphoreHandle_t        s_result_sem = NULL;

/* Buffer to hold the received inference label. */
static char s_result_label[APP_LABEL_MAX_LEN] = {0};

/* -------------------------------------------------------------------------
 * Static helpers
 * ------------------------------------------------------------------------- */
static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = (esp_mqtt_event_handle_t)event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        xSemaphoreGive(s_connected);
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        break;

    case MQTT_EVENT_DATA:
        /* Only handle messages on pipeline/result. */
        if (event->topic_len > 0) {
            char topic[64] = {0};
            size_t tlen = (size_t)event->topic_len < sizeof(topic) - 1
                          ? (size_t)event->topic_len : sizeof(topic) - 1;
            memcpy(topic, event->topic, tlen);

            if (strcmp(topic, APP_MQTT_TOPIC_RESULT) == 0) {
                /* Parse {"inference":"label"} */
                char *data_copy = malloc((size_t)event->data_len + 1);
                if (data_copy) {
                    memcpy(data_copy, event->data, (size_t)event->data_len);
                    data_copy[event->data_len] = '\0';

                    const char *key = "\"inference\":\"";
                    char *pos = strstr(data_copy, key);
                    if (pos) {
                        pos += strlen(key);
                        char *end = strchr(pos, '"');
                        if (end) {
                            size_t llen = (size_t)(end - pos);
                            if (llen >= APP_LABEL_MAX_LEN) {
                                llen = APP_LABEL_MAX_LEN - 1;
                            }
                            memcpy(s_result_label, pos, llen);
                            s_result_label[llen] = '\0';
                            ESP_LOGI(TAG, "Inference result: %s", s_result_label);
                            xSemaphoreGive(s_result_sem);
                        }
                    }
                    free(data_copy);
                }
            }
        }
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error");
        break;

    default:
        break;
    }
}

/* -------------------------------------------------------------------------
 * Public: app_mqtt_init
 * ------------------------------------------------------------------------- */
esp_err_t app_mqtt_init(config_t *config)
{
    if (s_client) {
        ESP_LOGW(TAG, "app_mqtt_init: already initialized");
        return ESP_OK;
    }

    s_connected  = xSemaphoreCreateBinary();
    s_result_sem = xSemaphoreCreateBinary();
    if (!s_connected || !s_result_sem) {
        ESP_LOGE(TAG, "app_mqtt_init: semaphore creation failed");
        return ESP_ERR_NO_MEM;
    }

    char uri[160];
    snprintf(uri, sizeof(uri), "mqtt://%s:%u",
             config->mqtt_host, (unsigned)config->mqtt_port);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = uri,
    };

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "esp_mqtt_client_init failed");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                   mqtt_event_handler, NULL);

    esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_mqtt_client_start failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Wait for connection (10 s timeout). */
    if (xSemaphoreTake(s_connected, pdMS_TO_TICKS(10000)) != pdTRUE) {
        ESP_LOGE(TAG, "MQTT connect timeout");
        return ESP_ERR_TIMEOUT;
    }

    ESP_LOGI(TAG, "MQTT transport ready: %s", uri);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_mqtt_subscribe_result
 * ------------------------------------------------------------------------- */
esp_err_t app_mqtt_subscribe_result(void)
{
    if (!s_client) {
        ESP_LOGE(TAG, "app_mqtt_subscribe_result: not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    int msg_id = esp_mqtt_client_subscribe(s_client, APP_MQTT_TOPIC_RESULT, 1);
    if (msg_id < 0) {
        ESP_LOGE(TAG, "Subscribe to %s failed", APP_MQTT_TOPIC_RESULT);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Subscribed to %s (msg_id=%d)", APP_MQTT_TOPIC_RESULT, msg_id);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_mqtt_publish_batch
 * ------------------------------------------------------------------------- */
esp_err_t app_mqtt_publish_batch(const char    *file_id,
                                 uint64_t       capture_seq,
                                 const char    *sensor_id,
                                 const char    *csv_chunk,
                                 const char    *checksum,
                                 int            batch_index,
                                 bool           is_last_batch,
                                 float          current_ma,
                                 uint32_t       sample_rate_hz)
{
    if (!s_client) {
        ESP_LOGE(TAG, "app_mqtt_publish_batch: not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    /* Build JSON payload. checksum is the caller-supplied SHA-256 hex string.
     * Intermediate batches pass "" (hash not yet finalised); the last batch
     * passes the full 64-char hex digest.
     */
    size_t csv_len      = strlen(csv_chunk);
    size_t checksum_len = checksum ? strlen(checksum) : 0;
    size_t json_size    = csv_len + checksum_len + 512;
    char *payload       = malloc(json_size);
    if (!payload) {
        ESP_LOGE(TAG, "app_mqtt_publish_batch: malloc failed");
        return ESP_ERR_NO_MEM;
    }

    int written = snprintf(payload, json_size,
        "{"
        "\"file_id\":\"%s\","
        "\"capture_sequence\":%llu,"
        "\"sensor_id\":\"%s\","
        "\"csv_data\":\"%s\","
        "\"checksum\":\"%s\","
        "\"current_ma\":%.2f,"
        "\"sample_rate_hz\":%u,"
        "\"batch_index\":%d,"
        "\"is_last_batch\":%s"
        "}",
        file_id,
        (unsigned long long)capture_seq,
        sensor_id,
        csv_chunk,
        checksum ? checksum : "",
        (double)current_ma,
        (unsigned)sample_rate_hz,
        batch_index,
        is_last_batch ? "true" : "false");

    if (written < 0 || (size_t)written >= json_size) {
        ESP_LOGE(TAG, "app_mqtt_publish_batch: payload overflow");
        free(payload);
        return ESP_FAIL;
    }

    int msg_id = esp_mqtt_client_publish(s_client, APP_MQTT_TOPIC_INPUT,
                                         payload, written, 1, 0);
    free(payload);

    if (msg_id < 0) {
        ESP_LOGE(TAG, "MQTT publish to %s failed", APP_MQTT_TOPIC_INPUT);
        return ESP_FAIL;
    }

    ESP_LOGD(TAG, "Published batch %d (last=%s) msg_id=%d",
             batch_index, is_last_batch ? "true" : "false", msg_id);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_mqtt_wait_result
 * ------------------------------------------------------------------------- */
esp_err_t app_mqtt_wait_result(char *out_label, size_t len, int timeout_ms)
{
    if (!s_result_sem) {
        ESP_LOGE(TAG, "app_mqtt_wait_result: not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (xSemaphoreTake(s_result_sem, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        ESP_LOGE(TAG, "app_mqtt_wait_result: timeout after %d ms", timeout_ms);
        return ESP_ERR_TIMEOUT;
    }

    strncpy(out_label, s_result_label, len - 1);
    out_label[len - 1] = '\0';
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_mqtt_deinit
 * ------------------------------------------------------------------------- */
void app_mqtt_deinit(void)
{
    if (s_client) {
        esp_mqtt_client_stop(s_client);
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
    }
    if (s_connected) {
        vSemaphoreDelete(s_connected);
        s_connected = NULL;
    }
    if (s_result_sem) {
        vSemaphoreDelete(s_result_sem);
        s_result_sem = NULL;
    }
}
