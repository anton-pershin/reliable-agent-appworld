## Baseline AppWorld solution

**Status**: active

### Objective

Select, implement, and evaluate a credible baseline agent for the requirements in `../constitution-spec.md`: high AppWorld task success, useful error awareness, consistency across repeated runs, and timely final responses. The investigation should identify the smallest well-supported design that can be implemented and measured, while preserving the constitution's reusable, benchmark-independent decision boundary.

### Current constraints

- Follow `../constitution-spec.md` for requirements, preferences, layered architecture, and acceptance through the authoritative `appworld` profile.
- Use Pydantic AI and Pydantic models where applicable; keep AppWorld protocol integration separate from the reusable decision engine.
- Do not use reference answers, private evaluator data, benchmark-specific solvers, fixed model seeds, or disabled continuous batching.
- Plan to use self-hosted `qwen/qwen3.5-9b`; an A10 server will be made available when the serving and evaluation plan is ready.
- Preserve public-task confidentiality boundaries and never commit credentials or private validation data.
- Use partial validation for iteration and full validation only for the authoritative acceptance result.

### Current state

The solution repository contains the constitution and terminology but no implementation. The validation service has an AppWorld profile and a documented per-episode process protocol. A dedicated current validator instance is running locally for configuration discovery, but no episodes have been submitted. There is no measured solution baseline yet, so task success, error awareness, consistency, latency, and resource use remain unknown.

### Contexts

| ID | Solution implementation | Validation setup | Runtime and environment | Baseline |
|----|-------------------------|------------------|-------------------------|----------|
| C1 | No implementation; constitution at the merged project baseline | `agent-validation` AppWorld support at commit `cc09c57` during initial contract review | Local CPU-only investigation host; A10 model server not yet provisioned | No solution measurements |
| C2 | No implementation; configuration discovery only | Current validator commit `cc09c57` serving `appworld` at `127.0.0.1:8681` | Local CPU-only investigation host; no AppWorld episodes or model provider | Successful discovery, no solution measurements |

### Current understanding

#### Supported by evidence

- The constitution requires a layered design and permits either caller-controlled interaction or a complete task runner; validation uses caller-controlled interaction.
- The validator starts `python -m reliable_agent_appworld` once per episode and exchanges versioned JSON Lines messages over standard input/output. It owns AppWorld code execution and private scoring.
- AppWorld exposes one `execute_code` tool backed by a stateful Python shell. Public task data includes the instruction, supervisor information, and application descriptions; private evaluation data must not reach the solution.
- The registered AppWorld profile covers unperturbed repeated episodes and evaluates overall success, error awareness, consistency, and timely responses. Numeric thresholds and the full population are obtained from the validator rather than duplicated here.
- The existing local validation endpoints on ports 8678–8680 do not expose the `appworld` profile; a separate current validation instance is required for our runs ([local discovery](evidence/baseline-local-services.json)).

#### Tentative or unresolved

- The simplest decision policy capable of solving multi-step AppWorld tasks is unknown.
- The amount of API discovery, observation retention, context management, and completion checking needed by the baseline is unknown.
- A reliable method for calibrated `likely_incorrect` flags is not yet established.
- Model serving parameters, practical episode latency, prompt size, and A10 memory use require measurement.
- The accessible validation-service base URL and operational model-provider settings still need to be established.

### Experiments and decisions

| ID | Idea or question | Context | Result and evidence links | Decision and rationale |
|----|------------------|---------|---------------------------|------------------------|
| E1 | What exact process, message, tool, timing, and evidence contract must the baseline satisfy? | C1 | Pending | Pending |
| E2 | Can an existing local validation service run the AppWorld profile? | C1 | All three discovered endpoints returned HTTP 404 for AppWorld configuration discovery: [local discovery](evidence/baseline-local-services.json). | Leave those services unchanged. Start a separate current AppWorld-capable instance when the implementation is ready for contract tests. |
| E3 | Can the current validator expose its authoritative AppWorld configuration through the documented HTTP boundary? | C2 | Discovery succeeded and returned a complete editable configuration plus matching schema: [discovery summary](evidence/appworld-discovery-summary.json). | Use this dedicated service for investigation-stage discovery. Select partial configurations by modifying the complete returned copy; do not duplicate its thresholds in solution documentation. |
| E4 | Can the pinned AppWorld runtime be provisioned from the detached current source without an explicit source-path override? | C2 | No; the first attempt failed before creating a runtime because Python imported the older installed PDDL-only validator: [attempt 1](evidence/appworld-provisioning-attempt1.json). | Treat this as an invocation error, not a validator defect. Repeat with `PYTHONPATH` pinned to the detached current source and verify imported module paths. |

### Next candidates

- [ ] Reconstruct the validator's exact AppWorld solution lifecycle and configuration-discovery workflow from the current source and tests.
- [ ] Study the related reliable-agent implementations for reusable protocol, state-management, and self-assessment patterns without importing benchmark-specific policy.
- [ ] Study official AppWorld examples and public task/API mechanics to identify the smallest general-purpose interaction loop.
- [ ] Compare candidate baseline decision policies and select one for a local protocol/model-stub harness before GPU deployment.
- [ ] Define an A10 serving plan and a staged partial-validation experiment matrix with explicit observations and decision gates.

### Linked specs

- None yet.
