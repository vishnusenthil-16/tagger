# Validation

## Automated checks

The Rust integration suite starts the actual service executable and synthetic HTTP providers on loopback. It covers classification, no-match and ties; ordering and provenance; strict request validation and authentication; body and field limits; provider status mappings; malformed, oversized and compressed responses; confidence; unreachable providers; overload and capacity recovery; upload and backend deadlines; disconnect cancellation; startup errors; the smoke CLI; proxy-environment isolation; and log redaction.

Tests do not use external credentials. The checked-in [OpenAPI contract](openapi.json) is tested for its endpoint, security, required response fields, and error statuses.

Run the development commands in [README.md](../README.md). CI runs formatting, Clippy, tests, Rustdoc, optimized builds, and source-package verification on Linux and macOS. The package uses an explicit source allowlist that excludes credentials, private inputs, conversation attachments, and build outputs.

## Live smoke testing

Start Tagger with your backend settings and separate service-client key as described in [usage](usage.md), then run:

```sh
cargo build --locked --release --bins
./target/release/tagger-smoke --url http://127.0.0.1:8080 --expect-backend jev
```

The smoke CLI uses `TAGGER_API_KEY` and makes three real upstream requests. It checks response semantics, complete distributions, and provenance. It does not measure model accuracy, calibration, capacity, or latency. Synthetic local test success does not establish live endpoint compatibility; run this check against your configured endpoint before deployment.
