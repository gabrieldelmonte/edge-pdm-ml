/*
 * freertos-tasks.c - FreeRTOS sampling tasks for the edge PDM firmware.
 *
 * Tasks:
 *   app_reader_task      CPU0 pri10 - reads sensors into queues (time-critical)
 *   app_lis3dh_wrt_task  CPU1 pri4  - dequeues LIS3DH samples, writes binary
 *   app_adxl345_wrt_task CPU1 pri4  - dequeues ADXL345 samples, writes binary
 *   app_oled_task        CPU1 pri1  - refreshes OLED display
 *   app_wakeup_task      CPU1 pri3  - stops acquisition after timeout
 *
 * Buffer sizes:
 *   APP_BINARY_WRITE_BUFFER_BYTES = 512
 *   APP_SAMPLE_QUEUE_LENGTH       = 32
 */
#include "freertos-tasks.h"
#include "../../include/app_constants.h"
#include "../../include/cpu_affinity.h"
#include "../../include/memory_utils.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_spiffs.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

static const char *TAG = "freertos-tasks";

/* -------------------------------------------------------------------------
 * Shared state
 * ------------------------------------------------------------------------- */
QueueHandle_t    g_lis3dh_queue      = NULL;
QueueHandle_t    g_adxl345_queue     = NULL;
volatile bool    g_acquisition_active = false;

static TaskHandle_t s_reader_task    = NULL;
static TaskHandle_t s_lis3dh_task    = NULL;
static TaskHandle_t s_adxl345_task   = NULL;
static TaskHandle_t s_oled_task      = NULL;
static TaskHandle_t s_wakeup_task    = NULL;

/* Live sample counts, updated by app_reader_task and read by app_oled_task
 * for the on-device status display. */
static volatile uint32_t s_lis3dh_sample_count  = 0;
static volatile uint32_t s_adxl345_sample_count = 0;

/* -------------------------------------------------------------------------
 * Task: app_reader_task
 * Pinned to CPU0 at priority 10 to run unpreempted (time-critical sampler).
 * ------------------------------------------------------------------------- */
void app_reader_task(void *pvParameters)
{
    reader_task_args_t *args = (reader_task_args_t *)pvParameters;

    /*
     * Subscribe this task to the TWDT so that SPI hangs are still caught.
     * IDLE0 was removed from TWDT monitoring in app_tasks_create() because
     * this tight polling loop intentionally holds CPU0 at priority 10 and
     * IDLE0 cannot run during acquisition.
     */
    esp_task_wdt_add(NULL);

    s_lis3dh_sample_count  = 0;
    s_adxl345_sample_count = 0;

    ESP_LOGI(TAG, "app_reader_task: acquisition started");

    while (g_acquisition_active) {
        if (args->cfg->lis3dh.enabled && args->lis3dh) {
            if (lis3dh_data_ready(args->lis3dh)) {
                lis3dh_sample_t s;
                if (lis3dh_read_sample(args->lis3dh, &s) == ESP_OK) {
                    /* Drop oldest if queue is full; do not block. */
                    if (xQueueSend(g_lis3dh_queue, &s, 0) != pdTRUE) {
                        ESP_LOGD(TAG, "LIS3DH queue full, sample dropped");
                    } else {
                        s_lis3dh_sample_count++;
                    }
                }
            }
        }

        if (args->cfg->adxl345.enabled && args->adxl345) {
            adxl345_sample_t s;
            if (adxl345_read_sample(args->adxl345, &s) == ESP_OK) {
                if (xQueueSend(g_adxl345_queue, &s, 0) != pdTRUE) {
                    ESP_LOGD(TAG, "ADXL345 queue full, sample dropped");
                } else {
                    s_adxl345_sample_count++;
                }
            }
        }

        esp_task_wdt_reset();
    }

    ESP_LOGI(TAG, "app_reader_task: done. lis3dh=%lu adxl345=%lu samples queued",
             (unsigned long)s_lis3dh_sample_count, (unsigned long)s_adxl345_sample_count);

    esp_task_wdt_delete(NULL);
    vTaskDelete(NULL);
}

