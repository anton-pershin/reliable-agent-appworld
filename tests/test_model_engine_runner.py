from __future__ import annotations

import io
import json

from pydantic_ai.exceptions import ModelRequestRejectedError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from reliable_agent_appworld.config import AgentConfig
from reliable_agent_appworld.contracts import (
    ActionAttempt,
    ActionDecision,
    ActionRequest,
    ActionResult,
    FinalDecision,
    Task,
    TaskState,
    ToolDescription,
)
from reliable_agent_appworld.engine import DecisionEngine
from reliable_agent_appworld.model import PydanticAIModelClient
from reliable_agent_appworld.observability import EventLogger
from reliable_agent_appworld.runner import TaskRunner


def config(**overrides):
    values = {"model_retries": 0, "output_retries": 1, "log_level": "DEBUG"}
    values.update(overrides)
    return AgentConfig(**values)


def task():
    return Task(
        id="task-1",
        instructions="Complete with execute_code and then respond.",
        input=json.dumps({"instruction": "Print 42"}),
        tools=[
            ToolDescription(
                name="execute_code",
                description="Run Python",
                arguments_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string", "minLength": 1}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
            )
        ],
    )


def action(code="print(42)"):
    return ActionDecision(action=ActionRequest(name="execute_code", arguments={"code": code}))


def final(completed=True, likely_incorrect=False):
    return FinalDecision(
        completed=completed,
        answer="42" if completed else "stopped",
        confidence=0.9 if completed else 0.0,
        likely_incorrect=likely_incorrect,
        reason="Observed output" if completed else "Cannot complete",
    )


def logger(cfg):
    stream = io.StringIO()
    return EventLogger("episode-test", cfg, stream), stream


class QueueModel:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.remaining = []

    async def decide(self, state, remaining_seconds):
        self.remaining.append(remaining_seconds)
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision


class ScriptedExecutor:
    def __init__(self, results=None, error=None):
        self.results = results or [ActionResult(status="ok", observation={"output": "42\n"})]
        self.error = error
        self.calls = []

    async def execute(self, call_id, actions, timeout):
        self.calls.append((call_id, actions, timeout))
        if self.error:
            raise self.error
        return self.results


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_internal_decision_accepts_json_object_strings():
    decision = ActionDecision.model_validate(
        {
            "kind": "action",
            "action": json.dumps(
                {
                    "name": "execute_code",
                    "arguments": json.dumps({"code": "print(42)"}),
                }
            ),
        }
    )
    assert decision.action.name == "execute_code"
    assert decision.action.arguments == {"code": "print(42)"}


async def test_structured_action_and_sampling_without_seed():
    cfg = config()
    event_logger, stream = logger(cfg)
    seen = {}

    def respond(messages, info):
        seen.update(info.model_settings)
        tool = next(tool for tool in info.output_tools if "ActionDecision" in tool.name)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args=action().model_dump())],
            model_name="test-model",
            model_id="test:test-model",
            provider_name="test",
        )

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(respond))
    decision = await client.decide(TaskState(task=task()), 30)
    assert isinstance(decision, ActionDecision)
    assert seen["temperature"] == 0.6
    assert seen["top_p"] == 0.95
    assert seen["top_k"] == 20
    assert seen["extra_body"]["min_p"] == 0
    assert seen["extra_body"]["repetition_penalty"] == 1.0
    assert seen.get("seed") is None
    response = next(event for event in events(stream) if event["event"] == "model_response")
    assert response["reported_model"] == "test:test-model"
    assert response["provider"] == "test"
    assert response["usage"]["requests"] == 1
    assert response["private_reasoning"] == "not-retained"


async def test_tool_rejection_falls_back_to_text_json():
    cfg = config()
    event_logger, stream = logger(cfg)
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelRequestRejectedError("test", "tool output unsupported")
        assert info.model_settings["tool_choice"] == "none"
        return ModelResponse(parts=[TextPart(content=json.dumps(final().model_dump()))])

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(respond))
    result = await client.decide(TaskState(task=task()), 30)
    assert isinstance(result, FinalDecision)
    assert calls == 2
    assert any(event["event"] == "text_fallback" for event in events(stream))


