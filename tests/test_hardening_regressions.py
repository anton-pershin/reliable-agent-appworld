from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time

import pytest
from test_model_engine_runner import QueueModel
from test_subprocess_episode import FakeProvider, public_task, readline

import reliable_agent_appworld.__main__ as main_module
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
    controlled_failure,
)
from reliable_agent_appworld.engine import DecisionEngine
from reliable_agent_appworld.model import PydanticAIModelClient
from reliable_agent_appworld.observability import BoundedRedactingStream, EventLogger
from reliable_agent_appworld.protocol import (
    JsonLinesTransport,
    ProtocolError,
    appworld_completion_action,
    appworld_completion_observed,
    appworld_discovery_action,
    decode_appworld_public_input,
)
from reliable_agent_appworld.runner import TaskRunner


def task():
    return Task(
        id="hardening",
        instructions="Use execute_code",
        input="{}",
        tools=[
            ToolDescription(
                name="execute_code",
                arguments_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
            )
        ],
    )


def action():
    return ActionDecision(
        action=ActionRequest(name="execute_code", arguments={"code": "print('ok')"})
    )


def final(completed=True):
    return FinalDecision(
        completed=completed,
        answer="done" if completed else "stopped",
        confidence=0.9 if completed else 0,
        likely_incorrect=not completed,
        reason="public evidence" if completed else "incomplete",
    )


def logger(config=None):
    stream = io.StringIO()
    config = config or AgentConfig(model_retries=0, log_level="DEBUG")
    return EventLogger("hardening", config, stream), stream


@pytest.mark.parametrize("retries", [0, 2])
async def test_persistent_no_evidence_success_becomes_controlled_failure(retries):
    config = AgentConfig(model_retries=0, output_retries=retries)
    event_logger, _ = logger(config)
    model = QueueModel(*([final(True)] * (retries + 1)))
    engine = DecisionEngine(config, model, event_logger)

    result = await engine.next_decision(TaskState(task=task()), 1)

    assert result.completed is False
    assert result.likely_incorrect is True
    assert "without public task evidence" in result.reason


def test_appworld_completion_check_requires_actual_successful_call_not_docs_or_fail():
    state = TaskState(task=task())
    state.record_attempt(
        ActionAttempt(
            call_id="call-1",
            action=ActionRequest(
                name="execute_code",
                arguments={
                    "code": "# apis.supervisor.complete_task() in a comment is not execution\nprint(apis.api_docs.show_api_doc(app_name='supervisor', api_name='complete_task'))"
                },
            ),
            results=[
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ],
        )
    )
    assert not appworld_completion_observed(state)

    state.record_attempt(
        ActionAttempt(
            call_id="call-2",
            action=ActionRequest(
                name="execute_code",
                arguments={"code": "apis.supervisor.complete_task(status='fail')"},
            ),
            results=[
                ActionResult(
                    status="ok", observation={"output": "Marked the active task complete."}
                )
            ],
        )
    )
    assert not appworld_completion_observed(state)

    state.record_attempt(
        ActionAttempt(
            call_id="call-3",
            action=ActionRequest(
                name="execute_code",
                arguments={"code": "print(apis.supervisor.complete_task(answer='42'))"},
            ),
            results=[
                ActionResult(
                    status="ok",
                    observation={"output": '{"message": "Marked the active task complete."}'},
                )
            ],
        )
    )
    assert appworld_completion_observed(state)


def test_appworld_discovery_action_uses_only_public_objective():
    public_task = task().model_copy(
        update={
            "input": json.dumps(
                {
                    "instruction": "Inspect Venmo records 🌐",
                    "supervisor": {"email": "public@example.invalid"},
                }
            )
        }
    )
    state = TaskState(task=public_task)

    decision = appworld_discovery_action(state)

    assert decision is not None
    code = decision.action.arguments["code"]
    compile(code, "<public-bootstrap>", "exec")
    assert "Inspect Venmo records 🌐" in code
    assert "search_api_docs(query=objective" in code
    assert '("show_profile", "show_account_passwords")' in code
    assert 'api_name="login"' in code
    assert "public@example.invalid" not in code
    assert "access_tokens = {}" not in code
    authenticated = appworld_discovery_action(state, authenticate=True)
    assert authenticated is not None
    authenticated_code = authenticated.action.arguments["code"]
    compile(authenticated_code, "<authenticated-bootstrap>", "exec")
    assert "access_tokens = {}" in authenticated_code
    assert 'app_name == "phone"' in authenticated_code
    assert "getattr(apis, app_name).login" in authenticated_code
    assert "apis.phone.search_contacts" in authenticated_code
    assert 'print("Access token' not in authenticated_code
    assert "public@example.invalid" not in authenticated_code
    assert appworld_discovery_action(TaskState(task=task())) is None

    for public_instruction in (
        "What is Velvet Echo's top song?",
        "Find Velvet Echo's top song.",
        "Show the title of Velvet Echo's top song.",
    ):
        public_lookup = TaskState(
            task=task().model_copy(
                update={"input": json.dumps({"instruction": public_instruction})}
            )
        )
        assert appworld_discovery_action(public_lookup, private_only=True) is None
    for private_objective in (
        "Keep going to the previous song until Luna Starlight plays.",
        "How many playlists do I have?",
        "Add these songs to my queue.",
        "Play the least listened song from this public album.",
        "Find and play Velvet Echo's top song.",
    ):
        private_state = TaskState(
            task=task().model_copy(
                update={"input": json.dumps({"instruction": private_objective})}
            )
        )
        assert appworld_discovery_action(private_state, private_only=True) is not None


