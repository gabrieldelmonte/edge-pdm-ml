# Inference Server

A FastAPI service that receives capture payloads from the middleware, classifies them with the trained MAFAULDA models, persists every result to PostgreSQL, and serves the API behind the React dashboard.

## Architecture

```
app/
├── api/        route handlers (auth, sensors, models, inference, dashboard)
├── core/       config, database engine, exceptions, password hashing
├── domain/     SQLAlchemy ORM models, Pydantic schemas, repository queries
├── ml/         inference_engine.py (model registry + caching), signal_processing.py (feature extraction)
├── services/   inference_service.py, sensor_service.py — business logic orchestration
├── alembic/    schema migrations
└── frontend/   React + Vite dashboard, built and served as static assets
```

## Database schema (PostgreSQL, SQLAlchemy ORM)

| Table | Purpose |
|---|---|
| `users` | Dashboard authentication (username, password hash, timestamps). |
| `sensors` | One row per registered sensor: owning user, bearing position (`underhang`/`overhang`), selected model, active accelerometer, last-seen timestamp. |
| `inference_records` | One row per processed capture: checksum, raw CSV, per-model results, frequency-domain analysis, measured current, timestamp. |

Unrecognized sensor ids are auto-registered under a synthetic service account on first inference, so the pipeline can be exercised end-to-end without manually provisioning a sensor first.

## Inference pipeline

```
resolve sensor  →  verify SHA-256 checksum (mismatch recorded, not rejected)
   →  parse → resample → window the raw CSV  →  extract features
   →  compute frequency-domain analysis
   →  run all 8 registered models  →  select the sensor's configured model
   →  persist the record  →  return the response
```

Each capture is split into non-overlapping windows; each window is classified independently by every model, and per-window class probabilities are averaged to produce the final label and confidence — reported alongside `windows_evaluated` for transparency. Models are lazily loaded on first use and cached in memory afterward, so repeat requests for the same bearing type/model reuse the already-loaded artifact instead of re-reading it from disk.

Two independent registries of four models each back the classification — one trained per accelerometer mounting position — spanning both classical (Random Forest, SVM, XGBoost, LightGBM) and deep-learning (CNN1D, LSTM, GRU, RNN) families. The exact feature extraction, training procedure, and evaluation results for these models live in [`src/ai/README.md`](../../ai/README.md); the server's signal-processing module mirrors that same feature pipeline exactly so that inference-time features match what each model was trained on.

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | — | Liveness check |
| POST | `/api/auth/register`, `/login`, `/logout` | — | Account management |
| GET | `/api/auth/session` | ✓ | Current session info |
| GET/POST/PATCH/DELETE | `/api/sensors[/{uid}]` | ✓ | Sensor CRUD, scoped to the owning user |
| GET | `/api/models/{bearing_type}` | ✓ | Per-model artifact availability |
| POST | `/inference` | — | The middleware-facing classification endpoint |
| GET | `/api/inference/latest`, `/api/analysis/{uid}`, `/api/analysis/dual` | ✓ | Dashboard data feeds |
| GET | `/api/inference/{id}/csv[/lis3dh\|/adxl345]` | ✓ | Raw capture download/audit |

The `/inference` endpoint is intentionally unauthenticated — it is the machine-to-machine path the middleware calls on every capture, never called directly by a dashboard user.

## Dashboard

The `frontend/` app (React + Vite, Chart.js for plotting) provides sensor registration and configuration, a live frequency-spectrum view per sensor, and a feed of recent inference results across every registered sensor.

## Running

```bash
cd src/pipeline/server
docker compose up --build
```

This provisions a `postgres:16-alpine` database and the app container (a two-stage build: Node for the frontend, Python 3.12 for the API), mounting the AI module's shared feature-extraction code and saved model artifacts read-only. The API listens on `localhost:8001`, forwarding to an internal port `8000`; the database on `localhost:5433`. Key environment variables: `DATABASE_URL`, `APP_SECRET_KEY`, `MODEL_ROOT`, `FRONTEND_DIST`, `PYTHONPATH` (points at the mounted AI module so `ai.shared.feature_extraction` is importable).

The model artifacts under `src/ai/saved_models/` are generated locally, not shipped in the repository (see [`src/ai/README.md`](../../ai/README.md)) — run the training pipeline at least once before bringing this stack up, or the mounted model directory will be empty and every inference request will fail to load a model.

To run outside Docker, install `requirements.txt` into a Python 3.12 environment, point `DATABASE_URL` at a reachable PostgreSQL instance, run the Alembic migrations, and start the app with `uvicorn app.main:app`.
