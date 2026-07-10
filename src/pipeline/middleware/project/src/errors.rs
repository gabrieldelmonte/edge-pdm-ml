/// Application-level errors that map to HTTP responses.
#[derive(Debug)]
#[allow(dead_code)]
pub enum AppError {
    /// The request was malformed or missing required fields.
    BadRequest(String),
    /// The upstream inference service returned an error or was unreachable.
    BadGateway(String),
    /// An unexpected internal error occurred.
    Internal(String),
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AppError::BadRequest(msg) => write!(f, "bad request: {}", msg),
            AppError::BadGateway(msg) => write!(f, "bad gateway: {}", msg),
            AppError::Internal(msg) => write!(f, "internal error: {}", msg),
        }
    }
}

impl axum::response::IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        use axum::http::StatusCode;
        use axum::Json;

        let (status, message) = match &self {
            AppError::BadRequest(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            AppError::BadGateway(msg) => (StatusCode::BAD_GATEWAY, msg.clone()),
            AppError::Internal(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg.clone()),
        };

        let body = serde_json::json!({
            "status": "error",
            "message": message,
        });

        (status, Json(body)).into_response()
    }
}
