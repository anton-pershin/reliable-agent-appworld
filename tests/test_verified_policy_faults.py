from __future__ import annotations

import json

import pytest
from pydantic_ai.exceptions import RunTimedOut
from pydantic_ai.models.function import FunctionModel
from test_model_engine_runner import QueueModel, action, config, final, logger, task

from reliable_agent_appworld.contracts import (
    ActionAttempt,
    ActionDecision,
    ActionRequest,
    ActionResult,
    FinalDecision,
    TaskState,
)
from reliable_agent_appworld.engine import DecisionEngine
from reliable_agent_appworld.errors import ModelRequestError
from reliable_agent_appworld.model import PydanticAIModelClient
from reliable_agent_appworld.protocol import (
    appworld_completion_call_id,
    appworld_completion_observed,
    appworld_validate_action,
)
from reliable_agent_appworld.runner import TaskRunner


def work_state() -> TaskState:
    state = TaskState(task=task())
    state.record_attempt(
        ActionAttempt(
            call_id="call-1",
            action=action().action,
            results=[ActionResult(status="ok", observation={"output": "work"})],
        )
    )
    return state


def completion_engine(model, cfg):
    return DecisionEngine(
        cfg,
        model,
        logger(cfg)[0],
        completion_check=appworld_completion_observed,
        completion_call_id=appworld_completion_call_id,
        completion_action=lambda state, decision: action(
            f"print(apis.supervisor.complete_task(answer={json.dumps(decision.answer)}))"
        ),
    )


def completion_attempt(call_id: str, answer: str, *, success: bool) -> ActionAttempt:
    return ActionAttempt(
        call_id=call_id,
        action=ActionRequest(
            name="execute_code",
            arguments={"code": f"print(apis.supervisor.complete_task(answer={json.dumps(answer)}))"},
        ),
        results=[
            ActionResult(
                status="ok" if success else "error",
                observation={"output": "Marked the active task complete." if success else "failed"},
            )
        ],
    )


async def test_generic_feedback_has_no_appworld_syntax():
    cfg = config(output_retries=1)
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, QueueModel(final(), final()), event_logger)
    state = TaskState(task=task())

    result = await engine.next_decision(state, 30)

    assert result.completed is False
    assert state.validation_feedback
    assert all("AppWorld" not in item and "apis." not in item for item in state.validation_feedback)


async def test_automatic_completion_requires_prior_public_work():
    cfg = config(output_retries=1)
    event_logger, _ = logger(cfg)
    calls = []

    def completion(state, decision):
        calls.append(decision)
        return action("print(apis.supervisor.complete_task())")

    engine = DecisionEngine(
        cfg,
        QueueModel(final(), final()),
        event_logger,
        completion_check=appworld_completion_observed,
        completion_call_id=appworld_completion_call_id,
        completion_action=completion,
    )

    result = await engine.next_decision(TaskState(task=task()), 30)

    assert result.completed is False
    assert not calls


async def test_failed_completion_invalidates_deferred_final():
    cfg = config()
    engine = completion_engine(QueueModel(final()), cfg)
    state = work_state()

    completion = await engine.next_decision(state, 30)
    assert isinstance(completion, ActionDecision)
    engine.record_attempt(state, completion_attempt("call-2", "A", success=False))

    assert state.pending_final is None
    assert state.pending_completion_call_id is None
    assert engine.release_deferred_final(state) is None


async def test_failed_a_then_successful_b_never_releases_stale_a():
    cfg = config()
    manual_b = action("print(apis.supervisor.complete_task(answer='B'))")
    engine = completion_engine(QueueModel(final(), manual_b), cfg)
    state = work_state()

    automatic_a = await engine.next_decision(state, 30)
    assert isinstance(automatic_a, ActionDecision)
    engine.record_attempt(state, completion_attempt("call-2", "A", success=False))

    selected_b = await engine.next_decision(state, 30)
    assert isinstance(selected_b, ActionDecision)
    engine.record_attempt(state, completion_attempt("call-3", "B", success=True))

    assert state.completion_call_id == "call-3"
    assert engine.release_deferred_final(state) is None


async def test_final_only_rejects_ordinary_action_and_permits_automatic_completion():
    cfg = config(output_retries=1)
    event_logger, _ = logger(cfg)
    ordinary = DecisionEngine(
        cfg, QueueModel(action(), final(False, True)), event_logger
    )
    state = work_state()

    result = await ordinary.next_decision(state, 30, allow_actions=False)
    assert isinstance(result, FinalDecision)
    assert result.completed is False

    automatic = completion_engine(QueueModel(final()), cfg)
    completion = await automatic.next_decision(work_state(), 30, allow_actions=False)
    assert isinstance(completion, ActionDecision)
    assert "complete_task" in completion.action.arguments["code"]


async def test_bootstrap_is_gated_by_action_permission():
    cfg = config()
    event_logger, _ = logger(cfg)
    calls = []

    def bootstrap(state):
        calls.append(state)
        return action()

    engine = DecisionEngine(
        cfg,
        QueueModel(final(False, True)),
        event_logger,
        bootstrap_action=bootstrap,
    )
    result = await engine.next_decision(
        TaskState(task=task()), 30, allow_actions=False
    )
    assert isinstance(result, FinalDecision)
    assert not calls


