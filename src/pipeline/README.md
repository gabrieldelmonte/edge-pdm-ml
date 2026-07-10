# Pipeline — Edge-to-Cloud Runtime

The three runtime subsystems that make up the live vibration-monitoring pipeline: the sensor firmware, the protocol middleware, and the inference server. Each is independently deployable and versioned on its own, but they are designed to be brought up together as a single stack for local development and validation.

```
ESP32-S3 sensor  ──HTTPS or MQTT──▶  Rust middleware  ──HTTP POST /inference──▶  FastAPI server ──▶ PostgreSQL + React dashboard
```

- **[`sensor/`](sensor/README.md)** — ESP-IDF firmware for the ESP32-S3. Runs a cyclic boot → init → acquire → send → deep-sleep state machine, reads two swappable MEMS accelerometers (LIS3DH, ADXL345) plus an INA219 current monitor, and ships each capture over HTTPS or MQTT.
- **[`middleware/`](middleware/README.md)** — a Rust service (Axum + rumqttc) that accepts both transports, normalizes them into one request shape, forwards to the inference server, and republishes MQTT results back to the sensor. Ships with a small status dashboard.
- **[`server/`](server/README.md)** — a FastAPI service that runs the classification (eight models, two independently trained per bearing position), persists every inference to PostgreSQL, and serves a React dashboard for sensor configuration and live signal/result visualization.

## Why a middleware sits between the sensor and the server

The sensor firmware supports two transports end to end — a two-pass, checksummed HTTPS POST, and a batched MQTT publish/subscribe exchange over a broker — so that the network layer's contribution to per-cycle time and energy on a battery-powered node can be measured directly rather than assumed. The middleware is the single point where both paths converge: it normalizes an HTTPS payload and a reassembled multi-batch MQTT payload into the same internal request shape before either ever reaches the inference server, so the server only ever has to speak one protocol (a plain HTTP `POST /inference`) regardless of which path the sensor used.

## Running the full stack locally

Each subsystem has its own `docker-compose.yml`; bring them up in this order so dependent services (the Mosquitto broker, the Postgres database) are ready before the components that need them:

```bash
# 1. Inference server (Postgres + FastAPI + built React dashboard)
cd src/pipeline/server
docker compose up --build

# 2. Middleware (Mosquitto broker + Rust bridge + status dashboard)
cd src/pipeline/middleware
docker compose up --build
```

By default the server listens on `localhost:8001`, the middleware's HTTP ingest endpoint on `localhost:8080`, the middleware's status dashboard on `localhost:3000`, and the Mosquitto broker on `localhost:1883`. The middleware is configured to reach the server via `host.docker.internal:8001` out of the box.

With both stacks up, exercise the whole pipeline **without any physical hardware** using the mock sensor harness at the repository root:

```bash
python scripts/mock_ingest.py --target middleware   # HTTPS: sensor → middleware → server
python scripts/mock_ingest.py --target server       # bypasses the middleware entirely
python scripts/mock_ingest.py --target mqtt         # full MQTT round trip, including the result publish back
python scripts/mock_ingest.py --target all          # all of the above
```

This generates a synthetic tri-axial signal, sends it down each path, and reports the classification returned by the server — useful for validating a change to any subsystem before involving real firmware.

Finally, flash `sensor/esp32-project/` onto an ESP32-S3 board (see [its README](sensor/README.md)) and point it at the middleware's HTTP or MQTT endpoint through its captive-portal configuration UI to close the loop with real vibration data.