/* -------------------------------------------------------------------------
 * Task: app_lis3dh_wrt_task
 * Pinned to SENSOR_CPU (CPU1).
 * ------------------------------------------------------------------------- */
void app_lis3dh_wrt_task(void *pvParameters)
{
    ESP_LOGI(TAG, "app_lis3dh_wrt_task: started");

    /* Binary write buffer on heap (512 bytes). */
    uint8_t *buf = (uint8_t *)spiram_malloc(APP_BINARY_WRITE_BUFFER_BYTES);
    if (!buf) {
        ESP_LOGE(TAG, "app_lis3dh_wrt_task: buffer alloc failed");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "app_lis3dh_wrt_task: buffer OK, opening file");

    FILE *f = fopen("/spiffs/lis3dh_samples.bin", "wb");
    if (!f) {
        ESP_LOGE(TAG, "app_lis3dh_wrt_task: cannot open lis3dh_samples.bin (errno=%d)", errno);
        free(buf);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "app_lis3dh_wrt_task: file opened, entering write loop");

    size_t   buf_pos      = 0;
    uint32_t samples_written = 0;

    while (g_acquisition_active || uxQueueMessagesWaiting(g_lis3dh_queue) > 0) {
        lis3dh_sample_t s;
        if (xQueueReceive(g_lis3dh_queue, &s, pdMS_TO_TICKS(10)) == pdTRUE) {
            /* Pack int16 x,y,z little-endian. */
            buf[buf_pos++] = (uint8_t)(s.x & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.x >> 8) & 0xFF);
            buf[buf_pos++] = (uint8_t)(s.y & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.y >> 8) & 0xFF);
            buf[buf_pos++] = (uint8_t)(s.z & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.z >> 8) & 0xFF);
            samples_written++;

            if (buf_pos >= APP_BINARY_WRITE_BUFFER_BYTES - APP_BINARY_RECORD_BYTES) {
                fwrite(buf, 1, buf_pos, f);
                buf_pos = 0;
            }
        }
    }

    /* Flush remaining */
    if (buf_pos > 0) {
        fwrite(buf, 1, buf_pos, f);
    }

    fclose(f);
    free(buf);
    ESP_LOGI(TAG, "app_lis3dh_wrt_task: done. %lu samples written to SPIFFS",
             (unsigned long)samples_written);
    vTaskDelete(NULL);
}

/* -------------------------------------------------------------------------
 * Task: app_adxl345_wrt_task
 * Pinned to SENSOR_CPU (CPU1).
 * ------------------------------------------------------------------------- */
void app_adxl345_wrt_task(void *pvParameters)
{
    ESP_LOGI(TAG, "app_adxl345_wrt_task: started");

    uint8_t *buf = (uint8_t *)spiram_malloc(APP_BINARY_WRITE_BUFFER_BYTES);
    if (!buf) {
        ESP_LOGE(TAG, "app_adxl345_wrt_task: buffer alloc failed");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "app_adxl345_wrt_task: buffer OK, opening file");

    FILE *f = fopen("/spiffs/adxl345_samples.bin", "wb");
    if (!f) {
        ESP_LOGE(TAG, "app_adxl345_wrt_task: cannot open adxl345_samples.bin (errno=%d)", errno);
        free(buf);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "app_adxl345_wrt_task: file opened, entering write loop");

    size_t   buf_pos         = 0;
    uint32_t samples_written = 0;

    while (g_acquisition_active || uxQueueMessagesWaiting(g_adxl345_queue) > 0) {
        adxl345_sample_t s;
        if (xQueueReceive(g_adxl345_queue, &s, pdMS_TO_TICKS(10)) == pdTRUE) {
            buf[buf_pos++] = (uint8_t)(s.x & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.x >> 8) & 0xFF);
            buf[buf_pos++] = (uint8_t)(s.y & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.y >> 8) & 0xFF);
            buf[buf_pos++] = (uint8_t)(s.z & 0xFF);
            buf[buf_pos++] = (uint8_t)((s.z >> 8) & 0xFF);
            samples_written++;

            if (buf_pos >= APP_BINARY_WRITE_BUFFER_BYTES - APP_BINARY_RECORD_BYTES) {
                fwrite(buf, 1, buf_pos, f);
                buf_pos = 0;
            }
        }
    }

    if (buf_pos > 0) {
        fwrite(buf, 1, buf_pos, f);
    }

    fclose(f);
    free(buf);
    ESP_LOGI(TAG, "app_adxl345_wrt_task: done. %lu samples written to SPIFFS",
             (unsigned long)samples_written);
    vTaskDelete(NULL);
}

