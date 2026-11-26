import json
import os

import pytest
from pydantic import ValidationError

from reliable_agent_appworld.config import ENV_PREFIX, AgentConfig, load_config
from reliable_agent_appworld.contracts import (
    ActionAttempt,
    ActionBatch,
    ActionRequest,
    ActionResult,
    ActionsMessage,
    FinalDecision,
    FinalMessage,
    ObservationMessage,
    Task,
    TaskMessage,
    TaskState,
    ToolDescription,
    VersionedMessage,
)


@pytest.fixture(autouse=True)
def _clear_config_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name)


def _appworld_task() -> Task:
    public_input = {
        "task_id": "82e2fac_1",
        "supervisor": {"first_name": "Alex"},
        "public_api": "apis.supervisor",
    }
    return Task(
        id="82e2fac_1",
        instructions=(
            "Discover public APIs, execute one small step, and call "
            "apis.supervisor.complete_task(...) before finishing."
        ),
        input=json.dumps(public_input, sort_keys=True),
        tools=[
            ToolDescription(
                name="execute_code",
                description="Execute one inspectable AppWorld Python step.",
                arguments_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string", "minLength": 1}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
            )
        ],
    )


def _final_values(**overrides):
    values = {
        "completed": True,
        "answer": "Task complete",
        "confidence": 0.75,
        "likely_incorrect": False,
        "reason": "The public observation confirms completion.",
    }
    return values | overrides


def _final_message_values(**overrides):
    return _final_values(protocol_version=1, type="final", **overrides)


def _attempt(call_id: str) -> ActionAttempt:
    return ActionAttempt(
        call_id=call_id,
        action=ActionRequest(name="execute_code", arguments={"code": "print('ok')"}),
        results=[ActionResult(status="ok", observation={"stdout": "ok\n"})],
        duration_seconds=0.01,
    )


def test_appworld_task_and_tool_schema_round_trip():
    message = TaskMessage(
        protocol_version=1, type="task", episode_id="episode-1", task=_appworld_task()
    )

    decoded = TaskMessage.model_validate_json(message.model_dump_json())

    assert decoded == message
    assert decoded.protocol_version == 1
    assert decoded.type == "task"
    assert isinstance(decoded.task.input, str)
    assert json.loads(decoded.task.input) == {
        "task_id": "82e2fac_1",
        "supervisor": {"first_name": "Alex"},
        "public_api": "apis.supervisor",
    }
    schema = decoded.task.tools[0].arguments_schema
    assert schema["required"] == ["code"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": ""},
        {"id": "task-1", "input": {"already": "decoded"}},
        {"id": "task-1", "private_evaluation": "forbidden"},
    ],
)
def test_invalid_task_contracts_are_rejected(payload):
    with pytest.raises(ValidationError):
        Task.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "execute_code", "arguments_schema": []},
        {"name": "execute_code", "unexpected": True},
    ],
)
def test_invalid_tool_contracts_are_rejected(payload):
    with pytest.raises(ValidationError):
        ToolDescription.model_validate(payload)


def test_appworld_action_observation_and_final_contracts_round_trip():
    action = ActionRequest(
        name="execute_code",
        arguments={"code": "apis.supervisor.complete_task(answer='Task complete')"},
    )
    actions = ActionsMessage(
        protocol_version=1, type="actions", call_id="call-1", actions=[action]
    )
    results = [
        ActionResult(
            status="ok",
            observation={"stdout": "", "result": "Task complete"},
        )
    ]
    observation = ObservationMessage(
        protocol_version=1, type="observation", call_id="call-1", results=results
    )
    final = FinalDecision(**_final_values()).as_message()

    for message in (actions, observation, final):
        assert type(message).model_validate_json(message.model_dump_json()) == message
        assert message.protocol_version == 1

    assert observation.as_observation().call_id == actions.call_id
    assert observation.as_observation().results == results
    assert final.type == "final"
    assert final.completed is True
    assert final.likely_incorrect is False

    with pytest.raises(ValidationError):
        ActionBatch(actions=[])
    with pytest.raises(ValidationError):
        ActionsMessage(protocol_version=1, type="actions", call_id="call-1", actions=[])


def test_protocol_envelope_fields_are_required():
    assert VersionedMessage(protocol_version=1).protocol_version == 1
    with pytest.raises(ValidationError):
        VersionedMessage()


@pytest.mark.parametrize("version", [True, 1.0, "1", 0, 2, None])
def test_protocol_version_is_strict_and_only_one(version):
    with pytest.raises(ValidationError):
        VersionedMessage(protocol_version=version)


