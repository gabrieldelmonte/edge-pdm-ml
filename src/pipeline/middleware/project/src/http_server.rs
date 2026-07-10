use std::sync::Arc;

use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    routing::{get, post},
};
use serde::{Deserialize, Serialize};

use crate::config::AppConfig;
use crate::errors::AppError;
use crate::inference_client::InferenceClient;
use crate::state::RuntimeState;

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

/// State shared across all HTTP handler functions.
#[derive(Clone)]
pub struct WebState {
    /// Application configuration (inference URL is mutable at runtime).
    pub config: AppConfig,
    /// Runtime counters.
    pub state: Arc<RuntimeState>,
    /// Client for forwarding payloads to the inference service.
    pub inference_client: InferenceClient,
}

// ---------------------------------------------------------------------------
// Request / response types
// ---------------------------------------------------------------------------

/// Incoming sensor CSV payload from an edge device.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SensorIngestRequest {
    /// Unique identifier for the uploaded file.
    pub file_id: String,
    /// Optional sequence index when a file arrives in multiple HTTP chunks.
    pub capture_sequence: Option<u32>,
    /// Optional sensor identifier string.
    pub sensor_id: Option<String>,
    /// CSV data as a UTF-8 string.
    pub csv_data: String,
    /// CRC32 / SHA256 checksum of the CSV data.
    pub checksum: String,
    /// Supply current in milliamperes.
    pub current_ma: Option<f64>,
    /// Sample rate of the sensor in Hz.
    pub sample_rate_hz: Option<u32>,
    /// Batch index within a multi-message MQTT transfer.
    pub batch_index: Option<u64>,
    /// Whether this is the final batch in a multi-message MQTT transfer.
    pub is_last_batch: Option<bool>,
}

/// Response returned after a successful health check.
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
}

/// Response returned by the status endpoint.
#[derive(Debug, Serialize)]
pub struct AppStatusResponse {
    pub status: &'static str,
    pub http_bind_host: String,
    pub http_port: u16,
    pub received_uploads: u64,
    pub completed_files: u64,
    pub inference_ok: u64,
    pub inference_error: u64,
    pub mqtt_received: u64,
    pub mqtt_forwarded: u64,
    pub sensors_connected: u64,
    pub sensors_seen: Vec<String>,
    pub last_sensor_id: Option<String>,
    pub last_file_id: Option<String>,
    pub last_current_ma: Option<f64>,
}

/// Payload accepted by `POST /api/config`.
#[derive(Debug, Deserialize)]
pub struct UpdateConfigRequest {
    pub inference_url: Option<String>,
}

/// Response returned after a config update.
#[derive(Debug, Serialize)]
pub struct UpdateConfigResponse {
    pub status: &'static str,
    pub inference_url: String,
}

/// Generic error response body (also used as a tuple element in handler returns).
#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct ApiErrorResponse {
    pub status: &'static str,
    pub message: String,
}

// ---------------------------------------------------------------------------
// Router construction
// ---------------------------------------------------------------------------

/// Build the Axum router with all API routes attached.
pub fn build_router(web_state: WebState) -> Router {
    Router::new()
        .route("/api/health", get(handle_health))
        .route("/api/config", get(handle_get_config).post(handle_post_config))
        .route("/api/status", get(handle_status))
        .route("/api/ingest", post(handle_ingest))
        .with_state(web_state)
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// GET /api/health - liveness probe.
async fn handle_health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

/// GET /api/config - return the current mutable configuration values.
async fn handle_get_config(
    State(ws): State<WebState>,
) -> Json<serde_json::Value> {
    let url = ws
        .config
        .inference_url
        .read()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone();

    Json(serde_json::json!({
        "inference_url": url,
        "debug_logs": ws.config.debug_logs,
        "mqtt_enabled": ws.config.mqtt_enabled,
    }))
}

/// POST /api/config - update runtime-mutable configuration values.
async fn handle_post_config(
    State(ws): State<WebState>,
    Json(body): Json<UpdateConfigRequest>,
) -> Result<Json<UpdateConfigResponse>, AppError> {
    if let Some(new_url) = body.inference_url {
        if new_url.is_empty() {
            return Err(AppError::BadRequest(
                "inference_url must not be empty".to_string(),
            ));
        }
        let mut guard = ws
            .config
            .inference_url
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *guard = new_url;
    }

    let current = ws
        .config
        .inference_url
        .read()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone();

    tracing::info!(inference_url = %current, "config updated");

    Ok(Json(UpdateConfigResponse {
        status: "ok",
        inference_url: current,
    }))
}

/// GET /api/status - return runtime counters.
async fn handle_status(
    State(ws): State<WebState>,
) -> Json<AppStatusResponse> {
    let snap = ws.state.snapshot();

    let (http_bind_host, http_port) = ws
        .config
        .bind_addr
        .rsplit_once(':')
        .map(|(h, p)| (h.to_string(), p.parse::<u16>().unwrap_or(0)))
        .unwrap_or_else(|| (ws.config.bind_addr.clone(), 0));

    Json(AppStatusResponse {
        status: "ok",
        http_bind_host,
        http_port,
        received_uploads: snap.received_uploads,
        completed_files: snap.completed_files,
        inference_ok: snap.inference_ok,
        inference_error: snap.inference_error,
        mqtt_received: snap.mqtt_received,
        mqtt_forwarded: snap.mqtt_forwarded,
        sensors_connected: snap.sensors_seen.len() as u64,
        sensors_seen: snap.sensors_seen,
        last_sensor_id: snap.last_sensor_id,
        last_file_id: snap.last_file_id,
        last_current_ma: snap.last_current_ma,
    })
}

/// POST /api/ingest - receive a sensor CSV payload and forward it to inference.
#[tracing::instrument(skip(ws), fields(file_id = %payload.file_id, sensor_id = ?payload.sensor_id))]
async fn handle_ingest(
    State(ws): State<WebState>,
    Json(payload): Json<SensorIngestRequest>,
) -> Result<(StatusCode, Json<serde_json::Value>), AppError> {
    ws.state.increment_received_uploads();

    tracing::info!("received ingest request");

    let t_start = std::time::Instant::now();
    let result = ws.inference_client.forward(&payload).await;
    let elapsed_ms = t_start.elapsed().as_millis();

    match result {
        Ok(value) => {
            ws.state.increment_inference_ok();
            ws.state.increment_completed_files();
            ws.state.record_ingest(
                payload.sensor_id.as_deref().unwrap_or("unknown"),
                &payload.file_id,
                payload.current_ma,
            );
            tracing::info!(transport = "http", elapsed_ms, "inference succeeded");
            Ok((StatusCode::OK, Json(value)))
        }
        Err(e) => {
            ws.state.increment_inference_error();
            tracing::error!(error = %e, transport = "http", elapsed_ms, "inference failed");
            Err(e)
        }
    }
}
