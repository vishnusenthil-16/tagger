# Tagger implementation plan v7

Status: v1 is Jev-only, as requested on 2026-11-02. Supersedes the dual-backend [v6 plan](history/PLAN_v6.md). The historical independent acceptance in [REVIEW.md](REVIEW.md) applies only to v4. Implementation and checks are recorded in [validation evidence](../../validation.md); this revision has not received an independent re-review.

## Scope and architecture

Tagger is a generic, stateless English-text categorization HTTP service. Callers supply their own categories with every request; there are no built-in domain categories, stored taxonomies, presets, or email-specific preprocessing. English is the supported scope, not a requirement to add language detection or reject text using an unreliable language heuristic.

Use Python, FastAPI/Pydantic, and httpx with pinned dependencies. One server-configured Jev backend per Tagger process. Laya and other providers are out of scope for v1. Users provide and operate an accessible endpoint. Use a narrow Jev adapter for the System One API. No LiteLLM dependency, DB, queue, agent framework, UI, connectors, multi-label output, or automatic backend fallback.

### Out of scope

- Deployment/orchestration of Tagger or models, homelab configuration, networking/egress proofs, and infrastructure management.
- Model provisioning, hosting, weight/tokenizer downloads, artifact verification, in-process inference, routing configuration, or model-server authentication setup.
- Mandatory labeled datasets, taxonomy registration, tuning/held-out evaluation, calibrated thresholds, accuracy guarantees, hardware-specific latency/RSS acceptance gates, and model selection research.
- Built-in rate limiting, quotas, and distributed concurrency control.

Users are responsible for endpoint availability, capacity, model configuration, and any task-specific quality evaluation. Tagger remains responsible for its own authentication, request validation, backend communication, output validation, deadlines, and bounded concurrency.

## Service configuration

Server-side environment variables; never accepted as classification-request overrides:

| Variable | Default / requirement |
| --- | --- |
| `TAGGER_BACKEND` | Required: `jev`; reject every other backend |
| `TAGGER_BACKEND_BASE_URL` | Required absolute HTTP(S) base URL; adapter appends its verified API route |
| `TAGGER_BACKEND_API_KEY` | Required Jev bearer key |
| `TAGGER_BACKEND_MODEL` | Required Jev model identifier |
| `TAGGER_API_KEY` | Required service-client key, separate from backend credentials |
| `TAGGER_REQUEST_TIMEOUT_SECONDS` | `30`; positive finite total request deadline |
| `TAGGER_MAX_REQUEST_BYTES` | `1048576` (1 MiB) |
| `TAGGER_MAX_TEXT_BYTES` | `65536` (64 KiB) |
| `TAGGER_MAX_CATEGORIES` | `19` caller categories; minimum configured value is 2 |
| `TAGGER_MAX_CATEGORY_DESCRIPTION_BYTES` | `2048` (2 KiB) |
| `TAGGER_MAX_CONCURRENT_REQUESTS` | `1` per process |

These are Tagger settings and proposed service defaults, not claims about provider-defined parameters or verified Jev capacities. Resolve actual auth, model-selection, base-path joining, and route requirements during adapter verification. Base URLs must not contain embedded credentials, query strings, or fragments. HTTPS certificate verification stays enabled; HTTP is supported for user-managed internal endpoints.

Fail startup on missing required configuration, invalid backend/URL, nonpositive or noninteger size/concurrency settings, nonfinite/nonpositive deadlines, category maxima below 2, or limits above a verified adapter hard limit. Count any adapter-added reserved option toward the backend maximum. Reject field byte caps larger than the total body cap; the total cap independently limits all fields together. Never silently clamp invalid settings.

## API and result semantics

`POST /v1/classify` accepts only `text` and ordered `options: [{id, description}]`. Reject unexpected fields, including backend, URL, credentials, and model overrides. Preserve caller content and option order; do not strip, rewrite, or truncate inputs. Whitespace-only text/descriptions are invalid.

IDs must be unique ASCII matching `[a-z][a-z0-9_-]{0,63}`. IDs and descriptions are model-visible. Accept 2 through the configured maximum number of caller categories.

Retain the reserved `__none__` hypothesis from the original contract: "None of the supplied categories applies to this content." This is a protocol-level no-match option, not a built-in domain taxonomy or a guarantee of out-of-distribution detection. Verify that the Jev adapter can represent it faithfully; never silently drop it to fit an upstream limit.

Remove `accept_min`, `margin_min`, policy IDs, evaluated-taxonomy hashes, and `policy_applicability` from the runtime contract. Any valid caller category set is eligible for a result without prior registration or evaluation.

