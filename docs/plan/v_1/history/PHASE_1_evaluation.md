> Historical document, superseded on 2026-11-02. The current requirements are in [PLAN.md](../PLAN.md).

# Phase 1 — evaluation preregistration (gate A)

Status: preparation tooling implemented; gate A remains open pending representative data and target-hardware confirmation. No model calls, quality measurements, or held-out runs have occurred.

The original plan labels phases A–E. Phase 1 maps to A, not service construction (C). The accepted design and review are unchanged.

## Implemented

- Standard-library-only Python CLI: `scripts/prepare_evaluation.py` (verified with Python 3.15.0).
- Ordered taxonomy validation: 2–19 unique, meaningful ASCII IDs and nonempty descriptions; `__none__` remains reserved.
- Explicit tuning/held-out split validation, rejecting shared source IDs, thread IDs, or identical trimmed text across splits. Group assignments are supplied by the dataset curator, not randomized per message.
- Required case and label coverage in both splits, with optional multilingual coverage.
- Canonical SHA-256 hashes for taxonomy, dataset, each split, and numeric targets. Canonical JSON uses UTF-8, sorted object keys, compact separators, and preserved array order. These taxonomy-hash semantics must also be used by the later service.
- Hardware observation and separate target-confirmation flag; sample/label counts; immutable-by-default manifest creation (existing files are never overwritten).
- Synthetic fixture and six regression tests. Fixture split variants are intentionally related and suitable only for exercising tooling, not independent quality evaluation.

## Run the fixture

From the repository root:

```sh
mkdir -p evaluation/runs
python3 scripts/prepare_evaluation.py \
  --taxonomy evaluation/examples/taxonomy.json \
  --dataset evaluation/examples/emails.jsonl \
  --data-kind synthetic \
  --output evaluation/runs/fixture-manifest.json
python3 -m unittest discover -s tests -v
```

A second invocation with the same output path fails deliberately. Use a new versioned filename for changed inputs. The manifest contains hashes and aggregate metadata, not email bodies. Private data and generated runs are ignored by Git.

## Dataset contract and curation

The example taxonomy (billing, support, meetings) is a proposal, not a confirmed user taxonomy. Each JSONL record requires:

| Field | Meaning |
| --- | --- |
| `id` | Unique sample ID |
| `thread_id` | Stable thread identity across all messages |
| `source_id` | Stable leakage-group identity for related sources/templates; not a newly generated per-message ID to evade grouping |
| `text` | Subject plus latest plain-text body, with history/signature deliberately removed by curator |
| `label` | One taxonomy ID or `__none__` |
| `split` | `tuning` or `held_out` |
| `cases` | Nonempty list of coverage tags |

Required tags in each split: `clear`, `ambiguous`, `out_of_category`, `instruction_like`, `long_tail`, `misleading_ids`. Add `multilingual` and pass `--multilingual` if those inputs are in scope. All taxonomy labels and `__none__` must occur in each split. A curator must inspect tag accuracy, near duplicates, shared templates and source linkage; exact-text checks cannot establish semantic independence.

Use real, authorized, representative email data under `evaluation/private/`. Assign connected thread/source groups wholly to a single split before tuning. Target a 70/30 tuning/held-out allocation by group, but preserve representative label/case counts; document deviations and final counts before any inference. No training split is needed: fine-tuning is out of scope. Ambiguous examples still need an adjudicated single ground-truth label and documented rationale in the private source data. Lock labels before running models.

Run with `--data-kind representative` only for genuinely representative data. Run on the actual intended inference host and add `--confirm-target-hardware` only when confirmed. Neither flag certifies representativeness or passes a quality gate.

## Frozen screening targets

| Metric | Target |
| --- | --- |
| Accepted decision precision, including no-match | ≥95% |
| Review rate | ≤30% |
| No-match precision | ≥90% |
| No-match recall | ≥70% |
| Warm p95 latency, concurrency 1 | ≤2 seconds |
| Combined local service + inference peak RSS | ≤8 GiB |

These provisional targets are copied from the accepted plan and embedded in every manifest. They are screening criteria, not production guarantees. `accept_min` and `margin_min` are deliberately absent: pick them on tuning data in gate B, then freeze model/revision, policy/config hashes and thresholds before held-out inference. Do not weaken targets after seeing held-out results. Any protocol revision requires a new preregistration and disclosure of prior test exposure.

## Measurement protocol for later phases

- Accepted precision = correct accepted decisions / all accepted decisions (caller labels and `__none__`). Coverage = accepted / all; review rate = reviewed / all. Review is not a correct classification.
- No-match precision = correct accepted `__none__` / all accepted `__none__`. No-match recall = correct accepted `__none__` / all ground-truth `__none__`, including reviewed examples in the denominator. Empty denominators are undefined, never a pass.
- Report raw numerator/denominator counts and 95% Wilson intervals for each proportion. Also report source-group cluster-bootstrap intervals (10,000 resamples, fixed seed 20261102) where related samples remain within a split; tiny group counts remain explicitly inconclusive.
- Choose the majority label using tuning labels only (including `__none__`; ties resolved by taxonomy order followed by `__none__`). On held-out data, compare its accuracy on the exact subset accepted by the model, giving identical coverage. Also report its all-example accuracy and the model's candidate accuracy. Do not use held-out labels to choose a majority label or thresholds.
- Report per-case counts and outcomes, length distribution, cold latency and warm end-to-end p95 (nearest-rank), with concurrency 1 and declared warmup/sample counts. Measure combined service/inference peak RSS on the recorded target host; do not substitute weight size or a single process for the complete deployment.
- Publish dataset/model/policy/config hashes with evidence in later gates. Freeze the number of held-out examples before execution; no optional stopping after favorable results. Tiny synthetic fixtures and insufficient no-match/accepted denominators cannot justify production claims.

## Remaining gate-A prerequisites

1. Confirm the intended taxonomy, scope (including languages), and actual deployment hardware.
2. Supply and curate representative labeled emails, including near-limit lengths and adversarial cases, with honest thread/source grouping.
3. Record the final split counts, annotation rationale, warmup/sample counts and preregistration manifest before model integration/evaluation.

The checked-in `evaluation/examples/manifest.json` uses illustrative hardware values and a placeholder timestamp, not a development-host observation. Its `fixture_only` status and unconfirmed hardware role are intentional. Gate B remains the next implementation stage; the API service is not yet implemented.
