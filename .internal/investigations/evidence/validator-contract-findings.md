# Validator contract findings

Source: `agent-validation` commit [`cc09c570d4341adb31b9bc8ce49557d3ca896abe`](https://github.com/anton-pershin/agent-validation/tree/cc09c570d4341adb31b9bc8ce49557d3ca896abe), especially the [solution interface](https://github.com/anton-pershin/agent-validation/blob/cc09c570d4341adb31b9bc8ce49557d3ca896abe/docs/solution-interface.md), `episodes.py`, `protocol.py`, `provisioning.py`, `appworld_session.py`, and committed tests.

## Installation and process

- Validation clones and checks out the exact submitted commit, installs it non-editably into a fresh virtual environment, and starts `python -m reliable_agent_appworld` in an empty per-episode workspace.
- Declare every runtime dependency and package prompt/resource explicitly. Do not depend on the checkout working directory, `PYTHONPATH`, or AppWorld being installed in the solution environment.
- Standard output is protocol-only JSON Lines; logs go to bounded standard error. Flush every protocol message.
- Provider configuration reaches the solution through the profile's allowed environment variables, principally `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

## Public interaction

- The first message has `type="task"`; `task.input` is itself a JSON string containing public `instruction`, `supervisor`, and `app_descriptions` fields. `task.instructions` is separate profile guidance.
- The only exposed tool is `execute_code`, with exactly one non-empty string `code` argument. Calls execute in one persistent shell with `apis` and `requester`; only printed output is observable.
- Send one non-empty action batch with a unique string `call_id`, then consume one ordered result per action. One action per turn is the safest baseline because every batch member consumes budget before validation.
- Syntax/runtime failures are ordinary observations. Preserve their output and revise later code rather than treating them as validator failures.

## Completion and self-assessment

- `apis.supervisor.complete_task(...)` and protocol `final` are separate requirements. Calling `complete_task` does not end the episode; the agent must subsequently send a final response.
- Answer-seeking tasks require the direct answer in `apis.supervisor.complete_task(answer=...)`; the protocol final's `answer` is not copied into AppWorld.
- Success requires `completed=true`, AppWorld task completion, and independent private evaluator success. No private outcome is sent to the agent.
- Always send strict booleans for `completed` and `likely_incorrect`, a finite confidence in `[0,1]`, and string answer/reason. An invalid error flag invalidates AC2 even when other final fields are accepted.
- A controlled `completed=false` final with `likely_incorrect=true` is preferable to a crash or timeout when the agent cannot complete the task.

## Budgets and failures

- The full profile currently allows 300 seconds from task delivery through final response, including model calls and self-assessment. The implementation needs an internal deadline and a no-model fallback response near exhaustion.
- The registered AppWorld interaction safety limit is 100, independent of disabled AC6. A lower configurable solution budget is desirable for the baseline.
- Solution output, logs, and observations have strict byte limits. Avoid dumping complete API documentation or large result sets; retain bounded diagnostic logs and explicit truncation records.
- AppWorld setup/evaluator/worker failures are infrastructure failures and make criteria uncomputable. Do not interpret them as solution task failures.

## Local acceptance harness implications

1. Test pure contracts and the decision loop with model/tool stubs.
2. Test the installed module as a subprocess with protocol-only stdout, malformed observations, final fallback, and redacted bounded logs.
3. Run one real task and one repeat with AC3 omitted for the first model-backed partial run.
4. Run at least two repeats with all four criteria before drawing consistency conclusions.
5. Inspect public protocol records, solution logs, run status, and metric evidence. Do not use private evaluation reports to tune the agent.
