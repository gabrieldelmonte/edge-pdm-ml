/*
 * app_acquire.c - ACQUIRE state handler.
 *
 * Starts FreeRTOS sampling tasks for enabled sensors, waits for them to
 * finish (driven by app_wakeup_task after sampling_time_ms), then returns
 * to the caller.
 */
#include "app_acquire.h"
#include "app_init.h"
#include "../../include/app_constants.h"
#include "../freertos/freertos-tasks.h"

#include <unistd.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "app_acquire";

/* -------------------------------------------------------------------------
 * Public: app_do_acquire
 * ------------------------------------------------------------------------- */
app_state_t app_do_acquire(config_t *cfg)
{
    lis3dh_handle_t  *lis3dh  = cfg->lis3dh.enabled  ? app_init_get_lis3dh()  : NULL;
    adxl345_handle_t *adxl345 = cfg->adxl345.enabled ? app_init_get_adxl345() : NULL;
    ssd1306_handle_t *oled    = cfg->oled.enabled    ? app_init_get_oled()    : NULL;
    ina219_handle_t  *ina219  = cfg->ina219.enabled  ? app_init_get_ina219()  : NULL;

    ESP_LOGI(TAG, "Starting acquisition (duration=%u ms, lis3dh=%s, adxl345=%s)",
             (unsigned)cfg->sampling_time_ms,
             cfg->lis3dh.enabled  ? "enabled" : "disabled",
             cfg->adxl345.enabled ? "enabled" : "disabled");

    /* Delete binary files from the previous acquisition before creating tasks.
     * Doing this here (main-task context, CPU1) means any SPIFFS GC triggered
     * by the unlink runs before the reader starts, so fopen() inside the writer
     * tasks is fast and no early samples are lost to SPIFFS housekeeping. */
    if (cfg->lis3dh.enabled) {
        ESP_LOGI(TAG, "Removing previous lis3dh_samples.bin");
        unlink("/spiffs/lis3dh_samples.bin");
        ESP_LOGI(TAG, "lis3dh_samples.bin removed");
    }
    if (cfg->adxl345.enabled) {
        ESP_LOGI(TAG, "Removing previous adxl345_samples.bin");
        unlink("/spiffs/adxl345_samples.bin");
        ESP_LOGI(TAG, "adxl345_samples.bin removed");
    }

    app_tasks_create(cfg, lis3dh, adxl345, oled, ina219);

    /* Confirm the main task is still alive after task creation. */
    ESP_LOGI(TAG, "Tasks created, entering polling loop");

    /* Poll until acquisition flag is cleared by app_wakeup_task.
     * Log a heartbeat every second so the monitor shows the device is alive. */
    uint32_t elapsed_ms = 0;
    while (g_acquisition_active) {
        vTaskDelay(pdMS_TO_TICKS(100));
        elapsed_ms += 100;
        if (elapsed_ms % 1000 == 0) {
            ESP_LOGI(TAG, "Acquiring... %lu ms / %lu ms",
                     (unsigned long)elapsed_ms,
                     (unsigned long)cfg->sampling_time_ms);
        }
    }

    /* Give writer tasks time to flush their buffers. */
    vTaskDelay(pdMS_TO_TICKS(200));

    app_tasks_stop();

    ESP_LOGI(TAG, "Acquisition complete");
    return APP_STATE_SEND_DATA;
}
