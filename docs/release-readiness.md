# Jev-only v1 release readiness

> Historical Python implementation evidence. These checks and live results do not certify the Rust rewrite. See [Rust migration and validation](rust-migration.md) for current evidence.

Assessment date: 2026-11-02. Initial package version: `0.1.0`; HTTP API version: `/v1`. “V1” describes the service scope/API, not a change to package version `1.0.0`.

## Scope

Jev only. Laya is removed from executable code, configuration choices and response schemas, and is not a release dependency. Historical planning documents remain clearly marked as history.

## Checks

**Verdict: technically ready as an initial Jev-only release candidate; publication is not complete.**

| Check | Result |
| --- | --- |
| Jev-only local suite | 159 tests + 3 subtests passed (after the five P3 review fixes) |
| Lint and formatting | Passed |
| Wheel and source distribution build | Passed |
| Package metadata (`twine check`) | Both artifacts passed |
| Clean isolated wheel installation outside the repo | Passed; installed `tagger --help` works |
| Fresh Jev-only live HTTP smoke run | 3/3 passed, configured/reported model `jev-1.14.0` |
| Source archive boundary | Explicit project-file allowlist; inbound conversation attachments excluded |
| Remote CI | Pending PR CI; see the checks on the published revision |

The fresh live run used only a project-specific backend credential, injected into the temporary process. No credential was persisted. Model results were two `classified` and one `no_match`, matching the earlier smoke-check shapes. These are contract/connectivity checks, not accuracy guarantees.

Archive inspection caught conversation attachments in the default source-distribution file selection. The explicit allowlist and `media/` ignore rule correct that packaging issue. Those initial local artifacts were not published.

## Release process status

The reviewed implementation is being submitted as a PR with separate planning, service, and release-documentation commits. Remote CI must pass on the submitted revision before release. No tag, hosted release, registry publication or deployment has been created.

Before publishing a release, run CI on the reviewed revision, then tag and publish the intended artifacts. These are release-process steps, not missing service features. The code is assessed for an initial release, not independently certified for every deployment or task-specific accuracy requirement.

## Documented limits

- Probability sum tolerance uses observed two-decimal Jev output precision; alternate models/versions need compatibility checks.
- No automatic retry/fallback, deployment provisioning, built-in rate limiting or accuracy guarantees.
- Concurrency is per process; client cancellation cannot guarantee the remote model stops.

See [usage](usage.md), [backend contract](backends.md) and [validation evidence](validation.md).
