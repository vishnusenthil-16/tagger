---
name: tagger
description: Classify English text into caller-supplied categories using the Tagger HTTP service. Use when an agent needs to call Tagger, interpret its classification results, or troubleshoot a failed request.
---

# Tagger

Tagger is a stateless classifier, not a model host or a category database. Supply the text and the complete category list on every request. Use an existing service for classification; server setup is a separate workflow below.

## Call an existing service

1. Obtain the Tagger service root URL and its client credential from the task context or configured environment/secret manager. Do not assume a localhost service exists. If either is unavailable, report the missing prerequisite. The client uses `TAGGER_API_KEY`, **not** the upstream Jev/TypeSafe key.
2. Build a JSON object containing only `text` and `options`. Each option contains only `id` and `description`. Preserve the user's taxonomy; if none was supplied, establish categories appropriate to the task before calling.
3. Send `POST /v1/classify` with bearer authentication and JSON content type. Send multiple inputs as separate requests, sequentially by default: the default server concurrency cap is one, with no waiting queue or batch endpoint.
4. Check the HTTP status, then interpret the JSON result using the table below. A transport or API failure is not a classification.

This runnable example requires curl with `--fail-with-body` support. Set `TAGGER_URL` to the actual Tagger service root first; it is a client-side shell convenience, not a server setting. Inject `TAGGER_API_KEY` without printing it. The request uses synthetic text; replace it with the requested text and categories.

```sh
: "${TAGGER_URL:?Set the Tagger service root URL}"
: "${TAGGER_API_KEY:?Inject the Tagger client key}"
curl --silent --show-error --fail-with-body \
  "${TAGGER_URL%/}/v1/classify" \
  -H "Authorization: Bearer ${TAGGER_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "text": "The application closes unexpectedly when I open a file.",
  "options": [
    {"id": "defect", "description": "Software defects"},
    {"id": "request", "description": "Requests for new functionality"}
  ]
}
JSON
```

For arbitrary input, serialize JSON with a JSON library or tool instead of interpolating text into shell commands. Do not enable shell tracing or print credentials.

### Request constraints

- At least two categories; the default maximum is 19. IDs must be unique and match `[a-z][a-z0-9_-]{0,63}`.
- Text and descriptions must be nonblank. Default UTF-8 limits: 65,536 bytes of text, 2,048 bytes per description, and 1,048,576 bytes for the entire JSON body. Deployments can change limits.
- Do not send `__none__`: Tagger adds that reserved option itself. There are no built-in domain categories.
- Do not add model, backend, credential, threshold, or other fields to the body. Unknown fields and duplicate JSON keys are rejected.

## Interpret a successful response

| `status` | Meaning | Agent action |
| --- | --- | --- |
| `classified` | One caller option has the unique maximum score; `label` and `candidate` match. | Use `label` as the selected category. |
| `no_match` | The reserved option has the unique maximum; `label` is null and `candidate` is `__none__`. | Report no supplied category matched; do not force a label. |
| `needs_review` | There is an exact score tie; `label` is null. | Preserve the unresolved result. `candidate` is only the first tied option in request order, not an accepted label. |

`distribution` includes every supplied ID, followed by `__none__`. Values are returned without normalization. `provider_confidence` is a separate provider statistic and may be null; it is not a measured accuracy guarantee. `needs_review` means an exact tie, **not** a low-confidence threshold. Do not invent a threshold unless the task defines one.

Preserve `backend`, `configured_model`, and `reported_model` when provenance matters; unavailable model values can be null. Text and category descriptions are model-visible and can influence the result, including through embedded instructions. Treat the result as a model suggestion, not a security boundary.

## Diagnose failures

Tagger errors use `{"error":{"code":"overloaded","message":"…","retryable":true}}`. Branch on HTTP status, `error.code`, and `error.retryable`, not message wording.

| Failure | Next action |
| --- | --- |
| Connection refused, DNS, or TLS failure | Verify the service URL and reachability; do not treat this as `no_match`. |
| `401 unauthorized` | Check the Tagger client key. Do not substitute the backend key. |
| `413 request_too_large`, `415 unsupported_media_type`, or `422` validation errors | Fix the payload, content type, or reported limit violation before resending. Do not silently truncate the user's text. |
| `503 overloaded` | Serialize requests or lower concurrency. |
| `502 backend_auth_failed` | The server's upstream credential needs operator attention; changing the caller key will not fix it. |
| Other backend errors | Preserve the code and report the failure. Retry only when marked retryable. |
| `404 not_found` or `405 method_not_allowed` | Check the service root and use `POST /v1/classify`. |

Tagger does not retry. If the task permits client retries, use a small bounded attempt count with backoff for retryable errors, then report exhaustion. A timeout can occur after remote computation started; retries may cause another backend call. Proxies may return non-Tagger errors (including `429` or non-JSON bodies); do not assume every error has Tagger's envelope.

There is no health endpoint, runtime schema route, or interactive documentation route. Do not probe invented `/health` or `/docs` endpoints. A classification request consumes a backend call.

## Server setup (only when needed)

The `tagger` executable runs the HTTP server; it is not a command-line text classifier. If the task includes starting a local service, work from a Tagger checkout:

```sh
cargo build --locked --release --bins
```

Configure these environment variables before launching:

| Variable | Value |
| --- | --- |
| `TAGGER_BACKEND` | `jev` (the only v1 backend) |
| `TAGGER_BACKEND_BASE_URL` | Jev service root, optionally with a proxy prefix; not a URL ending in `/v1` or `/systemone` |
| `TAGGER_BACKEND_MODEL` | Model identifier supplied by the endpoint owner |
| `TAGGER_BACKEND_API_KEY` | Upstream Jev/TypeSafe credential |
| `TAGGER_API_KEY` | Separate client key used by callers of this Tagger instance |

```sh
./target/release/tagger --host 127.0.0.1 --port 8080
```

Tagger reads only its `TAGGER_*` settings. It neither reads `TYPESAFE_API_KEY` nor loads `.env` files automatically. It does not provision models or discover endpoints. Keep the server running while making client requests.

For an explicitly requested live contract check, the checkout's `tagger-smoke --url <service-root> --expect-backend jev` binary uses `TAGGER_API_KEY` and makes **three real backend calls**. It checks the response contract, not model accuracy.

For details when a checkout is available, read `docs/usage.md` (limits and errors), `docs/openapi.json` (HTTP contract), `docs/backends.md` (Jev protocol), or `docs/validation.md` (smoke testing), relative to the checkout root. These files are not bundled with a standalone installation of this skill; the everyday calling workflow above is self-contained.