@pytest.mark.parametrize("field", ["completed", "likely_incorrect"])
@pytest.mark.parametrize("value", [0, 1, 1.0, "true", "false", None])
def test_final_self_assessment_requires_strict_booleans(field, value):
    with pytest.raises(ValidationError):
        FinalMessage.model_validate(_final_message_values(**{field: value}))


@pytest.mark.parametrize("confidence", [0, 0.25, 1])
def test_confidence_accepts_finite_json_numbers_in_range(confidence):
    final = FinalMessage.model_validate(_final_message_values(confidence=confidence))

    assert final.confidence == confidence
    assert isinstance(final.confidence, float)


@pytest.mark.parametrize(
    "confidence",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(10**400, id="huge-integer"),
        pytest.param(1e100, id="huge-float"),
        -0.01,
        1.01,
        True,
        "0.5",
    ],
)
def test_confidence_rejects_nonfinite_out_of_range_and_non_numeric_values(confidence):
    with pytest.raises(ValidationError):
        FinalMessage.model_validate(_final_message_values(confidence=confidence))


def test_task_state_allocates_sequential_call_ids_without_reuse():
    state = TaskState(task=_appworld_task())

    assert state.action_attempts == 0
    assert state.next_call_id == "call-1"
    with pytest.raises(ValueError, match="Expected call-1, got call-2"):
        state.record_attempt(_attempt("call-2"))
    assert state.attempts == []

    first = _attempt("call-1")
    state.record_attempt(first)
    assert state.action_attempts == 1
    assert state.next_call_id == "call-2"

    with pytest.raises(ValueError, match="Expected call-2, got call-1"):
        state.record_attempt(_attempt("call-1"))
    assert state.attempts == [first]

    second = _attempt("call-2")
    state.record_attempt(second)
    assert state.attempts == [first, second]
    assert state.action_attempts == 2
    assert state.next_call_id == "call-3"


def test_documented_model_and_deadline_defaults():
    config = load_config([])
    names = (
        "model",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "presence_penalty",
        "repetition_penalty",
        "max_output_tokens",
        "episode_timeout_seconds",
        "action_budget",
        "model_timeout_seconds",
        "final_response_reserve_seconds",
        "decision_reserve_seconds",
    )

    assert {name: getattr(config, name) for name in names} == {
        "model": "openai:qwen/qwen3.5-9b",
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "max_output_tokens": 4096,
        "episode_timeout_seconds": 285.0,
        "action_budget": 50,
        "model_timeout_seconds": 120.0,
        "final_response_reserve_seconds": 25.0,
        "decision_reserve_seconds": 0.0,
    }


def test_thinking_defaults_on_and_supports_environment_and_cli_overrides(monkeypatch):
    assert load_config([]).thinking is True
    monkeypatch.setenv(ENV_PREFIX + "THINKING", "false")
    assert load_config([]).thinking is False
    assert load_config(["--thinking"]).thinking is True
    monkeypatch.setenv(ENV_PREFIX + "THINKING", "true")
    assert load_config(["--no-thinking"]).thinking is False
    assert load_config([]).bootstrap_discovery is False
    assert load_config(["--bootstrap-discovery"]).bootstrap_discovery is True
    assert load_config([]).automatic_completion is False
    assert load_config(["--automatic-completion"]).automatic_completion is True
    assert load_config(["--bootstrap-authentication"]).bootstrap_authentication is True
    assert load_config(["--adaptive-bootstrap"]).adaptive_bootstrap is True


def test_cli_overrides_environment_and_preserves_unspecified_environment_values(monkeypatch):
    monkeypatch.setenv(ENV_PREFIX + "MODEL", "env-model")
    monkeypatch.setenv(ENV_PREFIX + "OUTPUT_MODE", "text")
    monkeypatch.setenv(ENV_PREFIX + "TEMPERATURE", "0.2")
    monkeypatch.setenv(ENV_PREFIX + "TOP_P", "0.8")
    monkeypatch.setenv(ENV_PREFIX + "TOP_K", "12")
    monkeypatch.setenv(ENV_PREFIX + "ACTION_BUDGET", "7")
    monkeypatch.setenv(ENV_PREFIX + "LOG_LEVEL", "ERROR")

    config = load_config(
        [
            "--model",
            "cli-model",
            "--temperature",
            "0.7",
            "--top-k",
            "21",
            "--action-budget",
            "9",
            "--log-level",
            "DEBUG",
        ]
    )

    assert config.model == "cli-model"
    assert config.temperature == 0.7
    assert config.top_k == 21
    assert config.action_budget == 9
    assert config.log_level == "DEBUG"
    assert config.output_mode == "text"
    assert config.top_p == 0.8


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("EPISODE_TIMEOUT_SECONDS", "100.5"),
        ("MODEL_TIMEOUT_SECONDS", "30.25"),
        ("FINAL_RESPONSE_RESERVE_SECONDS", "2.5"),
    ],
)
def test_environment_accepts_fractional_timeout_overrides(monkeypatch, name, raw):
    monkeypatch.setenv(ENV_PREFIX + name, raw)

    config = load_config([])

    field = name.lower()
    assert getattr(config, field) == float(raw)