Return a validated model suggestion:

- Unique maximum at a caller ID: `status=classified`, `label` and `candidate` are that ID.
- Unique maximum at `__none__`: `status=no_match`, `label=null`, `candidate=__none__`.
- Exact maximum tie: `status=needs_review`, `label=null`; choose the first tied option in request order (reserved option last) as the deterministic `candidate`.

These statuses describe model output, not empirical accuracy or confidence guarantees. Consumers decide whether to act automatically. Retaining exact-tie review does not introduce threshold tuning.

Return the complete provider-labeled raw distribution, separately named provider confidence when available, backend name, configured model identity when supplied, and actually reported model identity when available. Do not fabricate unavailable provenance or claim scores are calibrated/comparable across models.

Missing/extra labels, nonfinite/out-of-range probabilities, a sum outside the verified adapter tolerance, or a provider choice inconsistent with a maximum produce `502 backend_invalid_output`. Preserve raw values; never normalize or repair malformed distributions silently. Establish tolerance from actual documented/observed precision and option count during adapter verification, not an arbitrary permissive constant. If an endpoint cannot satisfy this result contract, report it as unsupported rather than inventing scores.

### Success response schema

All successful classifications return HTTP `200` with `Content-Type: application/json`. Every field below is required; unavailable values are explicit JSON `null`, not omitted. No additional response fields are allowed.

```json
{
  "status": "classified",
  "label": "bug",
  "candidate": "bug",
  "distribution": [
    {"id": "bug", "probability": 0.75},
    {"id": "feature", "probability": 0.15},
    {"id": "__none__", "probability": 0.10}
  ],
  "provider_confidence": null,
  "backend": "jev",
  "configured_model": null,
  "reported_model": null
}
```

| Field | Type and constraints |
| --- | --- |
| `status` | Enum: `classified`, `no_match`, `needs_review`; determined by the maximum/tie rules above |
| `label` | Caller ID string for `classified`; `null` otherwise |
| `candidate` | Caller ID string or `__none__`; always the deterministic maximum selected by the rules above |
| `distribution` | Array of objects with exactly `id: string` and `probability: number`; one entry per caller option in request order, followed by `__none__`; no missing, duplicate, or extra IDs |
| `distribution[].probability` | Finite JSON number in `[0, 1]`; provider value preserved without normalization, subject to the verified sum tolerance above |
| `provider_confidence` | Finite JSON number or `null`; only an explicitly reported confidence in the provider's selected result, never derived from the distribution. Adapter documentation must state its meaning and scale; unsupported/non-numeric confidence is `null` |
| `backend` | Literal `jev`; configured adapter name |
| `configured_model` | Nonempty string or `null`; server-configured model identifier when supplied |
| `reported_model` | Nonempty string or `null`; model identifier actually reported by the provider, never inferred from configuration |

### Error response schema

All Tagger-generated HTTP errors use `Content-Type: application/json` and exactly this shape (no framework validation details, input echoes, or raw exception/provider text):

```json
{
  "error": {
    "code": "overloaded",
    "message": "Service capacity is currently full.",
    "retryable": true
  }
}
```

`error.code` is a string from the table below, `error.message` is a nonempty sanitized service-authored string, and `error.retryable` is a boolean. Messages are explanatory, not a stable machine interface. `retryable=true` means a later unchanged request may succeed; it neither promises success nor triggers an internal retry. `false` means callers should correct the request/configuration or investigate before retrying.

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

Accept media type `application/json` with optional parameters; JSON bodies must be UTF-8. Install custom validation and exception handlers so FastAPI defaults do not bypass this envelope. Preserve required HTTP headers such as `Allow` on 405 responses. Errors generated outside Tagger (for example proxy rate limits), connection failures before an HTTP response is possible, and disconnected clients are outside this response-envelope guarantee.

## Input limits

Enforce the body cap while reading, even without `Content-Length`; reject oversized declared bodies early but do not trust the header alone. Measure decoded text and descriptions in UTF-8 bytes. The total request cap measures received body bytes, including JSON overhead. Do not accept compressed request bodies unless bounded decompression is deliberately implemented later.

Validate before any backend call:

| Condition | HTTP / code |
| --- | --- |
| Request body exceeds cap | `413 request_too_large` |
| Empty/whitespace-only or oversized text | `422 invalid_text` |
| Category count outside allowed range | `422 invalid_category_count` |
| Duplicate or invalid category ID | `422 invalid_category_id` |
| Empty/whitespace-only or oversized description | `422 invalid_category_description` |
| Invalid JSON, field types, or unexpected fields | `422 invalid_request` |

