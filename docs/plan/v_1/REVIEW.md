# Independent design review — historical v4

Scope notice (2026-11-02): this review applies only to [plan v4](history/PLAN_v4.md). The [current plan](PLAN.md) supersedes its taxonomy-evaluation and model-deployment requirements and has not received this independent review. Statements below are historical, not current acceptance gates.

Date: 2026-10-21
Reviewer: `openrouter/anthropic/claude-opus-5.5`; confirmed in execution history, not merely requested.

## Outcome

- v2: REVISE — seven findings covering integration, no-match semantics, review policy, input budgets, model identity, retries and isolation.
- v3: ACCEPT — deterministic policy and deployment gates accepted; four non-blocking clarifications requested.
- v4: ACCEPT — reviewer read the exact PLAN.md and confirmed the clarifications and local in-process contingency.

Primary agent independently checked TypeSafe confidence documentation, the Laya model card and LiteLLM decision documentation. Corrected the initial review's suggestion that LiteLLM support was merely a catalog entry. Omission is based on unnecessary dependency/operational scope for this MVP, not lack of documented endpoints.

## Final implementation notes from the accepting reviewer

These clarify gate B/C implementation without changing the accepted contract:

- Deployment topology follows gate B. If Laya runs in-process, there is one local service container and no separate inference API key or model-server hop. Apply the external-egress proof to that container. If it is a separate server, retain the private authenticated model endpoint and two-container isolation checks.
- In-process inference must run off the event loop (e.g. one worker thread), with explicit token budgets. HTTP timeout does not stop a forward pass: retain the single capacity slot until it actually exits; test continued API responsiveness and rejection of overlapping work.

## Evidence boundary

No inference benchmarks, implementation tests, deployment tests or quality gates have run. Acceptance is design readiness only. Generic unseen option sets intentionally produce review-marked candidates until the taxonomy is evaluated. Both required deployment modes remain release gates.
