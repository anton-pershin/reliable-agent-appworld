from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from reliable_agent_appworld import protocol
from reliable_agent_appworld.config import AgentConfig
from reliable_agent_appworld.contracts import ActionRequest, FinalDecision, Task, ToolDescription
from reliable_agent_appworld.observability import EventLogger
from reliable_agent_appworld.protocol import (
    JsonLinesToolExecutor,
    JsonLinesTransport,
    ProtocolError,
    decode_appworld_public_input,
)


def _public_payload() -> dict[str, Any]:
    return {
        "instruction": "Find the supervisor's public contact details.",
        "supervisor": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.test",
            "phone_number": "+1-555-0100",
        },
        "app_descriptions": {
            "contacts": "Read public contact records.",
            "messages": "Send a message when requested.",
        },
    }


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _task_message(*, protocol_version: Any = 1) -> dict[str, Any]:
    return {
        "protocol_version": protocol_version,
        "type": "task",
        "episode_id": "episode-1",
        "task": {
            "id": "task-1",
            "instructions": "Use only public data.",
            "input": "public input",
            "tools": [
                {
                    "name": "execute_code",
                    "description": "Execute public Python code.",
                    "arguments_schema": {"type": "object"},
                }
            ],
        },
    }


def _observation_message(
    *, call_id: str = "call-1", result_count: int = 1, protocol_version: Any = 1
) -> dict[str, Any]:
    results = [
        {"status": "ok", "observation": {"position": index}, "error": None}
        for index in range(result_count)
    ]
    return {
        "protocol_version": protocol_version,
        "type": "observation",
        "call_id": call_id,
        "results": results,
    }


def _actions() -> list[ActionRequest]:
    return [ActionRequest(name="execute_code", arguments={"code": "print('public')"})]


def _final_decision() -> FinalDecision:
    return FinalDecision(
        completed=False,
        answer="",
        confidence=0.0,
        likely_incorrect=True,
        reason="Controlled test stop",
    )


