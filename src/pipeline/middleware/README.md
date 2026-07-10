# Middleware

A Rust service that bridges the sensor's two transports — HTTPS and MQTT — into a single inference request, so the server behind it only ever has to speak one protocol.

## Role

The middleware is the sole point of contact between the sensor node and the inference server. It accepts capture uploads over either transport, normalizes both into the same request shape, and forwards them to the server's `/inference` endpoint over plain HTTP. For MQTT, it also republishes the classification result back to the sensor so the firmware's blocking wait for a result behaves identically regardless of which transport it used.

```
             POST /api/ingest                    forward (HTTP)
Sensor ─────────────────────────▶  Middleware  ─────────────────▶  Inference server
  ▲                                    │  ▲
  │        MQTT pipeline/input         │  │  MQTT pipeline/result
  └────────────────────────────────────┘  └── (republished after inference)
```

## Stack

Built with **Axum** (HTTP routing), **Tokio** (async runtime), and **rumqttc** (MQTT client), plus `dashmap` for the concurrent batch accumulator and `tracing` for structured logging. See `project/Cargo.toml` for exact dependency versions.

## HTTP ingest

Four routes are exposed: `GET /api/health`, `GET`/`POST /api/config`, `GET /api/status`, and `POST /api/ingest`. `POST /api/ingest` times and forwards the payload to the inference server via a shared `InferenceClient`, and returns the server's JSON response to the caller unchanged.

## MQTT listener and multi-batch reassembly

The MQTT listener connects to the configured broker and subscribes to the sensor's input topic. Incoming messages are dispatched by their batch metadata:

- A single-message transfer is forwarded immediately.
- An intermediate batch is accumulated in a concurrent map keyed by capture id, storing each batch's index and payload.
- The final batch (carrying the capture's checksum) drains that capture's accumulated batches, sorts them by index, concatenates them, and forwards the assembled payload.

A background sweep periodically evicts accumulators for captures that never completed, so a partial or lost transfer cannot grow memory unbounded. On a successful inference, the result is republished to the sensor's result topic.

## Dashboard

A small Node.js/Express app under `dashboard/` proxies the middleware's API and serves a single status page that polls upload counts, inference success/error counts, MQTT counters, and the set of sensor ids seen — useful for watching the pipeline live during a capture session without opening the server's own dashboard.

## Running

```bash
cd src/pipeline/middleware
docker compose up --build
```

This provisions three containers: a Mosquitto broker (anonymous access, for local development), the middleware itself, and the status dashboard. By default the middleware listens on `localhost:8080`, the dashboard on `localhost:3000`, and the broker on `localhost:1883`; the middleware is preconfigured to reach the inference server at `host.docker.internal:8001`. Key environment variables (see `docker-compose.yml`): `MW_BIND_ADDR`, `MW_INFERENCE_URL`, `MW_MQTT_ENABLED`, `MW_MQTT_BROKER_HOST`/`MW_MQTT_BROKER_PORT`, `MW_MQTT_INPUT_TOPIC`/`MW_MQTT_RESULT_TOPIC`.

To run outside Docker: `cargo run` from `project/`, with the same environment variables set and a broker/server reachable at the configured addresses.
