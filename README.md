# Edge PdM — Embedded Predictive Maintenance for Electric Motors

A low-cost, end-to-end **edge-to-cloud** vibration monitoring system for predictive maintenance (PdM) of electric motors. A battery-powered ESP32 sensor node captures tri-axial vibration and supply-current data, ships it to a Rust bridge over HTTPS or MQTT, and a FastAPI inference server classifies the motor's bearing condition using eight independently trained machine-learning and deep-learning models, persisting results to PostgreSQL and exposing a live React dashboard.

Unplanned downtime is one of the largest sources of lost productivity in industrial maintenance, and commercial vibration analyzers commonly cost between US$1,500 and US$25,000 per unit — putting continuous, multi-asset condition monitoring out of reach for many small and medium operations. This project explores how far low-cost MEMS accelerometers, an off-the-shelf microcontroller, and classical/deep-learning classifiers can go toward closing that gap, while keeping the full bill of materials for a sensor node under roughly US$45.

## Architecture

```
┌─────────────────────┐   HTTPS (2-pass POST)   ┌────────────────────┐   POST /inference   ┌───────────────────┐
│   ESP32-S3 sensor   │ ───────────────────────▶│   Rust middleware  │────────────────────▶│   FastAPI server  │
│ LIS3DH / ADXL345    │   MQTT (batched publish)│  (Axum + rumqttc)  │                     │  8-model registry │
│ INA219 / SSD1306    │ ───────────────────────▶│                    │                     │                   │
└─────────────────────┘◀────────────────────────└────────────────────┘◀────────────────────└─────────┬─────────┘
   deep-sleep cycle:          MQTT result topic                                                      │
   BOOT → INIT → ACQUIRE →                                                                ┌──────────┴──────────┐
   SEND_DATA → SLEEP                                                                      ▼                     ▼
                                                                                   ┌───────────────┐   ┌──────────────────┐
                                                                                   │ PostgreSQL 16 │   │  React dashboard │
                                                                                   └───────────────┘   └──────────────────┘
```

Three independently deployable subsystems, plus an offline training/evaluation pipeline:

| Subsystem | Path | Stack | Role |
|---|---|---|---|
| Sensor firmware | [`src/pipeline/sensor/`](src/pipeline/sensor/README.md) | C, ESP-IDF (ESP32-S3) | Acquires vibration + current, transmits over HTTPS or MQTT |
| Middleware | [`src/pipeline/middleware/`](src/pipeline/middleware/README.md) | Rust (Axum, Tokio, rumqttc) | Bridges both transports into one inference request |
| Inference server | [`src/pipeline/server/`](src/pipeline/server/README.md) | Python (FastAPI, SQLAlchemy) + React | Runs the 8-model classifier, persists results, serves the dashboard |
| AI training/evaluation | [`src/ai/`](src/ai/README.md) | Python | Offline model training and evaluation against MAFAULDA |

See [`src/pipeline/README.md`](src/pipeline/README.md) for how the three runtime subsystems fit together and how to run the whole stack locally.

## Why two transports

The sensor firmware supports both **HTTPS** and **MQTT** end-to-end, not as interchangeable options but as two complete, independently measurable paths. On a battery-powered node that spends most of its life in deep sleep, the network stack's connection-establishment cost is a first-order contributor to per-cycle energy consumption, so the project deliberately keeps both stacks alive to let cycle time and energy draw be compared under identical acquisition conditions rather than estimated from unrelated benchmarks.

## Classification approach

Ten motor conditions are classified — normal operation plus nine induction-motor bearing/shaft fault types (misalignment, imbalance, and bearing defects at two mounting positions) — from tri-axial accelerometer signals, using the public **MAFAULDA** (Machinery Fault Database) dataset. Two independent model families are trained, one per accelerometer mounting position (`underhang` and `overhang`), each evaluating eight classifiers side by side: four classical models operating on 57 hand-crafted time/frequency-domain features (Random Forest, SVM, XGBoost, LightGBM), and four deep-learning models operating directly on raw windowed signal (CNN1D, LSTM, GRU, RNN). Details of the feature set, training procedure, and the recording-level data split used to avoid inter-window leakage live in [`src/ai/README.md`](src/ai/README.md).

## Repository layout

```
edge-pdm-refactor/
├── scripts/mock_ingest.py      # End-to-end pipeline test harness (no hardware required)
└── src/
    ├── ai/                     # Offline training/evaluation against MAFAULDA
    └── pipeline/
        ├── sensor/             # ESP-IDF firmware for the ESP32-S3 sensor node
        ├── middleware/         # Rust protocol bridge + status dashboard
        └── server/             # FastAPI inference server + React dashboard
```

## Quick start

Each subsystem README documents its own build/run instructions in detail. In short:

1. **Train models** (required before the inference server has anything to load — trained artifacts are not shipped in the repository): see [`src/ai/README.md`](src/ai/README.md).
2. **Bring up the runtime stack** (Mosquitto broker, Rust middleware, dashboard, FastAPI server, PostgreSQL): see [`src/pipeline/README.md`](src/pipeline/README.md).
3. **Flash the firmware** to an ESP32-S3 board, or exercise the pipeline without hardware using `scripts/mock_ingest.py`: see [`src/pipeline/sensor/README.md`](src/pipeline/sensor/README.md).

## License

Distributed under the GNU General Public License v2.0 — see [`LICENSE`](LICENSE).
