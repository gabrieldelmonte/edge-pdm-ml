use std::sync::{Arc, RwLock};

/// Read an environment variable as a boolean.
/// Returns true if the value is "1", "true", or "yes" (case-insensitive).
fn env_bool(key: &str, default: bool) -> bool {
    match std::env::var(key) {
        Ok(val) => matches!(val.to_lowercase().as_str(), "1" | "true" | "yes"),
        Err(_) => default,
    }
}

/// Read an environment variable as a String.
fn env_string(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// Read an environment variable as a u16.
fn env_u16(key: &str, default: u16) -> u16 {
    match std::env::var(key) {
        Ok(val) => val.parse::<u16>().unwrap_or(default),
        Err(_) => default,
    }
}

/// Read an environment variable as a u64.
fn env_u64(key: &str, default: u64) -> u64 {
    match std::env::var(key) {
        Ok(val) => val.parse::<u64>().unwrap_or(default),
        Err(_) => default,
    }
}

/// Runtime-mutable application configuration.
///
/// Values are loaded from environment variables at startup. The `inference_url`
/// field is wrapped in an `Arc<RwLock<String>>` so that the HTTP config endpoint
/// can update it while the server is running.
#[derive(Clone, Debug)]
pub struct AppConfig {
    /// Address to bind the HTTP server on (MW_BIND_ADDR).
    pub bind_addr: String,
    /// URL of the upstream inference service (MW_INFERENCE_URL).
    pub inference_url: Arc<RwLock<String>>,
    /// Enable verbose human-readable log output (MW_DEBUG_LOGS).
    pub debug_logs: bool,
    /// Maximum request body size in bytes (MW_MAX_BODY_BYTES).
    #[allow(dead_code)]
    pub max_body_bytes: u64,

    // MQTT configuration
    /// Enable the MQTT listener (MW_MQTT_ENABLED).
    pub mqtt_enabled: bool,
    /// Hostname of the MQTT broker (MW_MQTT_BROKER_HOST).
    pub mqtt_broker_host: String,
    /// Port of the MQTT broker (MW_MQTT_BROKER_PORT).
    pub mqtt_broker_port: u16,
    /// MQTT topic to subscribe for incoming sensor payloads (MW_MQTT_INPUT_TOPIC).
    pub mqtt_input_topic: String,
    /// MQTT topic to publish inference results to (MW_MQTT_RESULT_TOPIC).
    pub mqtt_result_topic: String,
}

impl AppConfig {
    /// Load configuration from environment variables, applying defaults where missing.
    pub fn from_env() -> Self {
        let inference_url = Arc::new(RwLock::new(env_string(
            "MW_INFERENCE_URL",
            "http://localhost:8080/predict",
        )));

        AppConfig {
            bind_addr: env_string("MW_BIND_ADDR", "0.0.0.0:3000"),
            inference_url,
            debug_logs: env_bool("MW_DEBUG_LOGS", false),
            max_body_bytes: env_u64("MW_MAX_BODY_BYTES", 10 * 1024 * 1024),
            mqtt_enabled: env_bool("MW_MQTT_ENABLED", false),
            mqtt_broker_host: env_string("MW_MQTT_BROKER_HOST", "localhost"),
            mqtt_broker_port: env_u16("MW_MQTT_BROKER_PORT", 1883),
            mqtt_input_topic: env_string("MW_MQTT_INPUT_TOPIC", "pipeline/input"),
            mqtt_result_topic: env_string("MW_MQTT_RESULT_TOPIC", "pipeline/result"),
        }
    }
}
