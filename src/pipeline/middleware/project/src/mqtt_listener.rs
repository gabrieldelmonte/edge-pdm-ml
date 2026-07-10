use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use dashmap::DashMap;
use rumqttc::{AsyncClient, EventLoop, MqttOptions, QoS};

use crate::config::AppConfig;
use crate::http_server::SensorIngestRequest;
use crate::inference_client::InferenceClient;
use crate::state::RuntimeState;

// ---------------------------------------------------------------------------
// Internal accumulator types
// ---------------------------------------------------------------------------

/// Accumulator for a multi-batch MQTT file transfer.
struct CsvAccumulator {
    /// Ordered list of (batch_index, csv_data) chunks received so far.
    chunks: Vec<(usize, String)>,
    /// Unix timestamp (seconds) when this accumulator was first created.
    created_at_secs: u64,
}

impl CsvAccumulator {
    fn new(batch_index: usize, csv_data: String) -> Self {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        CsvAccumulator {
            chunks: vec![(batch_index, csv_data)],
            created_at_secs: now,
        }
    }

    fn add_chunk(&mut self, batch_index: usize, csv_data: String) {
        self.chunks.push((batch_index, csv_data));
    }

    /// Sort chunks by index and concatenate all CSV data.
    ///
    /// Each chunk's `csv_data` already ends with its own trailing newline
    /// (every row is terminated individually by the sender), so chunks are
    /// concatenated directly. Joining with an extra separator here would
    /// insert a spurious blank line at every batch boundary and corrupt the
    /// checksum computed over the original, unbatched CSV.
    fn assemble(&mut self) -> String {
        self.chunks.sort_by_key(|(idx, _)| *idx);
        self.chunks.iter().map(|(_, data)| data.as_str()).collect()
    }
}

// ---------------------------------------------------------------------------
// TTL eviction
// ---------------------------------------------------------------------------

fn current_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Remove accumulator entries that are older than `ttl_secs` seconds.
fn evict_stale(map: &DashMap<String, CsvAccumulator>, ttl_secs: u64) {
    let now = current_secs();
    map.retain(|file_id, acc| {
        let age = now.saturating_sub(acc.created_at_secs);
        if age > ttl_secs {
            tracing::warn!(
                file_id = %file_id,
                age_secs = age,
                "evicting stale MQTT accumulator (TTL exceeded)"
            );
            false
        } else {
            true
        }
    });
}

// ---------------------------------------------------------------------------
// MQTT setup helpers
// ---------------------------------------------------------------------------

/// rumqttc defaults to a 10 KB max packet size in both directions, which is
/// too small for a full (unbatched) capture's CSV payload. Sensor batches are
/// chunked to APP_CSV_CHUNK_BYTES (4 KB) on the firmware side, but a single
/// unbatched message (or a generously sized batch) can exceed 10 KB once
/// wrapped in JSON, so raise the limit well past what one capture needs.
const MQTT_MAX_PACKET_SIZE_BYTES: usize = 1024 * 1024;

fn build_mqtt_options(config: &AppConfig) -> MqttOptions {
    let client_id = format!("middleware-{}", std::process::id());
    let mut opts = MqttOptions::new(
        client_id,
        config.mqtt_broker_host.clone(),
        config.mqtt_broker_port,
    );
    opts.set_keep_alive(std::time::Duration::from_secs(30));
    opts.set_max_packet_size(MQTT_MAX_PACKET_SIZE_BYTES, MQTT_MAX_PACKET_SIZE_BYTES);
    opts
}

