"""Public errors never contain input, credentials, or provider exception text."""

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# code: (HTTP status, retryable, fixed public message)
ERRORS = {
    "unauthorized": (401, False, "Missing or invalid service credentials."),
    "request_timeout": (408, True, "Request deadline exceeded before the backend call."),
    "request_too_large": (413, False, "Request body exceeds the configured limit."),
    "unsupported_media_type": (415, False, "Use an uncompressed UTF-8 application/json body."),
    "invalid_request": (422, False, "Request JSON does not match the required schema."),
    "invalid_text": (422, False, "Text must be nonblank valid Unicode within the byte limit."),
    "invalid_category_count": (422, False, "Category count is outside the configured range."),
    "invalid_category_id": (422, False, "Category IDs must be valid and unique."),
    "invalid_category_description": (
        422,
        False,
        "Descriptions must be nonblank valid Unicode within the byte limit.",
    ),
    "backend_input_limit_exceeded": (422, False, "Input exceeds a backend limit."),
    "backend_invalid_output": (502, False, "Backend returned an invalid result."),
    "backend_auth_failed": (502, False, "Backend authentication failed."),
    "backend_error": (502, False, "Backend request failed."),
    "backend_unavailable": (503, True, "Backend is temporarily unavailable."),
    "overloaded": (503, True, "Service capacity is currently full."),
    "backend_timeout": (504, True, "Backend request timed out."),
    "not_found": (404, False, "Route not found."),
    "method_not_allowed": (405, False, "Method not allowed."),
    "internal_error": (500, False, "Internal service error."),
}


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(json_schema_extra={"enum": list(ERRORS)})
    message: str = Field(min_length=1)
    retryable: bool


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    error: ErrorDetail


class ServiceError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def response(self, headers: dict[str, str] | None = None) -> JSONResponse:
        status, retryable, message = ERRORS[self.code]
        headers = dict(headers or {})
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=status,
            content={"error": {"code": self.code, "message": message, "retryable": retryable}},
            headers=headers,
        )
