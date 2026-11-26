# Reusable reliable-agent findings

## Sources and revisions

- Mainline source: `/home/tony/reps/github/anton-pershin/reliable-agent` at `1bd60810bcce6410b8cdd3ab4bc936f496dc826e`.
- Unmerged repeatability branch: `/home/tony/reps/github/anton-pershin/worktrees/reliable-agent-ac3-repeatability`, with accepted PDDL implementation at `46bddba5bc90a0b2892bb48aea17afe3397ecb34`.
- Mainline full PDDL validation run: `20261117T150114-324103Z-9acb73d41f31`, run directory `/home/tony/reps/github/anton-pershin/agent-validation/artifacts/runs/20261117T150114-324103Z-9acb73d41f31`.
- Accepted PDDL branch run: `20261119T104916-200097Z-56ebe409152d`, run directory `/home/tony/reps/github/anton-pershin/agent-validation/artifacts/runs/20261119T104916-200097Z-56ebe409152d`.

These are different-benchmark results. They establish code/review lessons, not expected AppWorld performance.

## Reuse with narrow adaptation

- **Contracts:** strict Pydantic task/tool/action/observation/final models, assignment validation, strict booleans, and finite bounded confidence.
- **Runner:** asynchronous one-outstanding-call loop, exact call/observation matching, action accounting, controlled stops, and independent per-episode state.
- **Protocol:** versioned JSON Lines, bounded nonblocking writes, blocking-I/O guards, and guarded final delivery. Include the unmerged branch's write-done ordering fix rather than copying the mainline race.
- **Model client:** Pydantic AI typed outputs, validation retries, text fallback, request metadata, and explicit remaining-time limits. Use one absolute deadline across initial requests, repairs, and fallback.
- **Observability:** ordered JSON events, config/dependency provenance, credential redaction, and explicit unavailable/truncated fields.
- **Tests:** Pydantic AI `FunctionModel`/`TestModel`, scripted executors, fake clocks, subprocess protocol tests, cancellation/retry/redaction regressions, and delayed final-write cases.

The fresh source review found 99 mainline tests and 171 branch tests passing; this is supporting implementation evidence, while the durable validation runs above remain authoritative for benchmark outcomes.

## Do not port

- PDDL goal predicates, exact state-goal checking, goal regression, transition learning, learned-rule search, and object enumeration.
- Default plan sampling/voting/review pipeline. AppWorld's unstructured state and incremental API discovery fit a reactive one-code-action loop better.
- Fixed seed `0`, temperature `0.0`, or deterministic serving assumptions.
- Unbounded INFO payload logging. Prior validation showed that detailed logs can themselves cause output-limit failures and reduce timely completion.
- Mainline item-count-only history truncation; AppWorld needs byte/token-aware observation and history bounds.
- A verifier that sees only the latest observation; AppWorld completion may depend on earlier evidence.
- Benchmark-specific validation imports or long solution-override combinations.

## Measured cautions

- Mainline self-hosted PDDL validation was valid but not accepted (VM1 `0.665`, VM4 `0.25`). The perfect unmerged branch relies heavily on formal PDDL structure and must not be treated as a generic recipe.
- Full-candidate reviews and larger sample sets caused substantial latency/provider overload; more requests are not automatically better.
- Short screens did not reliably predict full-population repeatability. Predeclare experiment populations and retain negative/inconclusive results.
- Detailed logging changed outcomes through output limits; compact operational logs must be the default validation configuration.
- Final response delivery, strict flags, and timeout behavior are correctness mechanisms, not optional polish.