async fn subscribe_and_run(
    client: &AsyncClient,
    eventloop: &mut EventLoop,
    topic: &str,
    state: Arc<RuntimeState>,
    inference_client: InferenceClient,
    result_topic: String,
    accumulator: Arc<DashMap<String, CsvAccumulator>>,
) {
    if let Err(e) = client.subscribe(topic, QoS::AtLeastOnce).await {
        tracing::error!(error = %e, "failed to subscribe to MQTT topic");
        return;
    }
    tracing::info!(topic = %topic, "subscribed to MQTT input topic");

    let mut last_eviction = current_secs();

    loop {
        // Periodic TTL eviction every 30 seconds.
        let now = current_secs();
        if now.saturating_sub(last_eviction) >= 30 {
            evict_stale(&accumulator, 60);
            last_eviction = now;
        }

        match eventloop.poll().await {
            Ok(rumqttc::Event::Incoming(rumqttc::Packet::Publish(publish))) => {
                let payload_bytes = publish.payload.as_ref();
                let msg: SensorIngestRequest = match serde_json::from_slice(payload_bytes) {
                    Ok(m) => m,
                    Err(e) => {
                        tracing::warn!(error = %e, "failed to deserialize MQTT message, skipping");
                        continue;
                    }
                };

                state.increment_mqtt_received();

                handle_mqtt_message(
                    msg,
                    &state,
                    &inference_client,
                    &result_topic,
                    &accumulator,
                    client,
                )
                .await;
            }
            Ok(_) => {
                // Other MQTT events (connack, suback, pingresp, etc.) - no action needed.
            }
            Err(e) => {
                tracing::error!(error = %e, "MQTT event loop error");
                // Brief back-off before retrying to avoid tight error loops.
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

async fn handle_mqtt_message(
    msg: SensorIngestRequest,
    state: &Arc<RuntimeState>,
    inference_client: &InferenceClient,
    result_topic: &str,
    accumulator: &DashMap<String, CsvAccumulator>,
    client: &AsyncClient,
) {
    match msg.is_last_batch {
        None => {
            // Single-message transfer: forward immediately.
            forward_and_publish(msg, state, inference_client, result_topic, client).await;
        }
        Some(false) => {
            // Multi-batch: accumulate this chunk.
            let batch_index = msg.batch_index.unwrap_or(0) as usize;
            accumulator
                .entry(msg.file_id.clone())
                .and_modify(|acc| acc.add_chunk(batch_index, msg.csv_data.clone()))
                .or_insert_with(|| CsvAccumulator::new(batch_index, msg.csv_data.clone()));
            tracing::debug!(
                file_id = %msg.file_id,
                batch_index = batch_index,
                "accumulated MQTT batch chunk"
            );
        }
        Some(true) => {
            // Final batch: assemble and forward.
            let batch_index = msg.batch_index.unwrap_or(0) as usize;

            let full_csv = if let Some(mut acc) = accumulator.remove(&msg.file_id).map(|(_, v)| v) {
                acc.add_chunk(batch_index, msg.csv_data.clone());
                acc.assemble()
            } else {
                // No prior chunks stored - use this message alone.
                msg.csv_data.clone()
            };

            let full_request = SensorIngestRequest {
                csv_data: full_csv,
                is_last_batch: None,
                batch_index: None,
                ..msg
            };

            forward_and_publish(full_request, state, inference_client, result_topic, client).await;
        }
    }
}

async fn forward_and_publish(
    payload: SensorIngestRequest,
    state: &Arc<RuntimeState>,
    inference_client: &InferenceClient,
    result_topic: &str,
    client: &AsyncClient,
) {
    let t_start = std::time::Instant::now();
    let forward_result = inference_client.forward(&payload).await;
    let forward_elapsed_ms = t_start.elapsed().as_millis();

    match forward_result {
        Ok(result) => {
            state.increment_mqtt_forwarded();
            state.increment_inference_ok();
            state.increment_completed_files();
            state.record_ingest(
                payload.sensor_id.as_deref().unwrap_or("unknown"),
                &payload.file_id,
                payload.current_ma,
            );
            tracing::info!(
                file_id = %payload.file_id,
                transport = "mqtt",
                elapsed_ms = forward_elapsed_ms,
                "MQTT inference succeeded"
            );

            let result_bytes = match serde_json::to_vec(&result) {
                Ok(b) => b,
                Err(e) => {
                    tracing::error!(error = %e, "failed to serialize inference result for MQTT publish");
                    return;
                }
            };

            let t_publish = std::time::Instant::now();
            if let Err(e) = client
                .publish(result_topic, QoS::AtLeastOnce, false, result_bytes)
                .await
            {
                tracing::error!(error = %e, "failed to publish inference result to MQTT");
            } else {
                tracing::info!(
                    file_id = %payload.file_id,
                    publish_elapsed_ms = t_publish.elapsed().as_millis(),
                    total_elapsed_ms = t_start.elapsed().as_millis(),
                    "MQTT result published"
                );
            }
        }
        Err(e) => {
            state.increment_inference_error();
            tracing::error!(
                error = %e,
                file_id = %payload.file_id,
                transport = "mqtt",
                elapsed_ms = forward_elapsed_ms,
                "MQTT inference failed"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/// Run the MQTT listener loop.
///
/// Connects to the broker configured in `config`, subscribes to the input
/// topic, and processes incoming sensor payloads until the process exits.
pub async fn run_mqtt_listener(
    config: AppConfig,
    state: Arc<RuntimeState>,
    inference_client: InferenceClient,
) {
    let opts = build_mqtt_options(&config);
    let (client, mut eventloop) = AsyncClient::new(opts, 64);

    let accumulator: Arc<DashMap<String, CsvAccumulator>> = Arc::new(DashMap::new());
    let result_topic = config.mqtt_result_topic.clone();
    let input_topic = config.mqtt_input_topic.clone();

    tracing::info!(
        broker = %config.mqtt_broker_host,
        port = config.mqtt_broker_port,
        input_topic = %input_topic,
        "starting MQTT listener"
    );

    subscribe_and_run(
        &client,
        &mut eventloop,
        &input_topic,
        state,
        inference_client,
        result_topic,
        accumulator,
    )
    .await;
}
