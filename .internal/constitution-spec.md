## Reliable agent constitution spec

### 1. Executive summary

#### 1.1. Project description

The project will provide a reusable agent that achieves a high task success rate and consistent results across repeated runs on the AppWorld benchmark. For each completed task, the agent will return an answer, confidence, an explicit indication of whether its answer is likely incorrect, and a short reason.

#### 1.2. Project motivation

A high task success rate alone does not make an agent reliable: its results can change across repeated runs. The project aims to keep the task success rate high and nearly unchanged despite this noise, and to help users identify results that should not be trusted.

#### 1.3. Implementation repos

https://github.com/anton-pershin/reliable-agent-appworld — management and implementation repository.

#### 1.4. Architecture type

TBD

#### 1.5. Execution mode

Manual.

### 2. Requirement analysis

#### 2.1. Functional requirements

- **FR1. Overall success rate.** The agent must achieve high overall success rate under unperturbed evaluation conditions.
- **FR2. Error awareness.** The agent must be able to accurately flag unsuccessful episodes as likely incorrect.

#### 2.2. Non-functional requirements

- **NFR1. Consistency.** The difference between the highest and lowest overall success rates across repeated evaluation runs under unchanged conditions must be negligible.
- **NFR5. Time performance.** The vast majority of task episodes must terminate within the fixed time limit, measured from receipt of the task to the final response, including self-assessment. Episodes that reach the time limit without a final response count as unsuccessful.

#### 2.3. Preferences

- **P1. Task execution.** The agent should accept a task, instructions, and available tool descriptions, and use observations and tool results to work toward the task objective.
- **P2. Stateful interaction.** The agent should retain task state across interactions and expose an interface for producing the next action or response. It should also provide a runner for complete task execution when the calling system does not control the interaction loop.
- **P3. Integration.** The agent should support external tool execution and interaction protocols through adapters, without changes to its decision logic. Task setup, observations, action requests, and results should have explicit contracts.
- **P4. Completion and self-assessment.** The agent should explicitly declare task completion and return an answer, confidence, a boolean `likely_incorrect` indication, and a short reason. It should make this assessment without access to private evaluation results.
- **P5. Failure handling.** The agent should use observed tool or environment failures to decide whether to retry, revise its approach, or stop without declaring completion.
- **P6. Configuration.** The agent should expose configuration for model providers, generation settings, prompts, context management, tool bindings, retries, timeouts, execution budgets, and concurrency. Calling systems should be able to override these settings, and the effective configuration should be available without exposing credentials.
- **P7. Framework and data models.** Build the agent around Pydantic AI. Prefer Pydantic models for configuration, interaction contracts, and results.
- **P8. General-purpose reasoning.** The agent should solve tasks through model reasoning and ordinary observations and tool results, without reference answers, private evaluator data, or benchmark-specific solvers.
- **P9. Dependency boundaries.** Keep benchmark-specific integration code separate from the reusable decision engine. The solution should not depend on the identity or implementation of a validation service.
- **P10. Observability.** Write detailed, structured logs for each task episode and each decision, action, tool invocation, and model request. Logs should allow a reader to reconstruct the observable execution sequence and diagnose failures. Include:
  - Task and episode identifiers, linked operation and attempt identifiers, event order, start and end times, durations, and concurrent-operation relationships.
  - Task inputs, effective configuration, implementation and dependency versions, requested model and provider identifiers, available provider-reported identifiers, prompt versions, and configured random seeds.
  - Model request messages, tool descriptions, generation settings, public responses, reported usage, finish reasons, and request errors. Do not require or retain private model reasoning.
  - Action and tool arguments, returned observations, errors, execution status, and any reported or uncertain side effects. Distinguish a failed operation from an operation whose outcome is unknown.
  - Observable task-state updates, context truncation or summarization, loaded skills and resources, and tool availability changes. Record explicit plans or checks when the implementation uses them, without treating them as proof of correctness.
  - Retries, recovery steps, cancellations, timeouts, output parsing or schema failures, repairs, budget use, and termination reasons. Preserve failed and interrupted attempts, not only successful ones.
  - Completion declarations, final answers, confidence, likely incorrect flags, and their reasons. Distinguish the agent's completion claim from any externally established task outcome.
  - Links to retained artifacts and explicit indications of unavailable, omitted, truncated, redacted, or lost log data.
