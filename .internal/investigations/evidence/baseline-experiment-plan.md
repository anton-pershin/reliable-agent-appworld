# Baseline experiment matrix and decision gates

## Principles

- Use exact solution and model/serving revisions for every run; retain run IDs and run directories.
- Use train for smoke and dev for candidate selection. Do not tune on test_normal or inspect private evaluation reports.
- Use complete discovered partial configurations and omit AC3 whenever a diagnostic has only one repeat.
- Keep all planned failures in the denominator. Do not promote a candidate from an aborted/incomplete screen.
- Compare prespecified candidates without fixed model seeds. Select on completed populations, not individual successful examples.
- Inspect only public task inputs, protocol traces, solution logs, aggregate metrics, and infrastructure evidence for development decisions.

## Stages

| Stage | Population and configuration | Purpose | Decision gate |
|-------|------------------------------|---------|---------------|
| S0 | No AppWorld episodes; synthetic model requests | Qualify A10 identity, sampling, tool/JSON output, unicode/multiline code, near-context behavior, and concurrency | Choose a stable serving configuration and supported primary/fallback output mechanism. No unresolved parser errors, OOM, or unexplained 5xx. |
| S1 | Completed: temporary no-op, train `82e2fac_1`, two repeats, all criteria | Prove installation, protocol finalization, metrics, and runtime isolation | Completed in run `20261125T071128-376661Z-347b316f96f5`; use only as plumbing evidence. |
| S2 | Real agent, train `82e2fac_1`, one repeat, AC1/AC2/AC7 | First model-backed public interaction | Require a completed run with no infrastructure failure, valid final/error flag, bounded logs, and diagnosable public trace. Task success is desirable but not a plumbing gate. |
| S3 | Dev scenarios `50e1ac9` and `fac291d`, all three variants of each, one repeat, AC1/AC2/AC7 | Compare the prespecified output/prompt candidates with identical populations | Reject candidates with malformed-output loops, logging/output failures, or systematic missing completion. Otherwise prefer higher success and timely completion, then fewer model calls/interactions. Do not draw consistency conclusions. |
| S4 | Selected candidate on the same six dev tasks, two repeats, AC1/AC2/AC3/AC7 | Confirm repeat behavior, error awareness, latency, and metric completeness | Require every metric to compute and no infrastructure failures. Record VM4 and per-repeat outcomes without treating two repeats as strong long-run evidence. |
| S5 | If material uncertainty remains, broaden to a prespecified disjoint dev set with two repeats | Distinguish a lucky small screen from a credible baseline | Complete the full prespecified population; retain negative and inconclusive outcomes. No test-set tuning. |
| S6 | Unchanged full `appworld` profile | Authoritative implementation feedback and project-level certification only if all criteria are accepted | Append the run to validation history regardless of outcome. Before merge, the latest full run must be completed and the implementation at least valid; a baseline increment need not close the overall project gap. |

## Candidate order

1. Pydantic AI structured `action or final` decisions with concise interactive-code ReAct instructions.
2. If S0/S2 shows provider/parser incompatibility, use schema-guided JSON text output with absolute-deadline-bounded repair.
3. Compare fenced-code extraction only if both structured modes prevent a functional baseline; do not add it preemptively.
4. After the concise prompt is measurable, test one fixed corrected public onboarding demonstration. Add it only if the completed dev screen supports the added prompt tokens.
5. Compare model-only self-assessment with conservative overrides for explicit failures, unverified completion, and budget exhaustion. Never use private results to set flags.

## Measurements beyond acceptance metrics

- Model request count, input/output tokens, TTFT/latency where available, and `finish_reason`.
- Malformed/repaired decisions, invalid tool arguments, missing `complete_task`, repeated code/actions, and premature finals.
- Observation/history sizes, truncation events, context-overflow responses, and output-token truncation.
- Episode end-to-end time, final-reserve use, controlled-stop reasons, and log byte counts.
- AppWorld/model infrastructure errors kept separate from solution task failures.
