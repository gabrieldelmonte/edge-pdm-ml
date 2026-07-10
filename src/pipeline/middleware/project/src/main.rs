mod config;
mod errors;
mod http_server;
mod inference_client;
mod mqtt_listener;
mod state;

use inference_client::InferenceClient;

#[tokio::main]
async fn main() {
    let app_config = config::AppConfig::from_env();

    // Initialize structured logging based on the debug_logs flag.
    if app_config.debug_logs {
        tracing_subscriber::fmt().pretty().init();
    } else {
        tracing_subscriber::fmt().json().init();
    }

    tracing::info!("middleware service starting");

    let runtime_state = state::RuntimeState::new();

    let inference_client = InferenceClient::new(app_config.inference_url.clone());

    if app_config.mqtt_enabled {
        let mqtt_client = inference_client.clone();
        let mqtt_state = runtime_state.clone();
        let mqtt_config = app_config.clone();
        tokio::spawn(async move {
            mqtt_listener::run_mqtt_listener(mqtt_config, mqtt_state, mqtt_client).await;
        });
    }

    let web_state = http_server::WebState {
        config: app_config.clone(),
        state: runtime_state,
        inference_client,
    };

    let router = http_server::build_router(web_state);

    let bind_addr = app_config.bind_addr.clone();
    let listener = tokio::net::TcpListener::bind(&bind_addr)
        .await
        .unwrap_or_else(|e| panic!("failed to bind to {}: {}", bind_addr, e));

    tracing::info!(addr = %bind_addr, "HTTP server listening");

    axum::serve(listener, router)
        .await
        .unwrap_or_else(|e| panic!("server error: {}", e));
}
