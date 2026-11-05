# Service usage

## Configuration

All settings are server-side environment variables. Missing required settings or invalid limits fail startup without printing values. No `.env` file is loaded implicitly. Dependency requirements are in `Cargo.toml`; exact resolution is committed in `Cargo.lock`. Use `--locked` for builds.

| Variable | Default / requirement |
| --- | --- |
| `TAGGER_BACKEND` | Required: `jev` (v1 rejects every other value) |
| `TAGGER_BACKEND_BASE_URL` | Required HTTP(S) service root; `/v1/systemone` is appended, preserving any prefix |
| `TAGGER_BACKEND_API_KEY` | Required Jev bearer key |
| `TAGGER_BACKEND_MODEL` | Required Jev model identifier |
| `TAGGER_API_KEY` | Required independent service-client bearer key |
| `TAGGER_REQUEST_TIMEOUT_SECONDS` | `30`, positive finite wall-clock deadline |
| `TAGGER_MAX_REQUEST_BYTES` | `1048576` received body bytes, including JSON overhead |
| `TAGGER_MAX_TEXT_BYTES` | `65536` decoded UTF-8 bytes |
| `TAGGER_MAX_CATEGORIES` | `19` caller options; minimum 2 |
| `TAGGER_MAX_CATEGORY_DESCRIPTION_BYTES` | `2048` decoded UTF-8 bytes per description |
| `TAGGER_MAX_CONCURRENT_REQUESTS` | `1` per process |

Size/count limits must be positive integers; field byte caps cannot exceed the body cap. Jev permits at most 254 caller categories (plus the reserved option) and a 32 MiB configured body cap. These transport limits are not token-budget guarantees.

Use a separate service-client key and backend key. Keys must be nonempty printable ASCII without whitespace. HTTPS certificate verification is enabled. HTTP is permitted for user-managed internal endpoints. URLs cannot contain credentials, queries or fragments. Redirects, proxy environment variables and `.netrc` authentication are disabled. No automatic retries or fallback occur.

Laya and other providers are outside v1 scope. Tagger does not provision or discover a server, download weights/tokenizers, or configure model routing.

## Request

`POST /v1/classify`, with `Authorization: Bearer <TAGGER_API_KEY>` and `Content-Type: application/json`. Send UTF-8 JSON without compression. Authentication is checked before the body is read; a `401` includes `WWW-Authenticate: Bearer`.

```json
{
  "text": "The application closes unexpectedly when I open a file.",
  "options": [
    {"id": "defect", "description": "Software defects"},
    {"id": "request", "description": "Requests for new functionality"}
  ]
}
```

Only these fields are accepted. IDs are unique ASCII matching `[a-z][a-z0-9_-]{0,63}`. Text/descriptions must be nonblank Unicode and fit their UTF-8 caps; lone surrogates are rejected. Input text, descriptions and order are preserved. Duplicate JSON keys, malformed JSON, nonfinite constants, unexpected fields and type coercions are rejected. Validation order is authentication → media/body checks → JSON/schema → text → count → IDs → descriptions → admission → adapter limits.

The service adds `__none__` as the last option: no supplied category applies. It is not a domain category or a guaranteed out-of-distribution detector. Content and categories are model-visible; instructions embedded in either may influence a result. The adapter sends content in the separate `state` field, but this is not a prompt-injection guarantee.

## Response

A `200` response always includes all fields, with explicit `null` for unavailable provenance/confidence. Example shape (illustrative scores):

```json
{
  "status": "classified",
  "label": "defect",
  "candidate": "defect",
  "distribution": [
    {"id": "defect", "probability": 0.8},
    {"id": "request", "probability": 0.1},
    {"id": "__none__", "probability": 0.1}
  ],
  "provider_confidence": 0.7,
  "backend": "jev",
  "configured_model": "jev-1.14.0",
  "reported_model": "jev-1.14.0"
}
```

- A unique caller-option maximum produces `classified` with matching `label` and `candidate`.
- A unique reserved-option maximum produces `no_match`, `label: null`, `candidate: "__none__"`.
- An exact returned-score tie produces `needs_review`, `label: null`, and the first tied option in request order as `candidate` (reserved option last).

`distribution` always contains all supplied IDs plus `__none__`, in that order. Raw numeric values are preserved, not normalized. `provider_confidence` is a separately reported provider statistic, not computed by Tagger; see the Jev definition in the backend notes. Missing/nonnumeric confidence becomes `null`, while nonfinite or out-of-range numeric confidence is rejected. Configured and reported model identities remain separate; omitted provenance is never fabricated.