Byte limits protect Tagger; they do not prove the fully assembled backend request fits a model context window. Enforce verified adapter input limits, including instructions and reserved options. Exact token checking is conditional on an already-available exact tokenizer and known assembly; obtaining/provisioning tokenizers is not required. Otherwise rely on explicit upstream limit errors and document the limitation. Tagger never silently truncates; any provider truncation behavior discovered during integration must be documented, not presented as full-input processing.

## Concurrency, deadlines, and failures

Use httpx directly, with retries disabled. One upstream attempt per accepted request. Apply the configured absolute total deadline across request handling, admission, connection, response, and validation, not a separate full timeout for each stage.

Acquire a per-process capacity slot before the upstream call. If full, return `503 overloaded` immediately; no waiting queue. Retain the slot until the upstream operation finishes or local cancellation/cleanup completes, releasing it on every path. Client disconnect cancels waiting/upstream work best-effort. Cancelling an HTTP call cannot guarantee a remote model stops computation; upstream execution control remains the endpoint owner's responsibility.

The cap is per process: four workers with a cap of one permit four simultaneous backend calls. Document one worker as the initial configuration; operators size capacity against their endpoint. Body-size enforcement must not require consuming backend capacity.

Rate limiting is optional infrastructure outside Tagger: a user-provided proxy/API gateway may impose quotas/fairness and return `429 Too Many Requests`. Tagger overload remains `503`, distinct from rate limiting.

Use the error response schema and exhaustive mapping above. A deadline after upstream admission but before the actual attempt starts is still `408 request_timeout`; once the attempt starts it is `504 backend_timeout`. Backend authentication failures never masquerade as client authentication failures. Do not map every upstream `400`/`422` to a client fault: only verified input-limit rejections use `backend_input_limit_exceeded`; unknown failures use `backend_error`.

## Authentication and privacy

Authenticate Tagger clients using `Authorization: Bearer <TAGGER_API_KEY>`, independently of backend credentials. Return `WWW-Authenticate: Bearer` with `401 unauthorized`. Keep endpoint configuration and secrets server-side and out of error responses. Log only timing, outcome/error codes, and backend/model identifiers; never text, category descriptions, raw provider bodies, or secrets, including validation and exception logs. Do not persist requests or categories.

## Implementation sequence and acceptance gates

### A. Service contract and configuration

Implement typed startup configuration and request/response schemas from this plan. Test missing/invalid settings, unknown fields, arbitrary category sets, UTF-8 limits, body caps, category counts/IDs/descriptions, and separate client/backend authentication. Assert exact success/error field names and types, explicit null provenance, all three result statuses, and every error-table status/code/retryable combination with contract tests. Verify validation errors do not echo inputs or expose framework details. No dataset, taxonomy approval, or hardware confirmation prerequisite.

### B. Jev adapter

Verify current request/response contracts against provider documentation and user-provided endpoints. Record routes, authentication, model-selection rules, hard limits, score precision, reserved-option and tie behavior, and context-limit errors. Implement a narrow adapter and sanitized error mappings. Run live smoke calls on the supplied Jev endpoint; absent access is an integration-verification blocker, not a requirement to provision models. Mock tests may proceed independently but do not prove live compatibility.

### C. Service execution and fault tests

Implement classification orchestration, strict output validation, deterministic tie handling, per-process capacity, deadline/disconnect cleanup, and sanitized logs. Test exact boundaries, malformed distributions, overload, concurrent calls, slot release, timeouts/disconnects, upstream `400`/`401`/`422`/`429`/`529`, and transport errors. No retries or fallback.

### D. Usage documentation and verification evidence

Document env configuration for the user-provided Jev backend, request/result examples, limits, error codes, single-process capacity semantics, and score limitations. Record contract/fault test results and live adapter smoke results, including unavailable checks. Jev must be live-verified before claiming support. Laya is not a v1 release requirement. No deployment manifests, model setup, labeled quality benchmarks, latency/RSS targets, or homelab acceptance tests are required.

## Previous work

The earlier Phase 1 evaluation tooling remains optional and is not part of the service runtime or release gates. See [PHASE_1.md](PHASE_1.md). Its email fixtures, taxonomy hashes, and screening targets do not define Tagger's scope or configuration.

The superseded design is preserved in [history/PLAN_v4.md](history/PLAN_v4.md). The revised service is implemented in `src/tagger/`, independently of the old evaluation tooling. Gates A–D have Jev validation evidence. Laya is excluded from v1, not an outstanding release gate.
