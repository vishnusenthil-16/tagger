# Implementation and validation evidence

> Historical Python implementation evidence. These checks and live results do not certify the Rust rewrite. See [Rust migration and validation](rust-migration.md) for current evidence.

Date: **2026-11-02**. V1 scope: **upstream Jev only**. This is implementation evidence, not an independent code review or an accuracy benchmark.

## Gate status

| Gate | Result | Evidence |
| --- | --- | --- |
| A — contract/configuration | Implemented and locally validated | Strict settings, startup rejection, request/result/error schemas, arbitrary categories, auth separation, UTF-8/body limits, public error mappings |
| B — Jev adapter | End-to-end validated | Primary-source contract inspection, Jev adapter and precision profile, three successful Jev calls using the project-specific vault credential |
| C — execution/fault tests | Implemented and locally validated | Deadlines spanning upload and inference, immediate overload, per-process slots, cancellation/cleanup, malformed output, status/transport failures, no retries/fallback |
| D — docs/evidence | Delivered for Jev | README, configuration/API guide, generated OpenAPI, backend caveats, smoke command, local checks and live evidence |

**Scope update:** Laya has been removed from the v1 runtime, schemas and release criteria. It is not a remaining gate. No model deployment was added. See [release readiness](release-readiness.md) for checks on the Jev-only revision.

## Local verification

- `uv run pytest -q`: **159 passed, 3 subtests passed** after the five P3 review fixes (case-insensitive Bearer scheme, distinct client/backend keys, dead-code cleanup, and documentation grammar). Includes the six pre-existing optional evaluation-tool tests and three documentation/OpenAPI consistency tests.
- Real TCP tests: the Tagger CLI runs in a subprocess and calls a separate local HTTP provider for Jev; three synthetic requests plus unauthorized-request rejection. Proxy variables are deliberately invalid, proving those variables are not used. No external credentials are consumed by tests.
- `uv run ruff check src tests scripts/run_live_smoke.py`: passed.
- `uv run ruff format --check src tests scripts/run_live_smoke.py`: passed.
- `uv sync --locked`: passed with Python 3.12.13.
- `uv build`: wheel and source distribution built successfully.
- CLI help and documented curl flags checked; request JSON exercised through HTTP. OpenAPI generated from the implementation.
- CI workflow added for these local checks; no remote CI run or publication is claimed.

Fault tests exercise all 19 public error codes, invalid Unicode/JSON, byte/count/ID boundaries, backend `400`/`401`/`403`/`413`/`422`/`429`/`529`, unknown upstream errors, transport failures/timeouts, malformed/oversized/compressed output, exact ties/no-match, cleanup after cancellation, and capacity recovery. The tests include large malformed numeric values and oversized declared body lengths to prevent unsanitized internal errors.

## Live Jev end-to-end checks

A project-specific backend credential was injected into `TAGGER_BACKEND_API_KEY` for the child process only and was not persisted. A random temporary service-client key was kept only in process environments.

Path: local HTTP client → temporary Tagger CLI process → `https://api.typesafe.ai/v1/systemone` → validated public HTTP response. The temporary service was terminated after the run.

| Synthetic case | Public status | Configured model | Reported model |
| --- | --- | --- | --- |
| Software defect vs feature request | `classified` | `jev-1.14.0` | `jev-1.14.0` |
| Kitchenware vs clothing | `classified` | `jev-1.14.0` | `jev-1.14.0` |
| Weather text vs sports/music | `no_match` | `jev-1.14.0` | `jev-1.14.0` |

**3/3 passed.** These validate connectivity, auth, reserved-option acceptance, multiple caller category sets, complete distributions, response semantics and provenance. They do not measure model accuracy, calibration, capacity or latency. Real timeout/rate-limit/auth failures and exact model ties were not deliberately induced; those behaviors were tested locally.

Four preliminary direct synthetic requests are not counted in the 3/3 end-to-end checks. Their observed score precision informs the documented Jev adapter profile.

## Reproduce

Run the local commands in [README.md](../README.md). For live checks, inject the selected backend configuration and credential with your secret manager, then run:

```sh
cargo build --locked --release --bins
python3 scripts/run_live_smoke.py
```

This makes three real upstream requests. Alternatively, use `tagger-smoke` against an already-running service. Neither path stores credentials or changes model infrastructure. See [backend notes](backends.md) for precision and truncation caveats and [usage](usage.md) for limits and errors.
