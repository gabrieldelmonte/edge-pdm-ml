/*
 * main.c - Application entry point for the edge PDM sensor firmware.
 *
 * Calls app_run() which implements the BOOT->INIT->ACQUIRE->SEND_DATA->SLEEP
 * state machine.  Does not return under normal operation.
 */
#include "src/app/app.h"

void app_main(void)
{
    app_run();
}