async def test_uncertain_completion_invalidates_pending_final_in_runner():
    cfg = config()
    event_logger, _ = logger(cfg)
    model = QueueModel(action(), final())
    engine = completion_engine(model, cfg)

    class Executor:
        async def execute(self, call_id, actions, timeout):
            if call_id == "call-2":
                raise TimeoutError("uncertain completion")
            return [ActionResult(status="ok", observation={"output": "work"})]

    runner = TaskRunner(cfg, engine, Executor(), event_logger)
    result = await runner.run(task())

    assert result.completed is False
    assert runner.last_state.pending_final is None
    assert runner.last_state.pending_completion_call_id is None


async def test_generation_crossing_reserve_never_executes_ordinary_action():
    now = [0.0]
    cfg = config(
        episode_timeout_seconds=10,
        final_response_reserve_seconds=1,
        decision_reserve_seconds=3,
    )

    class CrossingModel:
        def __init__(self):
            self.calls = 0

        async def decide(self, state, remaining_seconds):
            self.calls += 1
            if self.calls == 1:
                now[0] += 7
                return action()
            return final(False, True)

    event_logger, _ = logger(cfg)
    engine = DecisionEngine(cfg, CrossingModel(), event_logger)

    class Executor:
        calls = 0

        async def execute(self, call_id, actions, timeout):
            self.calls += 1
            return []

    executor = Executor()
    runner = TaskRunner(cfg, engine, executor, event_logger, clock=lambda: now[0])
    result = await runner.run(task())

    assert result.completed is False
    assert executor.calls == 0


async def test_automatic_completion_can_execute_during_final_only_phase():
    now = [0.0]
    cfg = config(
        episode_timeout_seconds=10,
        final_response_reserve_seconds=1,
        decision_reserve_seconds=3,
    )
    engine = completion_engine(QueueModel(action(), final()), cfg)

    class Executor:
        calls = 0

        async def execute(self, call_id, actions, timeout):
            self.calls += 1
            if self.calls == 1:
                now[0] += 7
                return [ActionResult(status="ok", observation={"output": "work"})]
            return [
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ]

    event_logger, _ = logger(cfg)
    runner = TaskRunner(cfg, engine, Executor(), event_logger, clock=lambda: now[0])
    result = await runner.run(task())

    assert result.completed is True
    assert runner.last_state.action_attempts == 2


async def test_completion_milestone_survives_history_trimming():
    cfg = config(max_history_chars=1000, max_observation_chars=200)
    event_logger, _ = logger(cfg)
    engine = DecisionEngine(
        cfg,
        QueueModel(),
        event_logger,
        completion_check=appworld_completion_observed,
        completion_call_id=appworld_completion_call_id,
    )
    state = work_state()
    engine.record_attempt(state, completion_attempt("call-2", "done", success=True))
    for index in range(3, 6):
        engine.record_attempt(
            state,
            ActionAttempt(
                call_id=f"call-{index}",
                action=action().action,
                results=[ActionResult(status="ok", observation={"output": "x" * 2000})],
            ),
        )

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(lambda *_: None))
    history = client.public_history(state)
    prompt = json.loads(client.prompt(state, 30))

    assert state.history_trimmed
    assert history[0] == "<older history trimmed for context budget>"
    assert prompt["completion_observed"] is True


async def test_model_timeout_is_not_retried_identically_by_default():
    cfg = config(model_retries=0)
    event_logger, _ = logger(cfg)
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        raise RunTimedOut()

    client = PydanticAIModelClient(cfg, event_logger, FunctionModel(respond))
    with pytest.raises(ModelRequestError):
        await client.decide(TaskState(task=task()), 30)
    assert calls == 1


def test_docstring_and_cross_attempt_markers_are_not_completion():
    docstring_state = TaskState(task=task())
    docstring_state.record_attempt(
        ActionAttempt(
            call_id="call-1",
            action=ActionRequest(
                name="execute_code",
                arguments={"code": 'print("""apis.supervisor.complete_task()""")'},
            ),
            results=[
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ],
        )
    )
    assert not appworld_completion_observed(docstring_state)

    cross = TaskState(task=task())
    cross.record_attempt(completion_attempt("call-1", "A", success=False))
    cross.record_attempt(
        ActionAttempt(
            call_id="call-2",
            action=action().action,
            results=[
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ],
        )
    )
    assert not appworld_completion_observed(cross)


@pytest.mark.parametrize(
    "code",
    [
        "print(access_token)",
        "print(login_response)",
        "print(apis.supervisor.show_account_passwords())",
        "apis.requester.post('/auth/token')",
        "import apis\nprint(apis.spotify.show_profile())",
    ],
)
def test_action_policy_rejects_raw_requester_and_credential_printing(code):
    with pytest.raises(ValueError):
        appworld_validate_action(TaskState(task=task()), action(code))


def test_action_policy_allows_fixed_status_and_domain_output():
    state = TaskState(task=task())
    appworld_validate_action(state, action("print('Token saved for Spotify')"))
    appworld_validate_action(state, action("print(apis.spotify.show_profile())"))
