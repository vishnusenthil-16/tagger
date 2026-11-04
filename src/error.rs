use bytes::Bytes;
use http_body_util::Full;
use hyper::{Response, StatusCode};
use serde_json::json;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ServiceError {
    Unauthorized,
    RequestTimeout,
    RequestTooLarge,
    UnsupportedMediaType,
    InvalidRequest,
    InvalidText,
    InvalidCategoryCount,
    InvalidCategoryId,
    InvalidCategoryDescription,
    BackendInputLimitExceeded,
    BackendInvalidOutput,
    BackendAuthFailed,
    BackendError,
    BackendUnavailable,
    Overloaded,
    BackendTimeout,
    NotFound,
    MethodNotAllowed,
    InternalError,
}
impl ServiceError {
    pub fn details(self) -> (u16, bool, &'static str, &'static str) {
        match self {
            Self::Unauthorized => (
                401,
                false,
                "unauthorized",
                "Missing or invalid service credentials.",
            ),
            Self::RequestTimeout => (
                408,
                true,
                "request_timeout",
                "Request deadline exceeded before the backend call.",
            ),
            Self::RequestTooLarge => (
                413,
                false,
                "request_too_large",
                "Request body exceeds the configured limit.",
            ),
            Self::UnsupportedMediaType => (
                415,
                false,
                "unsupported_media_type",
                "Use an uncompressed UTF-8 application/json body.",
            ),
            Self::InvalidRequest => (
                422,
                false,
                "invalid_request",
                "Request JSON does not match the required schema.",
            ),
            Self::InvalidText => (
                422,
                false,
                "invalid_text",
                "Text must be nonblank valid Unicode within the byte limit.",
            ),
            Self::InvalidCategoryCount => (
                422,
                false,
                "invalid_category_count",
                "Category count is outside the configured range.",
            ),
            Self::InvalidCategoryId => (
                422,
                false,
                "invalid_category_id",
                "Category IDs must be valid and unique.",
            ),
            Self::InvalidCategoryDescription => (
                422,
                false,
                "invalid_category_description",
                "Descriptions must be nonblank valid Unicode within the byte limit.",
            ),
            Self::BackendInputLimitExceeded => (
                422,
                false,
                "backend_input_limit_exceeded",
                "Input exceeds a backend limit.",
            ),
            Self::BackendInvalidOutput => (
                502,
                false,
                "backend_invalid_output",
                "Backend returned an invalid result.",
            ),
            Self::BackendAuthFailed => (
                502,
                false,
                "backend_auth_failed",
                "Backend authentication failed.",
            ),
            Self::BackendError => (502, false, "backend_error", "Backend request failed."),
            Self::BackendUnavailable => (
                503,
                true,
                "backend_unavailable",
                "Backend is temporarily unavailable.",
            ),
            Self::Overloaded => (
                503,
                true,
                "overloaded",
                "Service capacity is currently full.",
            ),
            Self::BackendTimeout => (504, true, "backend_timeout", "Backend request timed out."),
            Self::NotFound => (404, false, "not_found", "Route not found."),
            Self::MethodNotAllowed => (405, false, "method_not_allowed", "Method not allowed."),
            Self::InternalError => (500, false, "internal_error", "Internal service error."),
        }
    }
    pub fn response(self) -> Response<Full<Bytes>> {
        let (status, retryable, code, message) = self.details();
        let mut response = Response::new(Full::new(Bytes::from(
            json!({"error": {"code": code, "message": message, "retryable": retryable}})
                .to_string(),
        )));
        *response.status_mut() = StatusCode::from_u16(status).expect("static valid HTTP status");
        response.headers_mut().insert(
            "content-type",
            "application/json".parse().expect("static header"),
        );
        if status == 401 {
            response
                .headers_mut()
                .insert("www-authenticate", "Bearer".parse().expect("static header"));
        }
        if status == 405 {
            response
                .headers_mut()
                .insert("allow", "POST".parse().expect("static header"));
        }
        response
    }
}