@pytest.mark.parametrize(
    ("name", "raw"),
    [("TOP_K", "not-an-integer"), ("TEMPERATURE", "not-a-float")],
)
def test_malformed_environment_numbers_are_rejected(monkeypatch, name, raw):
    monkeypatch.setenv(ENV_PREFIX + name, raw)

    with pytest.raises(ValueError):
        load_config([])


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("MODEL", ""),
        ("OUTPUT_MODE", "json"),
        ("LOG_LEVEL", "TRACE"),
        ("ACTION_BUDGET", "0"),
        ("TOP_P", "0"),
        ("TEMPERATURE", "nan"),
        ("EPISODE_TIMEOUT_SECONDS", "inf"),
        ("MODEL_RETRIES", "11"),
    ],
)
def test_invalid_environment_values_are_rejected(monkeypatch, name, raw):
    monkeypatch.setenv(ENV_PREFIX + name, raw)

    with pytest.raises(ValueError):
        load_config([])


@pytest.mark.parametrize(
    "argv",
    [
        ["--top-k", "not-an-integer"],
        ["--temperature", "not-a-float"],
        ["--output-mode", "json"],
        ["--log-level", "TRACE"],
    ],
)
def test_malformed_cli_values_are_rejected_by_parser(argv):
    with pytest.raises(SystemExit) as exc_info:
        load_config(argv)

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--action-budget", "0"],
        ["--top-p", "0"],
        ["--temperature", "nan"],
        ["--model-timeout-seconds", "inf"],
    ],
)
def test_semantically_invalid_cli_values_fail_validation(argv):
    with pytest.raises(ValidationError):
        load_config(argv)


def test_no_seed_can_be_configured(monkeypatch):
    monkeypatch.setenv(ENV_PREFIX + "SEED", "123")

    config = load_config([])

    assert not any("seed" in name.lower() for name in AgentConfig.model_fields)
    assert "seed" not in config.public_dict()
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"seed": 123})
    with pytest.raises(SystemExit):
        load_config(["--seed", "123"])


def test_public_config_is_json_safe_and_excludes_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-credential")
    monkeypatch.setenv(ENV_PREFIX + "OPENAI_API_KEY", "super-secret-credential")

    config = load_config(["--model", "openai:public-model"])
    public = config.public_dict()
    serialized = json.dumps(public, allow_nan=False)

    assert set(public) == set(AgentConfig.model_fields)
    assert public == config.model_dump(mode="json")
    assert public["model"] == "openai:public-model"
    assert "super-secret-credential" not in serialized
    assert not {"api_key", "openai_api_key", "credentials"} & set(public)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_budget", 0),
        ("episode_timeout_seconds", 0),
        ("model_timeout_seconds", 0),
        ("final_response_reserve_seconds", -1),
        ("max_output_tokens", 0),
        ("model_retries", 11),
        ("output_retries", -1),
        ("max_observation_chars", 99),
        ("max_history_chars", 999),
    ],
)
def test_invalid_individual_budgets_are_rejected(field, value):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({field: value})


def test_combined_decision_and_response_reserves_leave_usable_time():
    with pytest.raises(ValidationError, match="must leave usable episode time"):
        AgentConfig(
            episode_timeout_seconds=100,
            final_response_reserve_seconds=20,
            decision_reserve_seconds=80,
        )


@pytest.mark.parametrize("reserve", [100, 100.01])
def test_final_response_reserve_must_be_below_episode_timeout(reserve):
    with pytest.raises(ValidationError, match="final_response_reserve_seconds must be below"):
        AgentConfig(episode_timeout_seconds=100, final_response_reserve_seconds=reserve)


def test_observation_budget_cannot_exceed_history_budget():
    with pytest.raises(ValidationError, match="max_observation_chars cannot exceed"):
        AgentConfig(max_observation_chars=1001, max_history_chars=1000)


def test_budget_boundaries_are_valid():
    config = AgentConfig(
        episode_timeout_seconds=100,
        final_response_reserve_seconds=0,
        action_budget=1,
        max_observation_chars=1000,
        max_history_chars=1000,
        model_retries=0,
        output_retries=10,
    )

    assert config.action_budget == 1
    assert config.final_response_reserve_seconds == 0
    assert config.max_observation_chars == config.max_history_chars == 1000