/* -------------------------------------------------------------------------
 * Task: app_oled_task
 * Pinned to APP_CPU (CPU1). Non-fatal: if OLED absent, exits immediately.
 * ------------------------------------------------------------------------- */
void app_oled_task(void *pvParameters)
{
    writer_task_args_t *args = (writer_task_args_t *)pvParameters;

    if (!args->cfg->oled.enabled || !args->oled) {
        vTaskDelete(NULL);
        return;
    }

    /* Sized for the worst-case formatted width (not the display row width;
     * ssd1306_write_line() truncates to SSD1306_NUM_COLS itself). */
    char     line[32];
    uint32_t elapsed_ms = 0;

    while (g_acquisition_active) {
        ssd1306_write_line(args->oled, 0, "Acquiring...");

        snprintf(line, sizeof(line), "T:%lu/%lus",
                 (unsigned long)(elapsed_ms / 1000),
                 (unsigned long)(args->cfg->sampling_time_ms / 1000));
        ssd1306_write_line(args->oled, 1, line);

        snprintf(line, sizeof(line), "L:%lu A:%lu",
                 (unsigned long)s_lis3dh_sample_count,
                 (unsigned long)s_adxl345_sample_count);
        ssd1306_write_line(args->oled, 2, line);

        if (args->ina219) {
            float current_ma = 0.0f;
            if (ina219_read_current_ma(args->ina219, &current_ma) == ESP_OK) {
                snprintf(line, sizeof(line), "I:%.1fmA", (double)current_ma);
            } else {
                snprintf(line, sizeof(line), "I:err");
            }
        } else {
            snprintf(line, sizeof(line), "I:--");
        }
        ssd1306_write_line(args->oled, 3, line);

        vTaskDelay(pdMS_TO_TICKS(500));
        elapsed_ms += 500;
    }

    vTaskDelete(NULL);
}

/* -------------------------------------------------------------------------
 * Task: app_wakeup_task
 * Pinned to APP_CPU (CPU1).
 * Signals end of acquisition after sampling_time_ms.
 * ------------------------------------------------------------------------- */
void app_wakeup_task(void *pvParameters)
{
    writer_task_args_t *args = (writer_task_args_t *)pvParameters;
    uint32_t duration_ms = args->cfg->sampling_time_ms;

    ESP_LOGI(TAG, "Acquisition will run for %u ms", (unsigned)duration_ms);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));

    g_acquisition_active = false;
    ESP_LOGI(TAG, "app_wakeup_task: acquisition complete");
    vTaskDelete(NULL);
}

/* -------------------------------------------------------------------------
 * Public: app_tasks_create
 * ------------------------------------------------------------------------- */
