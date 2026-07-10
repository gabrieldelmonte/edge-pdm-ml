/*
 * cpu_affinity.h - CPU core assignment macros for FreeRTOS task creation.
 *
 * ESP32-S3 is dual-core (PRO_CPU = 0, APP_CPU = 1).
 * Single-core targets use tskNO_AFFINITY so the scheduler picks freely.
 */
#ifndef APP_CPU_AFFINITY_H
#define APP_CPU_AFFINITY_H

#ifdef CONFIG_IDF_TARGET_ESP32S3
#define SENSOR_CPU  1
#define APP_CPU     0
#else
/* ESP32-C3 or other single-core targets */
#define SENSOR_CPU  tskNO_AFFINITY
#define APP_CPU     tskNO_AFFINITY
#endif

#endif /* APP_CPU_AFFINITY_H */
