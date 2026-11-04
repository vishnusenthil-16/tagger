# Tagger

Tagger is a stateless HTTP service that categorizes English text using categories supplied with each request. It calls a user-managed Jev endpoint; it does not host models or store categories.

## Install

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```sh
uv sync --locked
```

## Quick start

Inject `TAGGER_BACKEND_API_KEY` (your TypeSafe key) and `TAGGER_API_KEY` (a separate key for callers of Tagger) through your secret manager or environment first. Tagger reads **only** its `TAGGER_*` variables; it does not use `TYPESAFE_API_KEY` or automatically load `.env` files.

```sh
export TAGGER_BACKEND=jev
export TAGGER_BACKEND_BASE_URL=https://api.typesafe.ai
export TAGGER_BACKEND_MODEL=jev-1.14.0
uv run tagger --host 127.0.0.1 --port 8080
```

In another terminal with the same service-client key:

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/classify \
  -H "Authorization: Bearer $TAGGER_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/classify.json
```

The JSON result contains a `classified`, `no_match`, or `needs_review` status, a label/candidate, the full probability distribution, and model provenance. Scores are model suggestions, not accuracy guarantees. The example's categories are not built into the service.

## Configuration and API

See [configuration and usage](docs/usage.md) for request limits, authentication, result/error schemas and concurrency behavior. [.env.example](.env.example) lists settings without real credentials.

V1 supports only Jev; selecting another backend fails startup. No request can override the backend, URL, model or credentials. The default concurrency cap is one, with immediate `503 overloaded` responses when full; there is no retry, fallback or waiting queue.

## Backend support and validation

The Jev adapter targets `POST /v1/systemone`. Use the endpoint's **service root** (optionally including a reverse-proxy prefix), not a URL already ending in `/v1` or `/systemone`.

[Jev compatibility notes](docs/backends.md) distinguish documented contracts, local synthetic tests, and live verification. [Validation evidence](docs/validation.md) records exactly what has run; [release readiness](docs/release-readiness.md) covers the Jev-only candidate and publication status. Model provisioning and deployment remain the endpoint owner's responsibility.

To send three synthetic contract checks through an already-running Tagger instance:

```sh
uv run tagger-smoke --url http://127.0.0.1:8080 --expect-backend jev
```

This uses `TAGGER_API_KEY`. It makes three real backend calls and checks the response contract, not accuracy. For a temporary local process, inject the backend settings and run `uv run python scripts/run_live_smoke.py`; its service-client key is generated in memory and the process stops after the checks.

## Development

```sh
uv sync --locked
uv run ruff check src tests scripts/run_live_smoke.py
uv run ruff format --check src tests scripts/run_live_smoke.py
uv run pytest -q
uv build
```

Tests use synthetic local providers and never consume external API keys. The historical evaluation scripts are optional and are not service acceptance gates. See the [implementation plan](docs/plan/v_1/PLAN.md).

## Releases

The [release workflow](.github/workflows/release.yml) publishes a wheel (`.whl`)
and source archive (`.tar.gz`) to GitHub Releases when a stable version tag such
as `v0.1.0` is pushed. It does not publish to PyPI.

Maintainers: update `project.version` in `pyproject.toml`, run `uv lock`, and merge
that change into `main` first. From an up-to-date `main` checkout, create and push
an annotated, signed tag matching the package version:

```sh
git tag -s v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

The workflow rejects mismatched versions, non-stable tags, and commits outside
`main` history. Lint, formatting, tests, and builds must pass before publishing.
PRs touching the workflow or package metadata exercise the build job without
release-write permissions; only tag runs can publish.

Existing releases are not overwritten. If a run fails, inspect its logs and any
existing release/draft before retrying; do not move an already-published tag.

## License

[Apache-2.0](LICENSE).
