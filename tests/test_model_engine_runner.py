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
    ActionError,
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
    assert seen["max_retries"] == 0
    assert seen.get("seed") is None
    response = next(event for event in events(stream) if event["event"] == "model_response")
    assert response["reported_model"] == "test:test-model"
    assert response["provider"] == "test"
    assert response["usage"]["requests"] == 1
    assert response["private_reasoning"] == "not-retained"


async def test_thinking_can_be_disabled_and_prompt_reports_remaining_time():
    cfg = config(thinking=False)
    event_logger, _ = logger(cfg)
    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(lambda *_: None))

    settings = client._settings()
    prompt = json.loads(client.prompt(TaskState(task=task()), 12.34))

    assert settings["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "Raw requester and HTTP access are forbidden" in client.instructions
    assert "Never print passwords" in client.instructions
    assert client._public_settings()["thinking"] is False
    assert prompt["remaining_episode_seconds"] == 12.3
    assert prompt["task"]["objective"] == "Print 42"
    assert "instruction" not in prompt["task"]["input"]
    assert prompt["prompt_version"] == "react-v2.4-verified"


async def test_pydantic_schema_validation_repair_is_retried_and_logged():
    cfg = config(output_retries=1)
    event_logger, stream = logger(cfg)
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        tool = next(tool for tool in info.output_tools if "ActionDecision" in tool.name)
        args = action().model_dump() if calls == 2 else {"kind": "action", "action": "not-json"}
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(respond))
    result = await client.decide(TaskState(task=task()), 30)

    assert isinstance(result, ActionDecision)
    assert calls == 2
    assert any(event["event"] == "model_repair" for event in events(stream))


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


def test_attempt_truncation_preserves_structure_error_and_unicode_traceback_tail():
    cfg = config(max_observation_chars=240)
    event_logger, _ = logger(cfg)
    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(lambda *_: None))
    attempt = ActionAttempt(
        call_id="call-1",
        action=action().action,
        results=[
            ActionResult(
                status="error",
                observation={"output": "🙂 start " + "x" * 3000 + "\nValueError: tail-clue"},
                error=ActionError(type="execution_failed", message="recovery-clue"),
            )
        ],
    )

    payload = client._attempt_payload(attempt, 240)
    result = payload["results"][0]
    compact = result["observation"]["truncated_json"]

    assert result["status"] == "error"
    assert result["error"]["message"] == "recovery-clue"
    assert compact.startswith('{"output": "🙂 start')
    assert compact.endswith("ValueError: tail-clue\"}")
    assert "middle omitted" in compact


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


async def test_completion_check_defers_and_releases_final_without_extra_model_call():
    cfg = config(output_retries=1)
    event_logger, _ = logger(cfg)
    model = QueueModel(final(), action("print(apis.supervisor.complete_task(answer='42'))"))
    engine = DecisionEngine(
        cfg,
        model,
        event_logger,
        completion_check=lambda state: any(
            "Marked the active task complete." in str(result.observation.get("output", ""))
            for attempt in state.attempts
            for result in attempt.results
        ),
    )
    state = TaskState(task=task())

    first = await engine.next_decision(state, 30)
    assert isinstance(first, ActionDecision)
    assert state.pending_final is not None

    engine.record_attempt(
        state,
        ActionAttempt(
            call_id="call-1",
            action=first.action,
            results=[
                ActionResult(
                    status="ok",
                    observation={"output": "Marked the active task complete."},
                )
            ],
        ),
    )
    assert state.completion_observed is True
    released = await engine.next_decision(state, 30)
    assert isinstance(released, FinalDecision)
    assert released.completed is True
    assert state.pending_final is None
    assert not model.decisions