class _SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class _RecordingBytesIO(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class _BlockingActionStream(_RecordingBytesIO):
    """A binary stream whose physical action write can be held in its worker thread."""

    def __init__(self) -> None:
        super().__init__()
        self.action_started = threading.Event()
        self.release_action = threading.Event()
        self.action_finished = threading.Event()
        self.final_written = threading.Event()

    def write(self, data: bytes) -> int:
        if b'"type":"actions"' in data:
            self.action_started.set()
            if not self.release_action.wait(timeout=2):
                raise TimeoutError("test did not release action write")
            try:
                return super().write(data)
            finally:
                self.action_finished.set()
        if b'"type":"final"' in data:
            self.final_written.set()
        return super().write(data)


def _stream_events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


# AppWorld public-input decoding


def test_appworld_public_input_preserves_unicode_multiline_text_and_task_metadata() -> None:
    payload = _public_payload()
    payload["instruction"] = "Read café notes 😊\nThen summarize 東京 — без private data."
    payload["supervisor"]["first_name"] = "Zoë"
    payload["supervisor"]["last_name"] = "李🙂"
    payload["app_descriptions"]["naïve-app"] = "Line one\nLine two: κόσμε 🌍"
    task = Task(
        id="unicode-task",
        instructions="Original instructions stay intact.",
        input=json.dumps(payload, ensure_ascii=False),
        tools=[ToolDescription(name="execute_code", description="Public execution")],
    )

    decoded = decode_appworld_public_input(task)

    assert json.loads(decoded.input) == payload
    assert "café" in decoded.input
    assert "東京" in decoded.input
    assert "李🙂" in decoded.input
    assert "\\n" in decoded.input  # JSON encoding retains embedded newlines safely.
    assert decoded.id == task.id
    assert decoded.instructions == task.instructions
    assert decoded.tools == task.tools
    assert task.input != decoded.input  # Normalization does not mutate the original task.


@pytest.mark.parametrize("raw_input", ["", "not JSON", "{]"], ids=["empty", "text", "bad-json"])
def test_appworld_public_input_rejects_malformed_json(raw_input: str) -> None:
    with pytest.raises(ProtocolError, match="must be a JSON string"):
        decode_appworld_public_input(Task(id="task", input=raw_input))


@pytest.mark.parametrize("raw_input", ["null", "[]", '"text"', "42"])
def test_appworld_public_input_rejects_non_objects(raw_input: str) -> None:
    with pytest.raises(ProtocolError, match="must be an object"):
        decode_appworld_public_input(Task(id="task", input=raw_input))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "instruction": "Missing fields",
                "supervisor": _public_payload()["supervisor"],
            },
            "fields must be exactly",
        ),
        (_public_payload() | {"private_evaluation": "must not enter"}, "fields must be exactly"),
        (_public_payload() | {"instruction": 7}, "instruction must be a string"),
        (_public_payload() | {"supervisor": []}, "Invalid AppWorld supervisor"),
        (
            _public_payload()
            | {
                "supervisor": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "ada@example.test",
                }
            },
            "Invalid AppWorld supervisor",
        ),
        (
            _public_payload()
            | {
                "supervisor": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "ada@example.test",
                    "phone_number": "+1-555-0100",
                    "private_id": "not-public",
                }
            },
            "Invalid AppWorld supervisor",
        ),
        (
            _public_payload()
            | {
                "supervisor": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "ada@example.test",
                    "phone_number": 42,
                }
            },
            "supervisor fields must be strings",
        ),
        (_public_payload() | {"app_descriptions": []}, "string map"),
        (
            _public_payload() | {"app_descriptions": {"contacts": {"private": True}}},
            "string map",
        ),
    ],
    ids=[
        "missing-field",
        "extra-private-field",
        "instruction-type",
        "supervisor-type",
        "supervisor-missing-field",
        "supervisor-extra-field",
        "supervisor-value-type",
        "descriptions-type",
        "description-value-type",
    ],
)
def test_appworld_public_input_rejects_invalid_shapes(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        decode_appworld_public_input(Task(id="task", input=json.dumps(payload)))


# Strict JSON Lines input and transport bounds


async def test_read_task_accepts_version_one_and_unicode() -> None:
    message = _task_message()
    message["task"]["instructions"] = "Public café instructions 🙂\n第二行"
    transport = JsonLinesTransport(stdin=io.BytesIO(_json_bytes(message)), stdout=io.BytesIO())

    episode_id, task = await transport.read_task(timeout=1)

    assert episode_id == "episode-1"
    assert task.id == "task-1"
    assert task.instructions == "Public café instructions 🙂\n第二行"
    assert task.tools[0].name == "execute_code"


@pytest.mark.parametrize("message_type", ["task", "observation"])
@pytest.mark.parametrize("version", [True, False, 1.0, 0.0])
async def test_jsonl_protocol_version_rejects_bools_and_floats(
    message_type: str, version: Any
) -> None:
    if message_type == "task":
        message = _task_message(protocol_version=version)
    else:
        message = _observation_message(protocol_version=version)
    transport = JsonLinesTransport(stdin=io.BytesIO(_json_bytes(message)), stdout=io.BytesIO())

    with pytest.raises(ProtocolError, match="protocol_version"):
        if message_type == "task":
            await transport.read_task()
        else:
            await transport.read_observation("call-1", 1)


@pytest.mark.parametrize("version", ["1", None, 0, 2])
async def test_read_task_rejects_other_invalid_protocol_versions(version: Any) -> None:
    transport = JsonLinesTransport(
        stdin=io.BytesIO(_json_bytes(_task_message(protocol_version=version))),
        stdout=io.BytesIO(),
    )

    with pytest.raises(ProtocolError, match="protocol_version"):
        await transport.read_task()


@pytest.mark.parametrize(
    "line",
    [
        b"",
        b"\n",
        b"{not-json}\n",
        b"\xff\xfe\n",
        b'{"protocol_version":1,"type":"task"}\n',
        _json_bytes(_task_message() | {"private_evaluation": {}}),
        _json_bytes(_task_message()).rstrip(b"\n") + _json_bytes(_task_message()),
    ],
    ids=[
        "eof",
        "blank-line",
        "malformed-json",
        "invalid-utf8",
        "missing-fields",
        "extra-field",
        "multiple-json-values",
    ],
)
async def test_read_task_rejects_malformed_jsonl(line: bytes) -> None:
    transport = JsonLinesTransport(stdin=io.BytesIO(line), stdout=io.BytesIO())

    with pytest.raises(ProtocolError):
        await transport.read_task()


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
async def test_read_observation_rejects_every_nonfinite_json_number(number: str) -> None:
    # 1e400 is valid JSON syntax but overflows Python's float to infinity. It must not
    # bypass the rejection of the explicit NaN/Infinity extensions.
    line = (
        '{"protocol_version":1,"type":"observation","call_id":"call-1",'
        '"results":[{"status":"ok","observation":{"value":'
        + number
        + '},"error":null}]}\n'
    ).encode()
    transport = JsonLinesTransport(stdin=io.BytesIO(line), stdout=io.BytesIO())

    with pytest.raises(ProtocolError, match="Invalid ObservationMessage"):
        await transport.read_observation("call-1", 1)


async def test_read_observation_rejects_mismatched_call_id() -> None:
    transport = JsonLinesTransport(
        stdin=io.BytesIO(_json_bytes(_observation_message(call_id="other-call"))),
        stdout=io.BytesIO(),
    )

    with pytest.raises(ProtocolError, match="does not match"):
        await transport.read_observation("expected-call", 1)


async def test_read_observation_rejects_mismatched_result_count() -> None:
    transport = JsonLinesTransport(
        stdin=io.BytesIO(_json_bytes(_observation_message(result_count=1))),
        stdout=io.BytesIO(),
    )

    with pytest.raises(ProtocolError, match="result count"):
        await transport.read_observation("call-1", 2)


async def test_protocol_input_limit_is_enforced_on_utf8_bytes_not_characters() -> None:
    message = _observation_message()
    message["results"][0]["observation"] = {"text": "🙂🙂🙂🙂"}
    line = _json_bytes(message)
    transport = JsonLinesTransport(
        stdin=io.BytesIO(line), stdout=io.BytesIO(), max_bytes=len(line) - 1
    )

    with pytest.raises(ProtocolError, match="byte limit"):
        await transport.read_observation("call-1", 1)


async def test_actions_write_is_unicode_jsonl_bounded_and_flushed() -> None:
    output = _RecordingBytesIO()
    transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=output)
    actions = [ActionRequest(name="execute_code", arguments={"code": "print('café 🙂')"})]

    await transport.write_actions("call-unicode", actions)

    encoded = output.getvalue()
    assert encoded.endswith(b"\n")
    assert len(encoded.splitlines()) == 1
    assert "café 🙂" in encoded.decode("utf-8")
    assert json.loads(encoded) == {
        "protocol_version": 1,
        "type": "actions",
        "call_id": "call-unicode",
        "actions": [
            {"name": "execute_code", "arguments": {"code": "print('café 🙂')"}}
        ],
    }
    assert output.flush_count == 1

    too_small_output = _RecordingBytesIO()
    too_small = JsonLinesTransport(
        stdin=io.BytesIO(), stdout=too_small_output, max_bytes=len(encoded) - 1
    )
    with pytest.raises(ProtocolError, match="Outgoing protocol message exceeds byte limit"):
        await too_small.write_actions("call-unicode", actions)
    assert too_small_output.getvalue() == b""
    assert too_small_output.flush_count == 0


