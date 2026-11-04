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

The complete typed contract and error table are in [PLAN.md](plan/v_1/PLAN.md). [openapi.json](openapi.json) is the checked-in HTTP contract artifact; interactive documentation and unauthenticated schema routes are not served by the runtime.

## Failures and capacity

All Tagger errors have this shape; messages are fixed sanitized explanations, not a machine interface:

```json
{"error":{"code":"overloaded","message":"Service capacity is currently full.","retryable":true}}
```

Use `code`, HTTP status and `retryable`, not the message. `true` means a later unchanged request may succeed; Tagger itself never retries. Unknown provider failures are not assumed to be caller input errors. Backend authentication failures return `502 backend_auth_failed`, never `401`.

The wall-clock deadline covers uploading, validation and upstream work. Expiry before the upstream attempt is `408 request_timeout`; after the attempt starts it is `504 backend_timeout`. Body size is enforced incrementally even without `Content-Length`. Backend responses are streamed with a fixed 1 MiB cap and must be uncompressed JSON. Oversized/invalid responses return `502 backend_invalid_output`.

A full process returns `503 overloaded` immediately. Slots remain held until upstream completion or local cancellation cleanup. Client disconnect cancels in-flight work best-effort; closing an HTTP call cannot stop remote computation reliably. Multiple processes multiply the cap. The provided CLI runs one process with a Tokio runtime; there is no distributed cap, queue, rate limiter or health endpoint. External rate limits may return `429`; those responses are not Tagger's error envelope. Bound aggregate connections/uploads in the HTTP server or surrounding infrastructure when needed.

Only outcome, backend and elapsed timing are logged. Access-path logging is off in the CLI. No requests, category descriptions, provider bodies, credentials or raw exceptions are persisted. Independently configured server/proxy logs are the operator's responsibility.

See [Rust migration notes](rust-migration.md) for malformed HTTP/Unicode handling and shutdown behavior.
