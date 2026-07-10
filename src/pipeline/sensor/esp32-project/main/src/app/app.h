/*
 * app.h - Top-level application state machine declarations.
 *
 * State sequence: BOOT -> INIT -> ACQUIRE -> SEND_DATA -> SLEEP
 */
#ifndef APP_APP_H
#define APP_APP_H

/* -------------------------------------------------------------------------
 * Application states
 * ------------------------------------------------------------------------- */
typedef enum {
    APP_STATE_BOOT      = 0,
    APP_STATE_INIT      = 1,
    APP_STATE_ACQUIRE   = 2,
    APP_STATE_SEND_DATA = 3,
    APP_STATE_SLEEP     = 4,
} app_state_t;

/**
 * app_run - Enter the main state machine loop. Does not return under normal
 * operation (the final state triggers deep sleep which resets the SoC).
 */
void app_run(void);

/**
 * state_to_str - Return a human-readable name for a state value.
 */
const char *state_to_str(app_state_t state);

#endif /* APP_APP_H */