async def test_text_output_repairs_invalid_json():
    cfg = config(output_mode="text")
    event_logger, stream = logger(cfg)
    responses = iter(["not-json", "```json\n" + json.dumps(action().model_dump()) + "\n```"])

    def respond(messages, info):
        return ModelResponse(parts=[TextPart(content=next(responses))])

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(respond))
    result = await client.decide(TaskState(task=task()), 30)
    assert isinstance(result, ActionDecision)
    assert any(event["event"] == "model_repair" for event in events(stream))


async def test_history_trims_old_observations_then_whole_entries():
    cfg = config(max_history_chars=1000, max_observation_chars=200)
    event_logger, stream = logger(cfg)
    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(lambda *_: None))
    state = TaskState(task=task())
    for index in range(1, 7):
        state.attempts.append(
            ActionAttempt(
                call_id=f"call-{index}",
                action=action().action,
                results=[ActionResult(status="ok", observation={"output": "x" * 2000})],
            )
        )
    history = client.public_history(state)
    assert len(json.dumps(history)) < 1500
    assert state.history_trimmed
    assert history[0] == "<older history trimmed for context budget>"
    assert any(event["event"] == "context_trimmed" for event in events(stream))


async def test_engine_repairs_invalid_tool_arguments():
    cfg = config()
    event_logger, _ = logger(cfg)
    invalid = action()
    invalid.action.arguments["extra"] = True
    model = QueueModel(invalid, action())
    engine = DecisionEngine(cfg, model, event_logger)
    state = TaskState(task=task())
    result = await engine.next_decision(state, 30)
    assert isinstance(result, ActionDecision)
    assert state.validation_feedback


async def test_engine_rejects_premature_completion_and_normalizes_failure_flag():
    cfg = config()
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(final(), action()), event_logger)
    result = await engine.next_decision(TaskState(task=task()), 30)
    assert isinstance(result, ActionDecision)

    engine = DecisionEngine(
        cfg, QueueModel(final(False, False), final(False, False)), event_logger
    )
    state = TaskState(task=task())
    state.attempts.append(
        ActionAttempt(call_id="call-1", action=action().action, results=[])
    )
    result = await engine.next_decision(state, 30)
    assert isinstance(result, FinalDecision)
    assert result.likely_incorrect is True


async def test_engine_repels_give_up_after_recoverable_attempts():
    cfg = config()
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(final(False, True), action()), event_logger)
    state = TaskState(task=task())
    state.attempts.append(ActionAttempt(call_id="call-1", action=action().action, results=[]))

    result = await engine.next_decision(state, 30)

    assert isinstance(result, ActionDecision)
    assert any("Recoverable" in feedback for feedback in state.validation_feedback)


async def test_runner_executes_one_action_then_final():
    cfg = config()
    event_logger, _ = logger(cfg)
    model = QueueModel(action(), final())
    engine = DecisionEngine(cfg, model, event_logger)
    executor = ScriptedExecutor()
    runner = TaskRunner(cfg, engine, executor, event_logger)
    result = await runner.run(task())
    assert result.completed is True
    assert runner.last_state.action_attempts == 1
    assert executor.calls[0][0] == "call-1"
    assert len(executor.calls[0][1]) == 1
    assert model.remaining[0] <= cfg.episode_timeout_seconds - cfg.final_response_reserve_seconds


async def test_runner_records_uncertain_execution_and_stops():
    cfg = config()
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(action()), event_logger)
    executor = ScriptedExecutor(error=TimeoutError("late observation"))
    runner = TaskRunner(cfg, engine, executor, event_logger)
    result = await runner.run(task())
    assert result.completed is False
    assert result.likely_incorrect is True
    assert "uncertain" in result.reason
    attempt = runner.last_state.attempts[0]
    assert attempt.results[0].error.type == "execution_outcome_unknown"


async def test_runner_action_budget_produces_controlled_final():
    cfg = config(action_budget=1)
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(action(), action()), event_logger)
    runner = TaskRunner(cfg, engine, ScriptedExecutor(), event_logger)
    result = await runner.run(task())
    assert result.completed is False
    assert "budget" in result.reason
    assert runner.last_state.action_attempts == 1
