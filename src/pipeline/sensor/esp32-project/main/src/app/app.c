/*
 * app.c - Top-level application state machine dispatcher.
 *
 * State sequence: BOOT -> INIT -> ACQUIRE -> SEND_DATA -> SLEEP
 *
 * This file contains only the switch/case dispatcher and transition logging.
 * Sub-state logic lives in app_init.c, app_acquire.c, and app_send.c.
 */
#include "app.h"
#include "app_init.h"
#include "app_acquire.h"
#include "app_send.h"

#include "esp_log.h"
#include "esp_sleep.h"
#include "../peripherals/setup.h"

static const char *TAG = "app";

/* -------------------------------------------------------------------------
 * Public: state_to_str
 * ------------------------------------------------------------------------- */
const char *state_to_str(app_state_t state)
{
    switch (state) {
    case APP_STATE_BOOT:      return "BOOT";
    case APP_STATE_INIT:      return "INIT";
    case APP_STATE_ACQUIRE:   return "ACQUIRE";
    case APP_STATE_SEND_DATA: return "SEND_DATA";
    case APP_STATE_SLEEP:     return "SLEEP";
    default:                  return "UNKNOWN";
    }
}

/* -------------------------------------------------------------------------
 * Public: app_run
 * ------------------------------------------------------------------------- */
void app_run(void)
{
    config_t    cfg;
    app_state_t state = APP_STATE_BOOT;
    app_state_t next  = APP_STATE_BOOT;

    while (true) {
        ESP_LOGI(TAG, "Current state: %s", state_to_str(state));
        switch (state) {
            case APP_STATE_BOOT:
                next = app_do_boot(&cfg);
                break;

            case APP_STATE_INIT:
                next = app_do_init(&cfg);
                break;

            case APP_STATE_ACQUIRE:
                next = app_do_acquire(&cfg);
                break;

            case APP_STATE_SEND_DATA:
                next = app_do_send(&cfg);
                break;

            case APP_STATE_SLEEP:
                ESP_LOGI(TAG, "State: %s -> entering deep sleep for %u s",
                        state_to_str(state),
                        (unsigned)cfg.deep_sleep_duration_s);
                esp_sleep_enable_timer_wakeup(
                    (uint64_t)cfg.deep_sleep_duration_s * 1000000ULL);
                esp_deep_sleep_start();
                /* Does not return. */
                break;

            default:
                ESP_LOGE(TAG, "Unknown state %d, resetting to BOOT", (int)state);
                next = APP_STATE_BOOT;
                break;
        }

        if (next != state) {
            ESP_LOGI(TAG, "State: %s -> %s",
                     state_to_str(state), state_to_str(next));
        }
        state = next;
    }
}