@pytest.mark.parametrize(
    ("objective", "answer", "expected"),
    [
        ("What is the song title?", '"Swept Away"', 'answer="Swept Away"'),
        ("What is the song title?", '"1984"', 'answer="1984"'),
        ("Find Velvet Echo's top song.", "Bittersweet Memories Remain", 'answer="Bittersweet Memories Remain"'),
        ("Show the title of Velvet Echo's top song.", "Bittersweet Memories Remain", 'answer="Bittersweet Memories Remain"'),
        ("Find and play Velvet Echo's top song.", "Played it", "complete_task()"),
        ("Show the title and play the song.", "Played it", "complete_task()"),
        ("How many songs are there?", "23", "answer=23"),
        ("How many songs are there?", '"23"', "answer=23"),
        ("What is the total cost?", '"$23.50"', "answer=23.5"),
        ("Using my Spotify data, who is the most followed artist?", "Eliana Harper with 29 followers", 'answer="Eliana Harper"'),
        ("According to my plan. How long is the workout?", "In 2023 it was 60 minutes", "answer=60"),
        ("Give me a comma-separated list.", "one, two", 'answer="one, two"'),
        ("Name the most followed artist.", "Eliana Harper", 'answer="Eliana Harper"'),
        ("Give a 5-star rating to liked songs. If already rated lower, increase it to 5.", "Rated them", "complete_task()"),
        ("Who is the most followed artist?", "Eliana Harper with 29 followers", 'answer="Eliana Harper"'),
        ("Who is the most followed artist?", "Eliana Harper is the most followed artist I follow on Spotify, with 29 followers.", 'answer="Eliana Harper"'),
        ("Who is the most followed artist?", "The most followed artist is Eliana Harper.", 'answer="Eliana Harper"'),
        ("How long is the workout?", "12.5 minutes", "answer=12.5"),
        ("How long is the workout?", "In 2023, the workout was 60 minutes", "answer=60"),
        ("How much was sent?", "Since 2023, one payment of $121.00 was sent; total $121.00", "answer=121.0"),
        ("What is the total cost?", "$1,234.50", "answer=1234.5"),
        ("Play the selected song.", "The song is playing.", "complete_task()"),
    ],
)
def test_appworld_completion_action_preserves_direct_answer_semantics(
    objective, answer, expected
):
    state = TaskState(
        task=task().model_copy(update={"input": json.dumps({"instruction": objective})})
    )
    decision = final().model_copy(update={"answer": answer})

    completion = appworld_completion_action(state, decision)

    assert completion is not None
    assert expected in completion.action.arguments["code"]


def test_answer_seeking_empty_completion_answer_is_rejected():
    state = TaskState(
        task=task().model_copy(
            update={"input": json.dumps({"instruction": "How many songs are there?"})}
        )
    )
    decision = final().model_copy(update={"answer": "  "})
    assert appworld_completion_action(state, decision) is None


async def test_history_exact_budget_retains_latest_multibyte_attempt():
    config = AgentConfig(max_history_chars=1000, max_observation_chars=1000)
    event_logger, _ = logger(config)
    client = PydanticAIModelClient(config, event_logger, model=object())
    state = TaskState(task=task())
    state.attempts.append(
        ActionAttempt(
            call_id="call-1",
            action=action().action,
            results=[ActionResult(status="ok", observation={"output": "🙂 café" * 1000})],
        )
    )

    history = client.public_history(state)

    assert len(json.dumps(history, ensure_ascii=False)) <= 1000
    assert "execute_code" in json.dumps(history)
    assert state.history_trimmed