void app_tasks_create(config_t *cfg,
                      lis3dh_handle_t  *lis3dh,
                      adxl345_handle_t *adxl345,
                      ssd1306_handle_t *oled,
                      ina219_handle_t  *ina219)
{
    /* Create queues. */
    if (cfg->lis3dh.enabled) {
        g_lis3dh_queue = xQueueCreate(APP_SAMPLE_QUEUE_LENGTH,
                                      sizeof(lis3dh_sample_t));
        if (!g_lis3dh_queue) {
            ESP_LOGE(TAG, "Failed to create LIS3DH queue");
        }
        ESP_LOGI(TAG, "LIS3DH queue created with length %d", APP_SAMPLE_QUEUE_LENGTH);
    }
    if (cfg->adxl345.enabled) {
        g_adxl345_queue = xQueueCreate(APP_SAMPLE_QUEUE_LENGTH,
                                       sizeof(adxl345_sample_t));
        if (!g_adxl345_queue) {
            ESP_LOGE(TAG, "Failed to create ADXL345 queue");
        }
        ESP_LOGI(TAG, "ADXL345 queue created with length %d", APP_SAMPLE_QUEUE_LENGTH);
    }

    g_acquisition_active = true;

    /* Log SPIFFS free space so partition exhaustion is visible in the log. */
    {
        size_t total = 0, used = 0;
        if (esp_spiffs_info(NULL, &total, &used) == ESP_OK) {
            ESP_LOGI(TAG, "SPIFFS: %u KB total, %u KB used, %u KB free",
                     (unsigned)(total / 1024), (unsigned)(used / 1024),
                     (unsigned)((total - used) / 1024));
        }
    }

    /* IDLE tasks are not subscribed to the TWDT (CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0=n).
     * The reader task subscribes itself via esp_task_wdt_add(NULL) so SPI hangs
     * are still caught without requiring manual IDLE0 management here. */

    /* reader_task args allocated on heap; lifetime = task lifetime. */
    static reader_task_args_t reader_args;
    reader_args.cfg     = cfg;
    reader_args.lis3dh  = lis3dh;
    reader_args.adxl345 = adxl345;

    static writer_task_args_t writer_args;
    writer_args.cfg    = cfg;
    writer_args.oled   = oled;
    writer_args.ina219 = ina219;

    /*
     * Create CPU1 tasks first.  app_reader_task is pinned to CPU0 at priority
     * 10 — creating it triggers an immediate context switch on CPU0 via the
     * SMP FreeRTOS scheduler.  Any xTaskCreatePinnedToCore call after it would
     * never execute because the main task (priority 1, CPU0) is preempted
     * synchronously.  All CPU1 tasks must therefore be in the ready state
     * before the reader is created.
     */
    if (cfg->lis3dh.enabled) {
        xTaskCreatePinnedToCore(app_lis3dh_wrt_task, "app_lis3dh_wrt",
                                APP_LIS3DH_WRT_STACK_SIZE, NULL,
                                APP_LIS3DH_WRT_PRIORITY, &s_lis3dh_task,
                                SENSOR_CPU);
        ESP_LOGI(TAG, "LIS3DH task created");
    }

    if (cfg->adxl345.enabled) {
        xTaskCreatePinnedToCore(app_adxl345_wrt_task, "app_adxl345_wrt",
                                APP_ADXL345_WRT_STACK_SIZE, NULL,
                                APP_ADXL345_WRT_PRIORITY, &s_adxl345_task,
                                SENSOR_CPU);
        ESP_LOGI(TAG, "ADXL345 task created");
    }

    if (cfg->oled.enabled) {
        xTaskCreatePinnedToCore(app_oled_task, "app_oled",
                                APP_OLED_STACK_SIZE, &writer_args,
                                APP_OLED_PRIORITY, &s_oled_task,
                                APP_CPU);
        ESP_LOGI(TAG, "OLED task created");
    }

    xTaskCreatePinnedToCore(app_wakeup_task, "app_wakeup",
                            APP_WAKEUP_STACK_SIZE, &writer_args,
                            APP_WAKEUP_PRIORITY, &s_wakeup_task,
                            APP_CPU);

    /* Create reader last: triggers immediate CPU0 preemption. */
    xTaskCreatePinnedToCore(app_reader_task, "app_reader",
                            APP_READER_STACK_SIZE, &reader_args,
                            APP_READER_PRIORITY, &s_reader_task, 0);
}

/* -------------------------------------------------------------------------
 * Public: app_tasks_stop
 * ------------------------------------------------------------------------- */
void app_tasks_stop(void)
{
    /* Signal all tasks to exit. */
    g_acquisition_active = false;

    /* Wait for tasks to self-delete. */
    vTaskDelay(pdMS_TO_TICKS(200));

    /* Clean up queues. */
    if (g_lis3dh_queue) {
        vQueueDelete(g_lis3dh_queue);
        g_lis3dh_queue = NULL;
    }
    if (g_adxl345_queue) {
        vQueueDelete(g_adxl345_queue);
        g_adxl345_queue = NULL;
    }

    s_reader_task  = NULL;
    s_lis3dh_task  = NULL;
    s_adxl345_task = NULL;
    s_oled_task    = NULL;
    s_wakeup_task  = NULL;
}
