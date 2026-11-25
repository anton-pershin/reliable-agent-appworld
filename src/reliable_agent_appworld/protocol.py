from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from collections.abc import Sequence
from typing import BinaryIO, TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import (
    ActionRequest,
    ActionResult,
    ActionsMessage,
    FinalDecision,
    Observation,
    ObservationMessage,
    Task,
    TaskMessage,
)

MAX_PROTOCOL_BYTES = 1_000_000
MessageT = TypeVar("MessageT", bound=BaseModel)


class ProtocolError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"JSON number is outside the finite float range: {value}")
    return result


def decode_appworld_public_input(task: Task) -> Task:
    try:
        payload = json.loads(task.input)
    except json.JSONDecodeError as exc:
        raise ProtocolError("AppWorld task.input must be a JSON string") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("Decoded AppWorld task.input must be an object")
    required = {"instruction", "supervisor", "app_descriptions"}
    if set(payload) != required:
        raise ProtocolError(f"AppWorld public input fields must be exactly {sorted(required)}")
    if not isinstance(payload["instruction"], str):
        raise ProtocolError("AppWorld instruction must be a string")
    supervisor = payload["supervisor"]
    supervisor_fields = {"first_name", "last_name", "email", "phone_number"}
    if not isinstance(supervisor, dict) or set(supervisor) != supervisor_fields:
        raise ProtocolError("Invalid AppWorld supervisor data")
    if any(not isinstance(value, str) for value in supervisor.values()):
        raise ProtocolError("AppWorld supervisor fields must be strings")
    descriptions = payload["app_descriptions"]
    if not isinstance(descriptions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in descriptions.items()
    ):
        raise ProtocolError("AppWorld app_descriptions must be a string map")
    normalized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return task.model_copy(update={"input": normalized})


class JsonLinesTransport:
    def __init__(
        self,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        max_bytes: int = MAX_PROTOCOL_BYTES,
    ) -> None:
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.max_bytes = max_bytes
        self._write_lock = asyncio.Lock()

    async def _readline(self, timeout: float | None = None) -> bytes:
        operation = asyncio.to_thread(self.stdin.readline, self.max_bytes + 1)
        line = await asyncio.wait_for(operation, timeout) if timeout is not None else await operation
        if not line:
            raise ProtocolError("Unexpected end of protocol input")
        if len(line) > self.max_bytes:
            raise ProtocolError("Protocol message exceeds byte limit")
        return line

    def _decode(self, line: bytes, model: type[MessageT]) -> MessageT:
        try:
            text = line.decode("utf-8")
            payload = json.loads(
                text, parse_constant=_reject_constant, parse_float=_finite_float
            )
            return model.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ProtocolError(f"Invalid {model.__name__}: {exc}") from exc

    async def read_task(self, timeout: float | None = None) -> tuple[str, Task]:
        message = self._decode(await self._readline(timeout), TaskMessage)
        return message.episode_id, message.task

    async def _write(self, message: BaseModel) -> None:
        encoded = (
            json.dumps(
                message.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ProtocolError("Outgoing protocol message exceeds byte limit")

        def write() -> None:
            self.stdout.write(encoded)
            self.stdout.flush()

        await asyncio.to_thread(write)

    async def _serialized_write(self, message: BaseModel, timeout: float | None) -> None:
        await self._write_lock.acquire()
        write_task = asyncio.create_task(self._write(message))
        try:
            if timeout is not None:
                await asyncio.wait_for(asyncio.shield(write_task), timeout)
            else:
                await asyncio.shield(write_task)
        except (TimeoutError, asyncio.CancelledError):
            if write_task.done():
                self._write_lock.release()
            else:
                write_task.add_done_callback(lambda _: self._write_lock.release())
            raise
        except BaseException:
            self._write_lock.release()
            raise
        else:
            self._write_lock.release()

    async def write_actions(
        self, call_id: str, actions: Sequence[ActionRequest], timeout: float | None = None
    ) -> None:
        message = ActionsMessage(call_id=call_id, actions=list(actions))
        await self._serialized_write(message, timeout)

    async def read_observation(
        self, expected_call_id: str, result_count: int, timeout: float | None = None
    ) -> Observation:
        message = self._decode(await self._readline(timeout), ObservationMessage)
        if message.call_id != expected_call_id:
            raise ProtocolError(
                f"Observation call_id {message.call_id!r} does not match {expected_call_id!r}"
            )
        if len(message.results) != result_count:
            raise ProtocolError("Observation result count does not match submitted actions")
        return message.as_observation()

    async def write_final(self, decision: FinalDecision, timeout: float | None = None) -> None:
        await self._serialized_write(decision.as_message(), timeout)


class JsonLinesToolExecutor:
    def __init__(self, transport: JsonLinesTransport) -> None:
        self.transport = transport

    async def execute(
        self,
        call_id: str,
        actions: Sequence[ActionRequest],
        timeout: float,
    ) -> list[ActionResult]:
        deadline = time.monotonic() + timeout
        await self.transport.write_actions(call_id, actions, timeout=timeout)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("No time remains for observation")
        observation = await self.transport.read_observation(
            call_id, len(actions), timeout=remaining
        )
        return observation.results