async def test_tool_executor_correlates_write_and_ordered_read_results() -> None:
    incoming = _observation_message(call_id="call-42", result_count=2)
    incoming["results"] = [
        {"status": "ok", "observation": {"position": "first"}, "error": None},
        {
            "status": "error",
            "observation": {},
            "error": {"type": "public_error", "message": "Second failed"},
        },
    ]
    output = _RecordingBytesIO()
    transport = JsonLinesTransport(stdin=io.BytesIO(_json_bytes(incoming)), stdout=output)
    executor = JsonLinesToolExecutor(transport)
    actions = [
        ActionRequest(name="first", arguments={"index": 1}),
        ActionRequest(name="second", arguments={"index": 2}),
    ]

    results = await executor.execute("call-42", actions, timeout=5)

    assert [result.status for result in results] == ["ok", "error"]
    assert results[0].observation == {"position": "first"}
    assert results[1].error is not None
    assert results[1].error.message == "Second failed"
    written = json.loads(output.getvalue())
    assert written["call_id"] == "call-42"
    assert [action["name"] for action in written["actions"]] == ["first", "second"]


async def test_tool_executor_gives_observation_only_remaining_absolute_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _SequenceClock(100.0, 100.75)
    monkeypatch.setattr(protocol, "time", SimpleNamespace(monotonic=clock))
    transport = JsonLinesTransport(
        stdin=io.BytesIO(_json_bytes(_observation_message())), stdout=io.BytesIO()
    )
    observed_timeouts: list[float | None] = []
    original_read = transport.read_observation

    async def recording_read(
        expected_call_id: str, result_count: int, timeout: float | None = None
    ) -> Any:
        observed_timeouts.append(timeout)
        return await original_read(expected_call_id, result_count, timeout)

    monkeypatch.setattr(transport, "read_observation", recording_read)

    results = await JsonLinesToolExecutor(transport).execute("call-1", _actions(), timeout=3)

    assert len(results) == 1
    assert observed_timeouts == [2.25]