[openapi.json](openapi.json) is the checked-in HTTP contract artifact; interactive documentation and unauthenticated schema routes are not served by the runtime.

## Failures and capacity

All Tagger errors have this shape; messages are fixed sanitized explanations, not a machine interface:

```json
{"error":{"code":"overloaded","message":"Service capacity is currently full.","retryable":true}}
```

Use `code`, HTTP status and `retryable`, not the message. `true` means a later unchanged request may succeed; Tagger itself never retries. Unknown provider failures are not assumed to be caller input errors. Backend authentication failures return `502 backend_auth_failed`, never `401`.

The wall-clock deadline covers uploading, validation and upstream work. Expiry before the upstream attempt is `408 request_timeout`; after the attempt starts it is `504 backend_timeout`. Body size is enforced incrementally even without `Content-Length`. Backend responses are streamed with a fixed 1 MiB cap and must be uncompressed JSON. Oversized/invalid responses return `502 backend_invalid_output`.

A full process returns `503 overloaded` immediately. Slots remain held until upstream completion or local cancellation cleanup. Client disconnect cancels in-flight work best-effort; closing an HTTP call cannot stop remote computation reliably. Multiple processes multiply the cap. The provided CLI runs one process with a Tokio runtime; there is no distributed cap, queue, rate limiter or health endpoint. External rate limits may return `429`; those responses are not Tagger's error envelope. Bound aggregate connections/uploads in the HTTP server or surrounding infrastructure when needed.

Only outcome, backend and elapsed timing are logged. Access-path logging is off in the CLI. No requests, category descriptions, provider bodies, credentials or raw exceptions are persisted. Independently configured server/proxy logs are the operator's responsibility.

## Transport and shutdown

The supported interface is the HTTP API and the `tagger` and `tagger-smoke` executables, not a Rust library API. Tokio handles asynchronous I/O, Hyper handles HTTP/1 connections, and Reqwest uses Rustls for TLS. Capacity permits are released on success, failure, timeout, or local cancellation.

Malformed Unicode escapes, including lone surrogates, return `422 invalid_request`. Excessively nested JSON is rejected by the parser. Blank-field checks include Unicode whitespace and ASCII separators U+001C–U+001F.

Malformed HTTP framing or header syntax may be rejected by Hyper before application handling, with a transport-level response or a closed connection instead of the JSON error envelope.

SIGINT/SIGTERM stops admission and cancels local connection tasks without waiting for remote model completion. Local cancellation does not guarantee that remote computation stops.

## Error reference

| Condition | HTTP | `error.code` | `retryable` |
| --- | --- | --- | --- |
| Missing or invalid Tagger client credentials | 401 | `unauthorized` | false |
| Total deadline exceeded before the upstream attempt starts | 408 | `request_timeout` | true |
| Request body exceeds cap | 413 | `request_too_large` | false |
| Non-JSON content type or unsupported body encoding (anything other than absent/identity) | 415 | `unsupported_media_type` | false |
| Invalid JSON, field types, or unexpected/missing fields | 422 | `invalid_request` | false |
| Empty/whitespace-only or oversized text | 422 | `invalid_text` | false |
| Category count outside allowed range | 422 | `invalid_category_count` | false |
| Duplicate or invalid category ID | 422 | `invalid_category_id` | false |
| Empty/whitespace-only or oversized description | 422 | `invalid_category_description` | false |
| Verified adapter input limit exceeded or explicit, verified upstream context/token-limit rejection | 422 | `backend_input_limit_exceeded` | false |
| Invalid upstream output, including malformed distributions | 502 | `backend_invalid_output` | false |
| Verified upstream authentication/authorization rejection | 502 | `backend_auth_failed` | false |
| Unexpected upstream status or error not mapped to a verified condition | 502 | `backend_error` | false |
| Transport failure (DNS/connect/TLS/read/write failure), or upstream `429`/`529` | 503 | `backend_unavailable` | true |
| No per-process upstream capacity available | 503 | `overloaded` | true |
| Total deadline exceeded after the upstream attempt starts, or upstream transport timeout | 504 | `backend_timeout` | true |
| Unknown Tagger route | 404 | `not_found` | false |
| Unsupported method on a known route | 405 | `method_not_allowed` | false |
| Unexpected internal service failure | 500 | `internal_error` | false |
