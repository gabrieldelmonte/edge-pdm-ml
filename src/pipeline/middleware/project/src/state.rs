use std::collections::HashSet;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// A point-in-time snapshot of all runtime counters.
#[derive(Debug, serde::Serialize)]
pub struct RuntimeSnapshot {
    /// Number of HTTP uploads received.
    pub received_uploads: u64,
    /// Number of files that completed processing successfully.
    pub completed_files: u64,
    /// Number of successful inference responses.
    pub inference_ok: u64,
    /// Number of failed inference attempts.
    pub inference_error: u64,
    /// Number of MQTT messages received.
    pub mqtt_received: u64,
    /// Number of MQTT messages successfully forwarded to inference.
    pub mqtt_forwarded: u64,
    /// All unique sensor IDs seen since startup.
    pub sensors_seen: Vec<String>,
    /// sensor_id from the most recent ingest request.
    pub last_sensor_id: Option<String>,
    /// file_id from the most recent ingest request.
    pub last_file_id: Option<String>,
    /// current_ma from the most recent ingest request.
    pub last_current_ma: Option<f64>,
}

/// Shared runtime counters for the middleware service.
///
/// Atomic counters are updated from multiple async tasks without locking.
/// Mutex-protected fields are updated infrequently (once per ingest).
#[derive(Debug)]
pub struct RuntimeState {
    received_uploads: AtomicU64,
    completed_files: AtomicU64,
    inference_ok: AtomicU64,
    inference_error: AtomicU64,
    mqtt_received: AtomicU64,
    mqtt_forwarded: AtomicU64,
    sensors_seen: Mutex<HashSet<String>>,
    last_sensor_id: Mutex<Option<String>>,
    last_file_id: Mutex<Option<String>>,
    last_current_ma: Mutex<Option<f64>>,
}

impl RuntimeState {
    /// Create a new `RuntimeState` with all counters set to zero.
    pub fn new() -> Arc<Self> {
        Arc::new(RuntimeState {
            received_uploads: AtomicU64::new(0),
            completed_files: AtomicU64::new(0),
            inference_ok: AtomicU64::new(0),
            inference_error: AtomicU64::new(0),
            mqtt_received: AtomicU64::new(0),
            mqtt_forwarded: AtomicU64::new(0),
            sensors_seen: Mutex::new(HashSet::new()),
            last_sensor_id: Mutex::new(None),
            last_file_id: Mutex::new(None),
            last_current_ma: Mutex::new(None),
        })
    }

    /// Increment the count of HTTP sensor uploads received.
    pub fn increment_received_uploads(&self) {
        self.received_uploads.fetch_add(1, Ordering::Relaxed);
    }

    /// Increment the count of files that completed the full pipeline.
    pub fn increment_completed_files(&self) {
        self.completed_files.fetch_add(1, Ordering::Relaxed);
    }

    /// Increment the count of successful inference calls.
    pub fn increment_inference_ok(&self) {
        self.inference_ok.fetch_add(1, Ordering::Relaxed);
    }

    /// Increment the count of failed inference calls.
    pub fn increment_inference_error(&self) {
        self.inference_error.fetch_add(1, Ordering::Relaxed);
    }

    /// Increment the count of MQTT messages received.
    pub fn increment_mqtt_received(&self) {
        self.mqtt_received.fetch_add(1, Ordering::Relaxed);
    }

    /// Increment the count of MQTT messages forwarded successfully.
    pub fn increment_mqtt_forwarded(&self) {
        self.mqtt_forwarded.fetch_add(1, Ordering::Relaxed);
    }

    /// Record metadata from a successfully processed ingest request.
    pub fn record_ingest(&self, sensor_id: &str, file_id: &str, current_ma: Option<f64>) {
        self.sensors_seen
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(sensor_id.to_string());
        *self.last_sensor_id.lock().unwrap_or_else(|p| p.into_inner()) =
            Some(sensor_id.to_string());
        *self.last_file_id.lock().unwrap_or_else(|p| p.into_inner()) =
            Some(file_id.to_string());
        *self.last_current_ma.lock().unwrap_or_else(|p| p.into_inner()) = current_ma;
    }

    /// Return a consistent snapshot of all counters.
    pub fn snapshot(&self) -> RuntimeSnapshot {
        let sensors_seen = self
            .sensors_seen
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .iter()
            .cloned()
            .collect();
        let last_sensor_id = self
            .last_sensor_id
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone();
        let last_file_id = self
            .last_file_id
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone();
        let last_current_ma = *self
            .last_current_ma
            .lock()
            .unwrap_or_else(|p| p.into_inner());

        RuntimeSnapshot {
            received_uploads: self.received_uploads.load(Ordering::Relaxed),
            completed_files: self.completed_files.load(Ordering::Relaxed),
            inference_ok: self.inference_ok.load(Ordering::Relaxed),
            inference_error: self.inference_error.load(Ordering::Relaxed),
            mqtt_received: self.mqtt_received.load(Ordering::Relaxed),
            mqtt_forwarded: self.mqtt_forwarded.load(Ordering::Relaxed),
            sensors_seen,
            last_sensor_id,
            last_file_id,
            last_current_ma,
        }
    }
}

impl Default for RuntimeState {
    fn default() -> Self {
        RuntimeState {
            received_uploads: AtomicU64::new(0),
            completed_files: AtomicU64::new(0),
            inference_ok: AtomicU64::new(0),
            inference_error: AtomicU64::new(0),
            mqtt_received: AtomicU64::new(0),
            mqtt_forwarded: AtomicU64::new(0),
            sensors_seen: Mutex::new(HashSet::new()),
            last_sensor_id: Mutex::new(None),
            last_file_id: Mutex::new(None),
            last_current_ma: Mutex::new(None),
        }
    }
}