async def test_concurrent_final_waits_for_an_action_write_already_in_progress() -> None:
    output = _BlockingActionStream()
    transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=output)

    action_task = asyncio.create_task(transport.write_actions("call-1", _actions()))
    assert await asyncio.to_thread(output.action_started.wait, 1)
    final_task = asyncio.create_task(transport.write_final(_final_decision()))
    await asyncio.sleep(0)  # Let the final writer contend for the transport lock.
    output.release_action.set()

    await asyncio.wait_for(asyncio.gather(action_task, final_task), timeout=1)

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [message["type"] for message in messages] == ["actions", "final"]
    assert output.flush_count == 2


async def test_final_waits_for_cancelled_action_whose_physical_write_is_pending() -> None:
    output = _BlockingActionStream()
    transport = JsonLinesTransport(stdin=io.BytesIO(), stdout=output)
    action_task = asyncio.create_task(transport.write_actions("call-1", _actions()))
    assert await asyncio.to_thread(output.action_started.wait, 1)

    action_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action_task

    final_task = asyncio.create_task(transport.write_final(_final_decision()))
    try:
        # A correct guarded writer cannot emit the final yet. Waiting briefly for that
        # event also gives a broken implementation an opportunity to overtake the action.
        await asyncio.to_thread(output.final_written.wait, 0.2)
    finally:
        output.release_action.set()

    await asyncio.wait_for(final_task, timeout=1)
    assert await asyncio.to_thread(output.action_finished.wait, 1)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [message["type"] for message in messages] == ["actions", "final"]


# Structured observability


def test_logger_emits_ordered_events_to_stderr_and_never_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    clock = _SequenceClock(10.0, 10.25, 11.0)
    logger = EventLogger("episode-🙂", AgentConfig(), clock=clock)

    logger.emit("task_started", task_id="public-task")
    logger.emit("task_finished", level="WARNING", completed=False)

    assert stdout.getvalue() == ""
    events = _stream_events(stderr)
    assert [event["event"] for event in events] == ["task_started", "task_finished"]
    assert [event["event_index"] for event in events] == [1, 2]
    assert [event["elapsed_seconds"] for event in events] == [0.25, 1.0]
    assert all(event["episode_id"] == "episode-🙂" for event in events)
    assert events[0]["task_id"] == "public-task"
    assert events[1]["level"] == "WARNING"
    assert all(event["private_reasoning"] == "not-retained" for event in events)
    assert all("timestamp" in event for event in events)


def test_logger_filters_below_configured_level_without_consuming_event_ids() -> None:
    stream = io.StringIO()
    logger = EventLogger(
        "episode", AgentConfig(log_level="WARNING"), stream=stream, clock=lambda: 5.0
    )

    logger.emit("debug", level="DEBUG")
    logger.emit("info", level="INFO")
    logger.emit("warning", level="WARNING")

    events = _stream_events(stream)
    assert [event["event"] for event in events] == ["warning"]
    assert events[0]["event_index"] == 1
    assert logger.bytes_emitted == len(stream.getvalue().encode("utf-8"))


def test_multibyte_payload_truncation_is_explicit_and_unicode_safe() -> None:
    stream = io.StringIO()
    original = "🙂 café\n漢字 and more"
    limit = 7
    logger = EventLogger(
        "episode",
        AgentConfig(log_payload_chars=limit),
        stream=stream,
        clock=lambda: 0.0,
    )

    logger.emit("unicode", nested={"text": original})

    raw = stream.getvalue()
    event = json.loads(raw)
    expected = original[:limit] + f"...<truncated {len(original) - limit} chars>"
    assert event["nested"]["text"] == expected
    assert original[:limit] in event["nested"]["text"]
    assert "\N{REPLACEMENT CHARACTER}" not in raw
    assert raw.encode("utf-8").decode("utf-8") == raw
    assert logger.bytes_emitted == len(raw.encode("utf-8"))


def test_nested_payload_container_and_depth_limits_are_explicit() -> None:
    stream = io.StringIO()
    logger = EventLogger(
        "episode", AgentConfig(log_payload_chars=100), stream=stream, clock=lambda: 0.0
    )
    deep = {"one": {"two": {"three": {"four": {"five": {"six": {"seven": "hidden"}}}}}}}

    logger.emit("containers", items=list(range(55)), mapping={str(i): i for i in range(55)}, deep=deep)

    event = _stream_events(stream)[0]
    assert event["items"][:50] == list(range(50))
    assert event["items"][50] == "<truncated 5 items>"
    assert event["mapping"]["_truncated_items"] == 5
    assert len(event["mapping"]) == 51
    assert event["deep"]["one"]["two"]["three"]["four"]["five"] == "<depth-truncated>"


