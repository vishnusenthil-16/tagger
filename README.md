# Tagger

Tagger is a stateless HTTP service that categorizes English text using categories supplied with each request. It calls a user-managed Jev endpoint; it does not host models or store categories.

## Install

Requires the current stable Rust toolchain (tested with Rust 1.99.1). The service and smoke CLI have no Python runtime dependency.

```sh
cargo build --locked --release --bins
```

## Quick start

Inject `TAGGER_BACKEND_API_KEY` (your TypeSafe key) and `TAGGER_API_KEY` (a separate key for callers of Tagger) through your secret manager or environment first. Tagger reads **only** its `TAGGER_*` variables; it does not use `TYPESAFE_API_KEY` or automatically load `.env` files.

```sh
export TAGGER_BACKEND=jev
export TAGGER_BACKEND_BASE_URL=https://api.typesafe.ai
export TAGGER_BACKEND_MODEL=jev-1.14.0
./target/release/tagger --host 127.0.0.1 --port 8080
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

[Jev compatibility notes](docs/backends.md) distinguish documented contracts, local synthetic tests, and live verification. [Rust validation](docs/rust-migration.md) records the current checks. Earlier [Python validation evidence](docs/validation.md) and [release assessment](docs/release-readiness.md) remain historical. Model provisioning and deployment remain the endpoint owner's responsibility.

To send three synthetic contract checks through an already-running Tagger instance:

```sh
./target/release/tagger-smoke --url http://127.0.0.1:8080 --expect-backend jev
```

This uses `TAGGER_API_KEY`. It makes three real backend calls and checks the response contract, not accuracy. For a temporary local process, inject the backend settings and run `python3 scripts/run_live_smoke.py` (optional Python standard-library orchestration); its service-client key is generated in memory and the process stops after the checks.

## Development

```sh
cargo fmt --all --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked --all-targets
cargo doc --locked --no-deps
cargo build --locked --release --bins
cargo package --locked --target-dir target/package-check
```

Tests use synthetic local providers and never consume external API keys. The historical evaluation scripts are optional and are not service acceptance gates. Run their standard-library tests with `python3 -m unittest discover -s evaluation/tests -v`. See the [implementation plan](docs/plan/v_1/PLAN.md).

## Releases

The [release workflow](.github/workflows/release.yml) builds Linux x86-64 and macOS ARM64 binary archives plus a Cargo source archive (`.crate`). Stable version tags such as `v0.1.0` publish these to GitHub Releases, not crates.io. The Linux binary targets the runner's glibc; it is not a portable static build.

Update `package.version` in `Cargo.toml` and the lockfile, pass CI, and merge to `main` before creating an annotated signed tag matching the version. The workflow rejects mismatched/non-stable tags and commits outside `main` history. It never overwrites an existing release. No release or deployment is performed by this rewrite.

## Rust migration

The HTTP endpoint, environment settings, Jev wire format, error envelope, and deterministic classification rules are retained. See [migration notes](docs/rust-migration.md) for transport-level differences and verification scope. The Python service and its packaging have been replaced; the optional historical evaluation preparation tool remains Python and is not a runtime dependency.

## License

[Apache-2.0](LICENSE).