def test_secrets_are_redacted_from_serialized_prompts_and_code():
    event_logger, stream = logger()
    event_logger.emit(
        "observation",
        results=[{"observation": {"access_token": "appworld-session-secret"}}],
    )
    prompt = json.dumps(
        {
            "history": [{"access_token": "appworld-session-secret"}],
            "code": "apis.spotify.login(password='code-password-secret')",
            "max_output_tokens": 4096,
        }
    )

    event_logger.emit("model_request", prompt=prompt)

    raw = stream.getvalue()
    assert "appworld-session-secret" not in raw
    assert "code-password-secret" not in raw
    assert "max_output_tokens" in raw


def test_standalone_jwt_and_unquoted_token_are_redacted():
    event_logger, stream = logger()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"
    event_logger.emit(
        "public_output",
        text=f"access_token=standalone-session-secret and {jwt}",
    )

    raw = stream.getvalue()
    assert "standalone-session-secret" not in raw
    assert jwt not in raw
    assert "<redacted>" in raw


@pytest.mark.parametrize(
    "assignment",
    [
        "password='two words'",
        'password="two words"',
        "access_token='two words'",
        'access_token="two words"',
    ],
)
def test_multiword_quoted_secrets_are_fully_redacted_everywhere(assignment):
    event_logger, structured_stream = logger()
    event_logger.emit("multiword_secret", text=assignment)

    proxy_raw = io.StringIO()
    proxy = BoundedRedactingStream(proxy_raw, max_bytes=1000)
    proxy.write(assignment)

    for raw in (structured_stream.getvalue(), proxy_raw.getvalue()):
        assert "two" not in raw
        assert "words" not in raw
        assert "<redacted>" in raw


def test_bounded_stderr_proxy_caps_dependency_output_and_redacts():
    raw = io.StringIO()
    proxy = BoundedRedactingStream(raw, max_bytes=500)

    proxy.write("password='dependency-secret'\n")
    proxy.write("x" * 2000)
    proxy.write("must not appear")

    assert len(raw.getvalue().encode()) <= 500
    assert "dependency-secret" not in raw.getvalue()
    assert "stderr_limit_reached" in raw.getvalue()
    assert "must not appear" not in raw.getvalue()


def test_inner_appworld_json_rejects_nonfinite_numbers():
    with pytest.raises(ProtocolError):
        decode_appworld_public_input(Task(id="task", input='{"instruction": NaN}'))


async def test_cumulative_stdout_budget_reserves_final_space():
    transport = JsonLinesTransport(
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
        max_bytes=10_000,
        max_total_bytes=600,
        final_reserve_bytes=200,
    )
    with pytest.raises(ProtocolError, match="Cumulative"):
        for index in range(20):
            await transport.write_actions(
                f"call-{index}",
                [ActionRequest(name="execute_code", arguments={"code": "x" * 80})],
                timeout=1,
            )
    assert transport.bytes_written <= 400

    await transport.write_final(final(False), timeout=1)
    assert transport.bytes_written <= 600


class IgnoringExecutor:
    async def execute(self, call_id, actions, timeout):
        await asyncio.sleep(30)


async def test_runner_enforces_timeout_for_executor_that_ignores_it():
    config = AgentConfig(
        model_retries=0,
        episode_timeout_seconds=0.15,
        final_response_reserve_seconds=0.05,
        decision_reserve_seconds=0,
    )
    event_logger, _ = logger(config)
    engine = DecisionEngine(config, QueueModel(action()), event_logger)
    runner = TaskRunner(config, engine, IgnoringExecutor(), event_logger)

    started = time.monotonic()
    result = await runner.run(task())

    assert time.monotonic() - started < 1
    assert result.completed is False
    assert runner.last_state.attempts[0].results[0].error.type == "execution_outcome_unknown"


async def test_runner_cancellation_is_observable_and_records_unknown_attempt():
    config = AgentConfig(model_retries=0)
    event_logger, _ = logger(config)
    engine = DecisionEngine(config, QueueModel(action()), event_logger)
    runner = TaskRunner(config, engine, IgnoringExecutor(), event_logger)

    operation = asyncio.create_task(runner.run(task()))
    await asyncio.sleep(0.02)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert runner.last_state.attempts[0].results[0].error.type == "execution_outcome_unknown"


