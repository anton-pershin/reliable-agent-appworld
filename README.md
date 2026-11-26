# Reliable Agent AppWorld

A small reactive agent for AppWorld and other caller-controlled tool environments. It uses Pydantic AI structured decisions, requests one code action at a time, learns from public observations, and returns an explicit completion declaration with confidence and `likely_incorrect` self-assessment.

The reusable engine and runner do not import AppWorld or the validation service. The JSON Lines integration supplies protocol translation plus injected public onboarding/completion policies; the decision engine depends only on generic callbacks and typed state.

## Install

Python 3.11 or later is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

For an exact runtime used in validation, install the repository non-editably and inspect the resolved dependency manifest in the validator's run evidence.

## Run

The process handles one episode on standard input/output. Standard output is protocol-only; structured logs go to standard error.

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local
.venv/bin/python -m reliable_agent_appworld
```

The default model is `openai:qwen/qwen3.5-9b`. Pydantic AI's startup banner is suppressed because stdout is part of the protocol.

Use `python -m reliable_agent_appworld --help` for overrides. Configuration can also be supplied through `RELIABLE_AGENT_APPWORLD_<UPPERCASE_FIELD_NAME>` environment variables.

Important defaults:

| Setting | Default |
|---------|---------|
| Output mode | `auto`: structured tool output, then JSON text after provider rejection |
| Sampling | Temperature `0.6`, top-p `0.95`, top-k `20`, min-p `0` |
| Penalties | Presence `0`, repetition `1.0` |
| Maximum generated tokens | `1024` |
| Model request cap / identical outer retries | `60` seconds / `0` |
| Internal episode budget | `180` seconds |
| Action budget | `30` single-action calls |
| Decision reserve | `35` seconds for final-only decisions |
| Final-response reserve | `15` seconds |
| Adaptive public discovery/authentication | Enabled for private-context and action-only objectives |
| Automatic observed completion | Enabled |
| Individual observation budget | `12,000` characters |
| Public history budget | `24,000` characters |
| Maximum log bytes | `750,000` |
| Process-wide stderr proxy | `900,000` bytes |
| Cumulative protocol stdout | `1,000,000` bytes with reserved final space |

No model seed is configured. Model generation, repairs, action execution, and final delivery share an absolute episode deadline; transient identical outer model retries are disabled by default. Real standard-input/output descriptors use nonblocking I/O, and protocol stdout reserves space for a final response. Controlled failure responses use `completed=false` and `likely_incorrect=true`. The reusable runner propagates cancellation; the one-episode CLI applies its own bounded controlled-final policy.

For suitable private-context or action-only objectives, one public onboarding action discovers relevant schemas, authenticates task-relevant apps through permitted Supervisor APIs, and prefetches relationship contacts when needed. Public answer lookups skip onboarding. A model success claim is held as a deferred final: automatic completion emits the appropriate direct-answer or no-answer Supervisor call, and the saved final is released only after its public success marker is observed. This protects the completion channel but does not by itself prove task correctness.

## Interaction contract

The validator starts:

```bash
python -m reliable_agent_appworld [solution_overrides]
```

Protocol version 1 exchanges `task`, `actions`, `observation`, and `final` JSON objects, one per line. For AppWorld, `task.input` is a JSON string containing only public instruction, supervisor, and application-description data. The only supplied action is `execute_code`.

AppWorld `apis.supervisor.complete_task(...)` and the protocol final are separate requirements. Answer-seeking tasks must pass their direct answer to `complete_task(answer=...)`. Private scoring remains external and is never supplied to the agent.

The authoritative protocol is the [agent-validation solution interface](https://github.com/anton-pershin/agent-validation/blob/main/docs/solution-interface.md).

## Validation

Use a running current validation service with the `appworld` profile. Discover its complete configuration and schema rather than copying thresholds or task catalogs into this repository:

```bash
curl --fail http://127.0.0.1:8681/profiles/appworld/configuration \
  > /tmp/opencode/appworld-discovery.json
```

For partial validation, modify a complete copy of the returned `configuration` and submit `repo`, full `commit`, optional `solution_overrides`, `profile`, and `configuration` to `POST /validate_partial`. Use one repeat only when AC3 is omitted.

Full validation uses the unchanged active registered profile:

```bash
curl --fail -X POST http://127.0.0.1:8681/validate \
  -H 'Content-Type: application/json' \
  -d '{
    "repo": "https://github.com/anton-pershin/reliable-agent-appworld.git",
    "commit": "FULL_COMMIT_HASH",
    "solution_overrides": "--log-level ERROR"
  }'
```

Partial results are diagnostic only. Append every completed, failed, or aborted full run to `.internal/validation-results.md`; never replace earlier entries.

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/python -m build
```

The standard tests use Pydantic AI test models, scripted executors, in-memory streams, and a local fake OpenAI-compatible provider. They do not need a GPU, AppWorld data, real provider credentials, or private evaluation data.

## Package layout

- `contracts.py`: strict public and internal Pydantic contracts.
- `config.py`: typed CLI/environment configuration.
- `model.py`: bounded ReAct prompts and Pydantic AI generation.
- `engine.py`: reactive decisions, schema checks, and controlled stops.
- `runner.py`: asynchronous episode loop and absolute budgets.
- `protocol.py`: strict JSON Lines transport, caller-controlled executor, and injected public AppWorld policies.
- `observability.py`: redacted structured events with explicit truncation and byte limits.
- `__main__.py`: one-episode process assembly.

Project requirements are in `.internal/constitution-spec.md`. Increment acceptance checks are in `.internal/specs/01-baseline-agent-general-spec.md` and `.internal/specs/02-verified-react-accuracy-general-spec.md`.