- **P11. Default sampling settings.** Use default sampling for LLMs as defined by the serving engine or the model card recommendation. If not found, use temperature 0.7, top_p 0.95, top_k 20. Do not restrict sampling in any other way. More specifically, do not fix the seed and do not disable continuous batching.
- **P12. Self-hosted qwen3.5-9b.** Use self-hosted `qwen/qwen3.5-9b` deployed on one of the available servers with GPUs.

  Logging detail, destinations, and retention should be configurable. Redact credentials and protect sensitive task content before persistence or export. Logs are diagnostic evidence supplied by the solution, not independently verified validation metrics; they should not require a model gateway or changes to the validation boundary.

### 3. Acceptance criteria

An external validation service evaluates each implementation against FR1–FR2 and NFR1 and NFR5. The implementation is accepted only when all these requirements are satisfied. Preferences in Section 2.3 are not acceptance criteria.

Use an already running HTTP validation service at a supplied base URL. The `appworld` registered profile is authoritative for project-level acceptance.

#### Configuration discovery

Send `GET /profiles/{profile}/configuration` to retrieve the registered `profile`, its complete normalized `configuration`, and the corresponding machine-readable JSON `schema`. The returned configuration can be edited and resubmitted without exposing credentials.

#### Full validation

Send `POST /validate` with `Content-Type: application/json` and this payload:

```json
{
  "repo": "<clonable link to solution implementation repo>",
  "commit": "<commit hash>",
  "solution_overrides": "<args separated by whitespaces>"
}
```

`repo` and `commit` identify the exact implementation. `solution_overrides` supplies execution configuration overrides; it is optional and defaults to an empty string. Full validation uses the active registered profile unchanged and rejects inline configuration.

#### Partial validation

For diagnostic feedback, fetch a registered configuration, edit the complete copy, and send `POST /validate_partial` with `Content-Type: application/json`. Its payload contains the same `repo`, `commit`, and optional `solution_overrides` fields as full validation, plus:

- `configuration`: required complete modified configuration object returned by discovery.
- `profile`: optional registered base-profile name, defaulting to the service's active profile.

The `configuration` object must contain every field from the discovered configuration, not only the fields being changed. Missing or unknown fields, invalid values, and invalid references are rejected rather than silently filled from defaults. The README provides an executable discovery-and-submission example without reproducing the registered configuration.

A partial configuration may modify any setting and need not be smaller or faster than full validation. Every result produced this way has `validation_scope="partial"` and is diagnostic only. It cannot certify acceptance, even when its population equals the full population or every reported criterion is accepted.

#### Results and run evidence

Successful full and partial responses contain `run_id`, `artifacts_location`, the registered base `profile`, `validation_scope`, and one entry for each acceptance criterion in scope. Errors raised after run creation also contain `run_id` and `artifacts_location`. The following example shows one criterion and one metric; other criteria and metrics use the same structure:

```json
{
  "run_id": "20261117T000000-000000Z-example",
  "artifacts_location": "/path/to/validation/run",
  "profile": "appworld",
  "validation_scope": "full",
  "AC1": {
    "status": "accepted",
    "metrics_status": {
      "VM1/unperturbed_overall_success_rate": {
        "computation_status": "computed",
        "acceptance_status": "accepted",
        "computed_value": 0.96,
        "expected_value_or_threshold": ">= 0.95",
        "error_message": null
      }
    }
  }
}
```

For every completed run, the same complete result is available at `<artifacts_location>/result.json`. The run manifest at `<artifacts_location>/run.json` records terminal run status as `completed`, `failed`, or `aborted`; this lifecycle status is distinct from the implementation outcome.

Each criterion has one of three statuses:

- `accepted`: all its metrics were computed and satisfy their acceptance conditions.
- `valid`: all its metrics were computed, but at least one acceptance condition is not satisfied.
- `invalid`: at least one metric could not be computed.

A metric that could not be computed has `computation_status` set to `failed_to_compute`, `computed_value` set to `null`, and an explanation in `error_message`. A computed metric has `acceptance_status` set to `accepted` or `rejected` and includes the applicable threshold or expected value.

Only a completed full-validation response from the unchanged authoritative `appworld` profile with every criterion in scope accepted certifies an accepted implementation. The project is complete only after that accepted implementation is integrated into the solution's main branch.

Append every completed, failed, or aborted full-validation run to `.internal/validation-results.md`, recording its run ID, related spec when applicable, solution commit, run status, and the returned `artifacts_location`. Never replace an earlier run entry. Partial runs do not belong in this history. Historical results produced before the run-aware contract remain in `.internal/validation-results-old.md`.

### 4. Insight

Up to further research.

### 5. Overall solution design

#### 5.1. High-level design

TBD

#### 5.2. Core components

TBD
