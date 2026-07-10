# Sensor Firmware (ESP32-S3)

ESP-IDF firmware for the edge sensor node: a battery-oriented, cyclic acquisition device that samples vibration and supply current, then hands the capture off to the middleware over HTTPS or MQTT before going back to sleep.

## Hardware

| Component | Interface | Role |
|---|---|---|
| **ESP32-S3** (dual-core Xtensa LX7, 240 MHz) | — | Main MCU. Chosen over the single-core RISC-V ESP32-C3 for headroom to move acquisition/writer/transport work across cores as the system grows. |
| **LIS3DH** | SPI | 3-axis MEMS accelerometer, ODR up to 5,376 Hz, ±2/4/8/16 g, low power draw (~11 µA typical). |
| **ADXL345** | SPI | 3-axis MEMS accelerometer, ODR up to 3,200 Hz, ±2/4/8/16 g, finer fixed resolution. |
| **INA219** | I²C | Current/power monitor (0.1 Ω shunt) instrumenting the node's own supply current during acquisition and transmission. |
| **SSD1306** | I²C, 128×64 | OLED status display: acquisition progress, elapsed time, live sample counts, live current draw. |

Both accelerometers can be enabled simultaneously and independently gated per-capture; at least one must be enabled for the device to leave the captive-portal setup screen. The two I²C peripherals share a single I²C master bus per controller.

The node's total bill of materials — MCU, both accelerometers (kept for redundancy/comparison), current monitor, and display — comes in at roughly **US$9–45**, several orders of magnitude below a commercial vibration analyzer.

## Firmware state machine

The firmware is a five-state cycle that runs once per wake and then deep-sleeps, so no state persists across cycles except the NVS-backed configuration:

```
BOOT → INIT → ACQUIRE → SEND_DATA → SLEEP ─┐
  ▲                                        │
  └─────────── timer wakeup ───────────────┘
```

- **BOOT** — initializes NVS/SPIFFS and loads the persisted configuration, or starts a captive-portal Wi-Fi access point for first-time setup.
- **INIT** — connects to Wi-Fi; initializes whichever accelerometer(s) are enabled (fatal if the only enabled sensor fails to initialize); initializes the OLED and INA219 non-fatally (the device continues without them if either is absent).
- **ACQUIRE** — spins up the sampling tasks and blocks until the configured capture duration elapses.
- **SEND_DATA** — transmits the captured signal as CSV to the middleware, via HTTPS or MQTT depending on configuration, and waits for the classification result.
- **SLEEP** — enables a timer wakeup and enters deep sleep; the device reboots into `BOOT` on the next cycle.

During `ACQUIRE`, a reader task polls both sensors into queues at the highest priority (deliberately un-yielding, to avoid dropping samples at high ODR), while separate writer tasks drain those queues to SPIFFS, refresh the OLED every 500 ms, and track the wakeup timer — all pinned across the two CPU cores so the tight sampling loop never blocks display or storage work.

## Dual transport: HTTPS and MQTT

Both paths are complete, independent implementations sharing the same SHA-256 checksum logic (via ESP-IDF's PSA Crypto API), so the resulting classification arrives at the device the same way regardless of transport:

- **HTTPS** — a two-pass POST to the middleware's `/api/ingest` endpoint. The first pass reads the capture once to compute its checksum and exact byte length (so the request can open with a precise `Content-Length` instead of relying on chunked transfer encoding); the second pass streams the same data in 4 KB chunks over the already-open connection.
- **MQTT** — publishes the capture as a series of configurable-size batches (CSV rows per message, set via the captive-portal UI) to a fixed input topic. Every intermediate batch carries an empty checksum; the final batch carries the full digest and a "last batch" flag. After publishing, the firmware subscribes to a result topic and blocks (with a timeout) for the classification pushed back by the middleware.

This dual design exists specifically so that cycle time and current draw — instrumented with microsecond-resolution timers and the INA219 reading attached to every payload — can be compared directly between the two transports under identical acquisition conditions, isolating the network layer's contribution to the node's battery budget.

## Building and flashing

Requires [ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/) (targeting `esp32s3`) with its component dependencies (`espressif/cjson`, `espressif/mqtt`) resolved via the IDF Component Manager.

```bash
cd src/pipeline/sensor/esp32-project
idf.py set-target esp32s3
idf.py build
idf.py -p <PORT> flash monitor
```

On first boot with no saved configuration, the device starts a captive-portal access point where Wi-Fi credentials, the target middleware endpoint (HTTPS or MQTT), sensor selection, sampling rate, capture duration, and MQTT batch size are all configured and persisted to NVS.

## Testing without hardware

`scripts/mock_ingest.py` at the repository root exercises the middleware and server exactly as the firmware would — including a full multi-batch MQTT publish/subscribe round trip — without needing a physical board. See [`../README.md`](../README.md) for usage.
