use std::sync::{Arc, RwLock};

use crate::errors::AppError;
use crate::http_server::SensorIngestRequest;

/// Detect the accelerometer family from sensor metadata.
///
/// Returns `"dual"` when the sensor id contains a slash (indicating multiple
/// axes), otherwise `"single"`.
#[allow(dead_code)]
pub fn detect_accel_family(sensor_id: Option<&str>) -> &'static str {
    match sensor_id {
        Some(id) if id.contains('/') => "dual",
        _ => "single",
    }
}

/// Infer a canonical sample rate label from a raw Hz value.
///
/// Returns the nearest standard label or the stringified value if no match is
/// found.
#[allow(dead_code)]
pub fn detect_sample_rate(sample_rate_hz: Option<u32>) -> String {
    match sample_rate_hz {
        Some(hz) => match hz {
            0..=2000 => "low".to_string(),
            2001..=8000 => "medium".to_string(),
            _ => "high".to_string(),
        },
        None => "unknown".to_string(),
    }
}

/// HTTP client for forwarding sensor payloads to the upstream inference service.
///
/// Holds a shared reference to the mutable `inference_url` so it always uses
/// the most recently configured URL.
#[derive(Clone)]
pub struct InferenceClient {
    client: reqwest::Client,
    inference_url: Arc<RwLock<String>>,
}

impl InferenceClient {
    /// Create a new `InferenceClient` backed by the given shared URL.
    pub fn new(inference_url: Arc<RwLock<String>>) -> Self {
        InferenceClient {
            client: reqwest::Client::new(),
            inference_url,
        }
    }

    /// Forward a sensor ingest payload to the inference service.
    ///
    /// Reads the current inference URL from the shared `RwLock`, POSTs the
    /// payload as JSON, and returns the parsed response body on success.
    pub async fn forward(&self, payload: &SensorIngestRequest) -> Result<serde_json::Value, AppError> {
        let url = self
            .inference_url
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone();

        tracing::debug!(url = %url, file_id = %payload.file_id, "forwarding payload to inference service");

        /* Project only the fields the server's InferenceRequestPayload accepts.
         * batch_index and is_last_batch are MQTT-only and are rejected with
         * extra_forbidden by the server's StrictSchema. */
        let forwarded = serde_json::json!({
            "file_id":          &payload.file_id,
            "csv_data":         &payload.csv_data,
            "checksum":         &payload.checksum,
            "capture_sequence": payload.capture_sequence,
            "sensor_id":        &payload.sensor_id,
            "current_ma":       payload.current_ma,
            "sample_rate_hz":   payload.sample_rate_hz,
        });

        let response = self
            .client
            .post(&url)
            .json(&forwarded)
            .send()
            .await
            .map_err(|e| AppError::BadGateway(format!("inference request failed: {}", e)))?;

        let status = response.status();
        if !status.is_success() {
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "<no body>".to_string());
            return Err(AppError::BadGateway(format!(
                "inference service returned {}: {}",
                status, body
            )));
        }

        let value: serde_json::Value = response
            .json()
            .await
            .map_err(|e| AppError::BadGateway(format!("failed to parse inference response: {}", e)))?;

        Ok(value)
    }
}
