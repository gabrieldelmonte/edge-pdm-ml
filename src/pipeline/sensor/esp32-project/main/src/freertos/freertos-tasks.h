/*
 * freertos-tasks.h - FreeRTOS task declarations for the edge PDM firmware.
 *
 * Five tasks:
 *   app_reader       - CPU0 pri10: time-critical sample reader
 *   app_lis3dh_wrt   - CPU1 pri4:  writes LIS3DH samples from queue to file
 *   app_adxl345_wrt  - CPU1 pri4:  writes ADXL345 samples from queue to file
 *   app_oled         - CPU1 pri1:  updates SSD1306 OLED display
 *   app_wakeup       - CPU1 pri3:  monitors deep-sleep wakeup timer
 */
#ifndef APP_FREERTOS_TASKS_H
#define APP_FREERTOS_TASKS_H

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "../peripherals/setup.h"
#include "../peripherals/headers/lis3dh.h"
#include "../peripherals/headers/adxl345.h"
#include "../peripherals/headers/ssd1306.h"
#include "../peripherals/headers/ina219.h"

/* -------------------------------------------------------------------------
 * Shared queues (extern, defined in freertos-tasks.c)
 * ------------------------------------------------------------------------- */
extern QueueHandle_t g_lis3dh_queue;
extern QueueHandle_t g_adxl345_queue;

/* -------------------------------------------------------------------------
 * Task control flags (set by app_acquire to stop tasks gracefully)
 * ------------------------------------------------------------------------- */
extern volatile bool g_acquisition_active;

/* -------------------------------------------------------------------------
 * Task argument structs
 * ------------------------------------------------------------------------- */
typedef struct {
    config_t        *cfg;
    lis3dh_handle_t *lis3dh;
    adxl345_handle_t *adxl345;
} reader_task_args_t;

typedef struct {
    config_t         *cfg;
    ssd1306_handle_t *oled;    /* NULL if OLED disabled or unavailable */
    ina219_handle_t  *ina219;  /* NULL if INA219 disabled or unavailable */
} writer_task_args_t;

/* -------------------------------------------------------------------------
 * Task functions
 * ------------------------------------------------------------------------- */

/** app_reader_task - Reads samples from both sensors and pushes to queues. */
void app_reader_task(void *pvParameters);

/** app_lis3dh_wrt_task - Pops from g_lis3dh_queue and writes binary to SPIFFS. */
void app_lis3dh_wrt_task(void *pvParameters);

/** app_adxl345_wrt_task - Pops from g_adxl345_queue and writes binary to SPIFFS. */
void app_adxl345_wrt_task(void *pvParameters);

/** app_oled_task - Periodically refreshes the OLED status display. */
void app_oled_task(void *pvParameters);

/** app_wakeup_task - Monitors elapsed time and signals end of acquisition. */
void app_wakeup_task(void *pvParameters);

/**
 * app_tasks_create - Create all enabled tasks and initialize queues.
 * @param cfg        Active configuration.
 * @param lis3dh     LIS3DH handle (may be NULL if disabled).
 * @param adxl345    ADXL345 handle (may be NULL if disabled).
 * @param oled       OLED handle (may be NULL if disabled or unavailable).
 * @param ina219     INA219 handle (may be NULL if disabled or unavailable).
 */
void app_tasks_create(config_t *cfg,
                      lis3dh_handle_t  *lis3dh,
                      adxl345_handle_t *adxl345,
                      ssd1306_handle_t *oled,
                      ina219_handle_t  *ina219);

/**
 * app_tasks_stop - Signal tasks to stop and wait for them to finish.
 */
void app_tasks_stop(void);

#endif /* APP_FREERTOS_TASKS_H */
