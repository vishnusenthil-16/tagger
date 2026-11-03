# Earlier Phase 1 — optional evaluation tooling

Status: superseded as a mandatory implementation phase on 2026-11-02. The [current plan](PLAN.md) defines a generic English-text categorization service with user-supplied categories and a user-managed Jev endpoint (v1).

## Existing work retained

- `scripts/prepare_evaluation.py`: optional taxonomy/dataset validation, split-leakage checks, hashes, hardware metadata, and evaluation-manifest generation.
- `evaluation/examples/`: synthetic email fixtures and an example manifest, not production-quality evidence or built-in service categories.
- `tests/test_prepare_evaluation.py`: six tooling tests passed when implemented.

These files are not runtime dependencies. Their fixed taxonomy, screening thresholds, source/thread schema, hardware recording, and evaluation prerequisites do not apply to the service. They remain available for someone who independently wants to evaluate an email use case. No model calls or accuracy evaluation were performed by this work.

The former instructions are preserved in [history/PHASE_1_evaluation.md](history/PHASE_1_evaluation.md) as historical, optional material. They do not override the current plan. The revised gate A is service contract/configuration implementation; it is now implemented and locally validated separately from this historical tooling. See [current validation evidence](../../validation.md).
