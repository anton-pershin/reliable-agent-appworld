# Accuracy experiment plan — 2026-11-26

## Evidence boundary

Use public task/protocol/model/serving evidence and aggregate validation metrics only. Do not inspect private evaluator reports, ground truth, reference answers, hidden task metadata, or credentials. Candidate changes must be general-purpose; no task-ID branches or benchmark-specific solvers.

## Established baseline

Run `20261125T132059-063795Z-2c936467dd77` evaluated commit `f8da94d9e8747317eedefed7a0260a69c6b9d606`: 630 completed episode records, zero successes, zero infrastructure failures, and one actual `complete_task` invocation. See `accuracy-initial-failure-analysis.json`.

## S1: six-task mechanism screen

Population fixed before candidate results: `e85d92a_1`, `c901732_1`, `396c5a2_3`, `82e2fac_1`, `2a163ab_1`, and `b119b1f_1`; one repeat; AC1/AC2/AC7. The set includes public answer extraction, an apparently completed effect, pagination risk, authenticated private data, cross-app authentication, and a previous completion-call anomaly. It is intentionally mechanism-oriented and must not be used as an unbiased accuracy estimate.

Common settings: 180-second internal episode ceiling, 30-second model cap, 15-second response reserve, 30 actions, validator concurrency 2, no fixed model seed. Non-thinking candidates use temperature 0.7, top-p 0.8, presence penalty 1.5, and at most 1024 output tokens.

- **A / commit `50e9629bd0e262d331e241b2c71fc67ac9fcf7d1`:** verified public completion with deferred final, concise discovery/authentication/pagination instructions, direct-answer semantics, remaining-time feedback, non-thinking generation.
- **B / commit `e9316ce1055d970968bdd98bd6749ec77b2f4822`:** A plus direct objective rendering and final-only handling at action exhaustion, with otherwise identical non-thinking settings.
- **C:** B with thinking enabled, temperature 0.6, top-p 0.95, presence penalty 0, and at most 1024 output tokens. This isolates the practical thinking/latency tradeoff on the latest prompt/loop.

A and B are sequential engineering probes, not independent ablations of every prompt sentence. C is the principal sampling comparison. Run C only if A/B show that the completion mechanism can yield successes or if non-thinking output quality is the clear blocker.

### S1 gates

- All selected metrics must compute; no infrastructure failure is acceptable.
- Every `completed=true` protocol final must be preceded by public successful completion evidence.
- Inspect all six public trajectories, including incorrect completions and failure flags.
- Promote only a candidate with actual task successes. If all remain zero, classify the dominant public failure and revise the hypothesis rather than expanding the population.
- Prefer higher task success; break ties by timely responses, fewer actions/errors, and lower wall latency. Do not infer consistency from one repeat.

## S2: broader one-repeat diagnostic

For the selected mechanism only, run a fresh stratified set of at least 12 tasks covering public reads, authenticated reads, single-app writes, multi-app writes, pagination, and direct answers. Freeze task IDs before submission. Use the same internal budgets and one repeat. Reject candidates whose successes are confined to the S1 mechanism set or whose error flags become misleading.

## S3: repeated dev confirmation

Run a frozen dev population with at least two scenarios, all three variants, and two repeats, selecting all four registered criteria. Compare with the merged baseline's public dev/full evidence where applicable. Require every criterion to be at least valid, no infrastructure failures, actual task successes, and no material repeat collapse. Expand only if the result is too sparse to distinguish candidates.

## Final implementation gate

After selecting the design, write and implement the general spec, complete independent review and all local/CI/wheel checks, and run final partial confirmation on the exact reviewed code revision. Submit full validation only once for that final revision unless it is invalid/fails or review changes runtime behavior. Append every terminal full run to validation history.