def test_credentials_and_bearer_tokens_are_redacted_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_secret = "sk-live-café-🙂-12345"
    anthropic_secret = "anthropic-private-67890"
    monkeypatch.setenv("OPENAI_API_KEY", openai_secret)
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_secret)
    stream = io.StringIO()
    logger = EventLogger("episode", AgentConfig(), stream=stream, clock=lambda: 0.0)

    logger.emit(
        "secrets",
        api_key="api-key-field-value",
        nested={
            "Authorization": "Bearer abc.def+ghi/jkl=",
            "password": "hunter2",
            "safe": f"Provider returned {openai_secret} and {anthropic_secret}",
        },
        items=[f"prefix {openai_secret} suffix", "Bearer short_token-123"],
        public="unchanged",
    )

    raw = stream.getvalue()
    for credential in (
        openai_secret,
        anthropic_secret,
        "api-key-field-value",
        "hunter2",
        "abc.def+ghi/jkl=",
        "short_token-123",
    ):
        assert credential not in raw
    event = _stream_events(stream)[0]
    assert event["api_key"] == "<redacted>"
    assert event["nested"]["Authorization"] == "<redacted>"
    assert event["nested"]["password"] == "<redacted>"
    assert event["nested"]["safe"].count("<redacted>") == 2
    assert event["items"][0] == "prefix <redacted> suffix"
    assert event["items"][1] == "Bearer <redacted>"
    assert event["public"] == "unchanged"


def test_redaction_happens_before_payload_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-secret-that-must-never-leak"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    stream = io.StringIO()
    logger = EventLogger(
        "episode",
        AgentConfig(log_payload_chars=40),
        stream=stream,
        clock=lambda: 0.0,
    )

    logger.emit("bounded_secret", text=secret + ("public-" * 20))

    event = _stream_events(stream)[0]
    assert secret not in stream.getvalue()
    assert "<redacted>" in event["text"]
    assert "<truncated " in event["text"]


def _normal_log_prefix(config_kwargs: dict[str, Any], event_count: int = 5) -> bytes:
    stream = io.StringIO()
    logger = EventLogger(
        "episode",
        AgentConfig(max_log_bytes=900_000, **config_kwargs),
        stream=stream,
        clock=lambda: 0.0,
    )
    for index in range(event_count):
        logger.emit("normal", index=index, public="🙂 café")
    return stream.getvalue().encode("utf-8")


def _emit_normal_events(logger: EventLogger, event_count: int = 5) -> None:
    for index in range(event_count):
        logger.emit("normal", index=index, public="🙂 café")


def test_total_log_cap_uses_utf8_bytes_and_records_suppression() -> None:
    config_kwargs = {"log_payload_chars": 40}
    prefix_size = len(_normal_log_prefix(config_kwargs))
    # Leave enough room for the mandatory limit marker, but not another large event.
    max_log_bytes = prefix_size + 600
    stream = io.StringIO()
    logger = EventLogger(
        "episode",
        AgentConfig(max_log_bytes=max_log_bytes, **config_kwargs),
        stream=stream,
        clock=lambda: 0.0,
    )
    _emit_normal_events(logger)
    assert logger.bytes_emitted == prefix_size

    logger.emit("too_large", payloads=["🙂" * 100] * 50)
    raw_at_limit = stream.getvalue()
    logger.emit("after_limit", public="must be suppressed")

    assert stream.getvalue() == raw_at_limit
    assert logger.suppressed_events == 2
    assert logger.bytes_emitted == len(raw_at_limit.encode("utf-8"))
    assert logger.bytes_emitted <= max_log_bytes
    events = _stream_events(stream)
    assert events[-1]["event"] == "log_limit_reached"
    assert events[-1]["level"] == "WARNING"
    assert all(event["event"] != "after_limit" for event in events)


def test_total_log_cap_reserves_space_for_explicit_suppression_event() -> None:
    config_kwargs = {"log_payload_chars": 40}
    prefix_size = len(_normal_log_prefix(config_kwargs))
    max_log_bytes = prefix_size + 50
    stream = io.StringIO()
    logger = EventLogger(
        "episode",
        AgentConfig(max_log_bytes=max_log_bytes, **config_kwargs),
        stream=stream,
        clock=lambda: 0.0,
    )
    _emit_normal_events(logger)

    logger.emit("too_large", payloads=["🙂" * 100] * 50)

    assert logger.bytes_emitted <= max_log_bytes
    events = _stream_events(stream)
    assert events[-1]["event"] == "log_limit_reached"
    assert logger.suppressed_events == 1