async def test_optional_bootstrap_action_precedes_model_without_consuming_it():
    cfg = config()
    event_logger, _ = logger(cfg)
    model = QueueModel(final())
    engine = DecisionEngine(
        cfg,
        model,
        event_logger,
        bootstrap_action=lambda state: action("print('public docs')"),
    )
    state = TaskState(task=task())

    first = await engine.next_decision(state, 30)
    assert isinstance(first, ActionDecision)
    assert first.action.arguments["code"] == "print('public docs')"
    assert len(model.decisions) == 1

    state.record_attempt(
        ActionAttempt(
            call_id="call-1",
            action=first.action,
            results=[ActionResult(status="ok", observation={"output": "public docs"})],
        )
    )
    second = await engine.next_decision(state, 30)
    assert isinstance(second, FinalDecision)
    assert not model.decisions


async def test_automatic_completion_action_skips_ignored_reprompt_and_releases_final():
    cfg = config()
    event_logger, _ = logger(cfg)
    model = QueueModel(final())
    engine = DecisionEngine(
        cfg,
        model,
        event_logger,
        completion_check=lambda state: any(
            "Marked the active task complete." in str(result.observation.get("output", ""))
            for attempt in state.attempts
            for result in attempt.results
        ),
        completion_action=lambda state, decision: action(
            "print(apis.supervisor.complete_task(answer='42'))"
        ),
    )
    state = TaskState(task=task())
    state.record_attempt(
        ActionAttempt(
            call_id="call-1",
            action=action().action,
            results=[ActionResult(status="ok", observation={"output": "public work"})],
        )
    )

    completion = await engine.next_decision(state, 30)
    assert isinstance(completion, ActionDecision)
    assert "complete_task" in completion.action.arguments["code"]
    assert state.pending_final is not None
    assert not model.decisions

    engine.record_attempt(
        state,
        ActionAttempt(
            call_id="call-2",
            action=completion.action,
            results=[
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ],
        ),
    )
    released = await engine.next_decision(state, 30)
    assert isinstance(released, FinalDecision)
    assert released.completed is True


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


async def test_runner_action_budget_requests_final_without_another_execution():
    cfg = config(action_budget=1)
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(action(), final(False, True)), event_logger)
    executor = ScriptedExecutor()
    runner = TaskRunner(cfg, engine, executor, event_logger)
    result = await runner.run(task())
    assert result.completed is False
    assert result.reason == "Cannot complete"
    assert runner.last_state.action_attempts == 1
    assert len(executor.calls) == 1


async def test_decision_reserve_switches_to_final_only_mode():
    now = [0.0]

    class AdvancingExecutor:
        def __init__(self):
            self.calls = 0

        async def execute(self, call_id, actions, timeout):
            self.calls += 1
            now[0] += 0.06
            return [ActionResult(status="ok", observation={"output": "work"})]

    cfg = config(
        episode_timeout_seconds=0.2,
        final_response_reserve_seconds=0.05,
        decision_reserve_seconds=0.1,
    )
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(action(), final(False, True)), event_logger)
    executor = AdvancingExecutor()
    runner = TaskRunner(cfg, engine, executor, event_logger, clock=lambda: now[0])

    result = await runner.run(task())

    assert result.completed is False
    assert result.reason == "Cannot complete"
    assert executor.calls == 1
    assert any("Finalization is now required" in item for item in runner.last_state.validation_feedback)


async def test_runner_releases_deferred_final_after_last_allowed_action():
    cfg = config(action_budget=1, output_retries=1)
    event_logger, _ = logger(cfg)
    completion_action = action("print(apis.supervisor.complete_task(answer='42'))")
    engine = DecisionEngine(
        cfg,
        QueueModel(final(), completion_action),
        event_logger,
        completion_check=lambda state: any(
            "Marked the active task complete." in str(result.observation.get("output", ""))
            for attempt in state.attempts
            for result in attempt.results
        ),
    )
    executor = ScriptedExecutor(
        [ActionResult(status="ok", observation={"output": "Marked the active task complete."})]
    )
    runner = TaskRunner(cfg, engine, executor, event_logger)

    result = await runner.run(task())

    assert result.completed is True
    assert runner.last_state.action_attempts == 1
    assert len(executor.calls) == 1