async def test_final_delivery_uses_remaining_shared_deadline(monkeypatch):
    timeouts = []

    class FakeTransport:
        bytes_written = 0

        def __init__(self, stdout):
            pass

        async def read_task(self):
            return "deadline-test", Task(id="task")

        async def write_final(self, decision, timeout=None):
            timeouts.append(timeout)

    class SlowModel:
        def __init__(self, *args):
            pass

        async def decide(self, state, remaining_seconds):
            await asyncio.sleep(10)

    monkeypatch.setattr(main_module, "JsonLinesTransport", FakeTransport)
    monkeypatch.setattr(main_module, "PydanticAIModelClient", SlowModel)
    monkeypatch.setattr(main_module.logging, "basicConfig", lambda **kwargs: None)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        await main_module.run_episode(
            AgentConfig(
                model_retries=0,
                episode_timeout_seconds=0.12,
                final_response_reserve_seconds=0.04,
                decision_reserve_seconds=0,
                bootstrap_discovery=False,
                automatic_completion=False,
            )
        )
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    assert timeouts and timeouts[0] <= 0.04


def test_controlled_failure_reason_is_bounded():
    assert len(controlled_failure("x" * 10000).reason) <= 1000


async def test_partial_failed_write_poisons_transport_and_blocks_final():
    transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=io.BytesIO())
    transport._stdout_fd = 1

    async def partial_write(message):
        transport.bytes_written += 5
        raise TimeoutError("stalled after a short write")

    transport._write = partial_write
    with pytest.raises(TimeoutError):
        await transport.write_actions("call-1", [action().action], timeout=1)
    assert transport.poisoned

    before = transport.bytes_written
    with pytest.raises(ProtocolError, match="poisoned"):
        await transport.write_final(final(False), timeout=1)
    assert transport.bytes_written == before


def test_stderr_proxy_marker_fits_every_natural_capacity_boundary():
    marker = b'{"event":"stderr_limit_reached"}\n'
    for capacity in range(200, 200 + len(marker) + 3):
        raw = io.StringIO()
        proxy = BoundedRedactingStream(raw, max_bytes=capacity)
        proxy.write("x" * (capacity - len(marker)))
        proxy.write("overflow")
        assert raw.getvalue().encode().endswith(marker)
        assert len(raw.getvalue().encode()) <= capacity


def test_cancelled_real_pipe_write_has_no_pending_transport_task():
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        with os.fdopen(write_fd, "wb", closefd=False) as output:
            transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=output)

            async def cancel_write():
                operation = asyncio.create_task(
                    transport.write_actions("call-1", [action().action])
                )
                await asyncio.sleep(0.02)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                await transport.drain(timeout=0.1)
                assert not transport._pending_writes

            asyncio.run(cancel_write())
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_real_pipe_write_timeout_does_not_leave_pending_io():
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            try:
                if not os.write(write_fd, b"x" * 4096):
                    break
            except BlockingIOError:
                break
        with os.fdopen(write_fd, "wb", closefd=False) as output:
            transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=output)
            started = time.monotonic()
            with pytest.raises((TimeoutError, ProtocolError)):
                asyncio.run(
                    transport.write_actions(
                        "call-1", [action().action], timeout=0.05
                    )
                )
            assert time.monotonic() - started < 0.5
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_malformed_inner_appworld_input_returns_controlled_final():
    root = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(root, "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "reliable_agent_appworld", "--log-level", "ERROR"],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        payload = public_task()
        payload["task"]["input"] = "{bad-json"
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        response = json.loads(readline(process))
        assert response["type"] == "final"
        assert response["completed"] is False
        assert response["likely_incorrect"] is True
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_stalled_observation_still_delivers_final_and_exits_with_stdin_open():
    provider = FakeProvider()
    root = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env.update(
        PYTHONPATH=os.path.join(root, "src"),
        OPENAI_BASE_URL=provider.base_url,
        OPENAI_API_KEY="local",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reliable_agent_appworld",
            "--log-level",
            "ERROR",
            "--model-retries",
            "0",
            "--episode-timeout-seconds",
            "3",
            "--final-response-reserve-seconds",
            "0.25",
            "--decision-reserve-seconds",
            "0",
            "--no-bootstrap-discovery",
        ],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        process.stdin.write(json.dumps(public_task()) + "\n")
        process.stdin.flush()
        action_message = json.loads(readline(process))
        assert action_message["type"] == "actions"
        started = time.monotonic()
        response = json.loads(readline(process, timeout=4))
        assert response["type"] == "final"
        assert response["completed"] is False
        assert process.wait(timeout=4) == 0
        assert time.monotonic() - started < 4
        assert process.stdin.closed is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        provider.close()
