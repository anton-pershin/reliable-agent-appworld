from __future__ import annotations

import ast
import asyncio
import io
import json
import math
import os
import re
import sys
import time
from collections.abc import Sequence
from typing import BinaryIO, TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import (
    ActionDecision,
    ActionRequest,
    ActionResult,
    ActionsMessage,
    FinalDecision,
    FinalMessage,
    Observation,
    ObservationMessage,
    Task,
    TaskMessage,
    TaskState,
)

MAX_PROTOCOL_BYTES = 1_000_000
FINAL_RESERVE_BYTES = 25_000
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


def _strict_json_loads(value: str):
    return json.loads(value, parse_constant=_reject_constant, parse_float=_finite_float)


def decode_appworld_public_input(task: Task) -> Task:
    try:
        payload = _strict_json_loads(task.input)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("AppWorld task.input must be a JSON string containing finite JSON") from exc
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
    normalized = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    return task.model_copy(update={"input": normalized})


def _successful_complete_task_call(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        function = node.func
        if function.attr != "complete_task" or not isinstance(function.value, ast.Attribute):
            continue
        supervisor = function.value
        if (
            supervisor.attr != "supervisor"
            or not isinstance(supervisor.value, ast.Name)
            or supervisor.value.id != "apis"
        ):
            continue
        failed = any(
            keyword.arg == "status"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "fail"
            for keyword in node.keywords
        )
        if not failed:
            return True
    return False


def _attempt_completion_succeeded(attempt) -> bool:
    code = attempt.action.arguments.get("code")
    if not isinstance(code, str) or not _successful_complete_task_call(code):
        return False
    return any(
        result.status == "ok"
        and "Marked the active task complete." in str(result.observation.get("output", ""))
        for result in attempt.results
    )


def appworld_completion_call_id(state: TaskState) -> str | None:
    for attempt in reversed(state.attempts):
        if _attempt_completion_succeeded(attempt):
            return attempt.call_id
    return None


def appworld_completion_observed(state: TaskState) -> bool:
    """Recognize public successful completion without coupling the generic engine to AppWorld."""
    return appworld_completion_call_id(state) is not None


_CREDENTIAL_NAME = re.compile(
    r".*(?:password|access_token|tokens?|login|login_response|account_password_map).*",
    re.IGNORECASE,
)


def appworld_validate_action(state: TaskState, decision: ActionDecision) -> None:
    """Reject obvious raw-requester and credential-printing code before execution."""
    del state
    code = decision.action.arguments.get("code")
    if not isinstance(code, str):
        return
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "apis" for alias in node.names
        ):
            raise ValueError("Do not import apis; use the preloaded high-level API object")
        if isinstance(node, ast.Name) and node.id == "requester":
            raise ValueError("Raw requester/HTTP access is forbidden; use high-level app APIs")
        if isinstance(node, ast.Attribute) and node.attr == "requester":
            raise ValueError("Raw requester/HTTP access is forbidden; use high-level app APIs")
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "print":
            continue
        for argument in node.args:
            for child in ast.walk(argument):
                if isinstance(child, ast.Name) and _CREDENTIAL_NAME.fullmatch(child.id):
                    raise ValueError(
                        "Do not print passwords, tokens, login responses, or credential variables"
                    )
                if isinstance(child, ast.Attribute) and child.attr in {
                    "login",
                    "show_account_passwords",
                }:
                    raise ValueError("Do not print raw login or account-password responses")


_PRIVATE_CONTEXT = re.compile(
    r"\b(?:my|mine|me|i|current|previous|next|feed|queue|libraries|library|account|friends?|roommates?|coworkers?|spouse|wife|husband|transactions?|notes?|files?|bills?|tasks?|alarms?|messages?|playlists?)\b",
    re.IGNORECASE,
)


def appworld_discovery_action(
    state: TaskState, *, authenticate: bool = False, private_only: bool = False
) -> ActionDecision | None:
    """Build one public, task-independent API-doc onboarding action for AppWorld."""
    try:
        public_input = json.loads(state.task.input)
    except json.JSONDecodeError:
        return None
    objective = public_input.get("instruction") if isinstance(public_input, dict) else None
    if not isinstance(objective, str) or not objective:
        return None
    if private_only and (
        _PRIVATE_CONTEXT.search(objective) is None and _objective_requests_answer(objective)
    ):
        return None
    authentication = '''
# Establish permitted public sessions once; retain tokens without printing secrets.
supervisor_profile = apis.supervisor.show_profile()
account_password_map = {
    item["account_name"]: item["password"]
    for item in apis.supervisor.show_account_passwords()
}
access_tokens = {}
authenticated_apps = []
for app_name in app_names[:4]:
    if app_name not in account_password_map:
        continue
    username = supervisor_profile["phone_number"] if app_name == "phone" else supervisor_profile["email"]
    login_response = getattr(apis, app_name).login(
        username=username, password=account_password_map[app_name]
    )
    if isinstance(login_response, dict) and "access_token" in login_response:
        access_tokens[app_name] = login_response["access_token"]
        authenticated_apps.append(app_name)
        print("Saved access token for", app_name, "as access_tokens[%r]" % app_name)
    else:
        print("Login failed for", app_name, "with a non-token response; credentials are not printed")
print("Authenticated apps:", authenticated_apps)
if relation_context and "phone" in access_tokens:
    phone_contacts = []
    contact_page_index = 0
    while True:
        contact_page = apis.phone.search_contacts(
            access_token=access_tokens["phone"], page_index=contact_page_index, page_limit=20
        )
        if not isinstance(contact_page, list):
            print("Contact prefetch unavailable for phone; raw response is not printed")
            break
        phone_contacts.extend(contact_page)
        if len(contact_page) < 20:
            break
        contact_page_index += 1
    print("Public contacts and relationships:")
    for contact in phone_contacts:
        print({
            "name": (contact.get("first_name"), contact.get("last_name")),
            "email": contact.get("email"),
            "phone_number": contact.get("phone_number"),
            "relationships": contact.get("relationships"),
        })
''' if authenticate else ""
    code = f'''# Deterministic public API onboarding; no private data is accessed.
objective = {json.dumps(objective, ensure_ascii=False)}
print("Objective:", objective)

def compact_doc(doc):
    if not isinstance(doc, dict) or "api_name" not in doc:
        return doc
    return {{
        "app_name": doc.get("app_name"),
        "api_name": doc.get("api_name"),
        "description": doc.get("description"),
        "parameters": [
            (p.get("name"), p.get("type"), p.get("required"), p.get("description"), p.get("constraints"))
            for p in doc.get("parameters", [])
        ],
        "success_response": doc.get("response_schemas", {{}}).get("success"),
    }}

docs = apis.api_docs.search_api_docs(query=objective, page_index=0, page_limit=10)
print("Relevant API schemas:")
if isinstance(docs, list):
    for doc in docs:
        print(compact_doc(doc))
else:
    print(docs)
    docs = []

print("Supervisor credential/profile schemas:")
for api_name in ("show_profile", "show_account_passwords"):
    print(compact_doc(apis.api_docs.show_api_doc(app_name="supervisor", api_name=api_name)))

app_names = sorted({{doc.get("app_name") for doc in docs if isinstance(doc, dict)}} - {{"api_docs", "supervisor", None}})
app_aliases = {{
    "amazon": ("amazon",),
    "file_system": ("file system", "file_system", "~/"),
    "gmail": ("gmail",),
    "phone": ("phone", "alarm", "alarms", "text message", "voice message"),
    "simple_note": ("simple note", "simple_note", "simplenote"),
    "splitwise": ("splitwise",),
    "spotify": (
        "spotify", "playlist", "playlists", "song", "songs",
        "artist", "artists", "album", "albums",
    ),
    "todoist": ("todoist",),
    "venmo": ("venmo", "money", "payment", "payments", "pay", "paid"),
}}
objective_lower = objective.lower()
mentioned = [
    name for name in app_aliases
    if any(alias in objective_lower for alias in app_aliases.get(name, (name,)))
]
if mentioned:
    app_names = mentioned
relation_context = any(word in objective_lower for word in ("roommate", "family", "friend", "coworker", "sibling", "parent", "spouse", "wife", "husband"))
if relation_context:
    if "phone" not in app_names:
        app_names.append("phone")
app_names = sorted(set(app_names))
print("Relevant app login schemas:")
for app_name in app_names[:4]:
    print(compact_doc(apis.api_docs.show_api_doc(app_name=app_name, api_name="login")))
{authentication}
'''
    return ActionDecision(
        action=ActionRequest(name="execute_code", arguments={"code": code})
    )


_ANSWER_QUESTION = re.compile(
    r"(?:^|[.!?]\s+|,\s*)(?:what|who|whom|whose|which|when|where|why|how|is|are|was|were|do|does|did|can|could|would|should)\b",
    re.IGNORECASE,
)
_ANSWER_IMPERATIVE = re.compile(
    r"(?:^|[.!?]\s+|,\s*)(?:find|show|give|tell|list|name|provide|calculate|compute|determine|identify|return)\b",
    re.IGNORECASE,
)
_ACTION_IMPERATIVE = re.compile(
    r"\b(?:play|send|add|remove|update|delete|set|like|unlike|create|mark|move|download|upload|follow|unfollow|befriend|unfriend|start|pause|seek|clear|reset|reassign|comment|rate|rated|rating|increase|decrease)\b",
    re.IGNORECASE,
)


def _objective_requests_answer(objective: str) -> bool:
    if objective.rstrip().endswith("?") or _ANSWER_QUESTION.search(objective):
        return True
    answer = _ANSWER_IMPERATIVE.search(objective)
    if answer is None:
        return False
    action = _ACTION_IMPERATIVE.search(objective)
    return action is None or answer.start() > action.start()


def _direct_answer(objective: str, value: str) -> str | int | float:
    stripped = value.strip()
    answer_requested = _objective_requests_answer(objective)
    if answer_requested and re.search(r"\bwho\b", objective, re.IGNORECASE):
        stripped = re.split(r"\s+with\s+\d", stripped, maxsplit=1)[0].rstrip()
        stripped = re.sub(r"\s*\([^()]*\)\s*$", "", stripped).rstrip()
        if re.search(r"\s+is\s+", stripped, re.IGNORECASE):
            left, right = re.split(
                r"\s+is\s+", stripped, maxsplit=1, flags=re.IGNORECASE
            )
            if left.strip().lower() in {"it", "this", "answer", "the answer"}:
                stripped = right
            else:
                stripped = left if len(left.split()) <= len(right.split()) else right
        stripped = stripped.strip(" ,").rstrip(".")
    if not stripped:
        return stripped
    if (
        len(stripped) >= 2
        and stripped[0] == stripped[-1]
        and stripped[0] in {'"', "'"}
    ):
        try:
            stripped = json.loads(stripped) if stripped[0] != "'" else stripped[1:-1]
        except json.JSONDecodeError:
            pass
    numeric_objective = answer_requested and (
        re.search(r"\bhow\b", objective, re.IGNORECASE)
        or re.search(r"\b(?:total|cost|amount|duration)\b", objective, re.IGNORECASE)
    )
    if not numeric_objective:
        return stripped
    numeric_match = re.fullmatch(r"[-+]?\$?\d[\d,]*(?:\.\d+)?", stripped)
    numeric = numeric_match.group(0) if numeric_match else None
    if numeric is None:
        currency = re.findall(r"[-+]?\$\d[\d,]*(?:\.\d+)?", stripped)
        numbers = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", stripped)
        numeric = currency[-1] if currency else (numbers[-1] if numbers else None)
    if numeric is not None:
        normalized = str(numeric).replace("$", "").replace(",", "")
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            pass
    return stripped


def appworld_completion_action(
    state: TaskState, decision: FinalDecision
) -> ActionDecision | None:
    """Translate a model completion claim into its required public AppWorld call."""
    try:
        public_input = json.loads(state.task.input)
    except json.JSONDecodeError:
        return None
    objective = public_input.get("instruction") if isinstance(public_input, dict) else None
    if not isinstance(objective, str) or not objective:
        return None
    if _objective_requests_answer(objective):
        direct_answer = _direct_answer(objective, decision.answer)
        if direct_answer == "":
            return None
        answer = json.dumps(direct_answer, ensure_ascii=False)
        code = f"print(apis.supervisor.complete_task(answer={answer}))"
    else:
        code = "print(apis.supervisor.complete_task())"
    return ActionDecision(
        action=ActionRequest(name="execute_code", arguments={"code": code})
    )


def _file_descriptor(stream: BinaryIO) -> int | None:
    try:
        descriptor = stream.fileno()
    except (AttributeError, io.UnsupportedOperation, OSError, ValueError):
        return None
    return descriptor if isinstance(descriptor, int) and descriptor >= 0 else None


class JsonLinesTransport:
    def __init__(
        self,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        max_bytes: int = MAX_PROTOCOL_BYTES,
        max_total_bytes: int = MAX_PROTOCOL_BYTES,
        final_reserve_bytes: int = FINAL_RESERVE_BYTES,
    ) -> None:
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.max_bytes = max_bytes
        self.max_total_bytes = max_total_bytes
        self.final_reserve_bytes = final_reserve_bytes
        self.bytes_written = 0
        self._poisoned = False
        self._pending_writes: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self._read_buffer = bytearray()
        self._stdin_fd = _file_descriptor(self.stdin)
        self._stdout_fd = _file_descriptor(self.stdout)
        if self._stdin_fd is not None:
            os.set_blocking(self._stdin_fd, False)
        if self._stdout_fd is not None:
            os.set_blocking(self._stdout_fd, False)

    async def _wait_descriptor(self, descriptor: int, *, write: bool, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        add = loop.add_writer if write else loop.add_reader
        remove = loop.remove_writer if write else loop.remove_reader
        add(descriptor, ready.set)
        try:
            await asyncio.wait_for(ready.wait(), timeout)
        finally:
            remove(descriptor)

    async def _fd_readline(self, timeout: float | None) -> bytes:
        assert self._stdin_fd is not None
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            newline = self._read_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._read_buffer[: newline + 1])
                del self._read_buffer[: newline + 1]
                if len(line) > self.max_bytes:
                    raise ProtocolError("Protocol message exceeds byte limit")
                return line
            if len(self._read_buffer) > self.max_bytes:
                raise ProtocolError("Protocol message exceeds byte limit")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Protocol read deadline exhausted")
            if remaining is None:
                await self._wait_descriptor_forever(False)
            else:
                await self._wait_descriptor(
                    self._stdin_fd, write=False, timeout=remaining
                )
            try:
                chunk = os.read(
                    self._stdin_fd, min(65_536, self.max_bytes + 1 - len(self._read_buffer))
                )
            except BlockingIOError:
                continue
            if not chunk:
                raise ProtocolError("Unexpected end of protocol input")
            self._read_buffer.extend(chunk)

    async def _wait_descriptor_forever(self, write: bool) -> None:
        descriptor = self._stdout_fd if write else self._stdin_fd
        assert descriptor is not None
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        add = loop.add_writer if write else loop.add_reader
        remove = loop.remove_writer if write else loop.remove_reader
        add(descriptor, ready.set)
        try:
            await ready.wait()
        finally:
            remove(descriptor)

    async def _readline(self, timeout: float | None = None) -> bytes:
        if self._stdin_fd is not None:
            return await self._fd_readline(timeout)
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
            payload = _strict_json_loads(text)
            return model.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ProtocolError(f"Invalid {model.__name__}: {exc}") from exc

    async def read_task(self, timeout: float | None = None) -> tuple[str, Task]:
        message = self._decode(await self._readline(timeout), TaskMessage)
        return message.episode_id, message.task

    def _encode(self, message: BaseModel) -> bytes:
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
        reserve = 0 if isinstance(message, FinalMessage) else self.final_reserve_bytes
        if self.bytes_written + len(encoded) + reserve > self.max_total_bytes:
            raise ProtocolError("Cumulative protocol stdout budget exhausted")
        return encoded

    async def _fd_write(self, encoded: bytes, timeout: float | None) -> None:
        assert self._stdout_fd is not None
        deadline = time.monotonic() + timeout if timeout is not None else None
        view = memoryview(encoded)
        while view:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Protocol write deadline exhausted")
            if remaining is None:
                await self._wait_descriptor_forever(True)
            else:
                await self._wait_descriptor(self._stdout_fd, write=True, timeout=remaining)
            try:
                written = os.write(self._stdout_fd, view)
            except BlockingIOError:
                continue
            self.bytes_written += written
            view = view[written:]

    async def _write(self, message: BaseModel) -> None:
        encoded = self._encode(message)
        if self._stdout_fd is not None:
            await self._fd_write(encoded, timeout=None)
            return

        def write() -> None:
            self.stdout.write(encoded)
            self.stdout.flush()

        await asyncio.to_thread(write)
        self.bytes_written += len(encoded)

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    async def _serialized_write(self, message: BaseModel, timeout: float | None) -> None:
        if self._poisoned:
            raise ProtocolError("Protocol stream is poisoned by a failed partial write")
        started = time.monotonic()
        if timeout is not None:
            await asyncio.wait_for(self._write_lock.acquire(), timeout)
        else:
            await self._write_lock.acquire()
        remaining = None if timeout is None else max(0.0, timeout - (time.monotonic() - started))
        starting_bytes = self.bytes_written
        if self._stdout_fd is not None:
            try:
                if remaining is not None:
                    await asyncio.wait_for(self._write(message), remaining)
                else:
                    await self._write(message)
            except BaseException:
                if self.bytes_written != starting_bytes:
                    self._poisoned = True
                raise
            finally:
                self._write_lock.release()
            return

        write_task = asyncio.create_task(self._write(message))
        self._pending_writes.add(write_task)

        def finish(task: asyncio.Task) -> None:
            self._pending_writes.discard(task)
            error = None if task.cancelled() else task.exception()
            if task.cancelled() or (
                error is not None
                and (self.bytes_written != starting_bytes or not isinstance(error, ProtocolError))
            ):
                self._poisoned = True
            self._write_lock.release()

        write_task.add_done_callback(finish)
        if remaining is not None:
            await asyncio.wait_for(asyncio.shield(write_task), remaining)
        else:
            await asyncio.shield(write_task)

    async def drain(self, timeout: float = 1.0) -> None:
        pending = list(self._pending_writes)
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=timeout
            )

    async def write_actions(
        self, call_id: str, actions: Sequence[ActionRequest], timeout: float | None = None
    ) -> None:
        message = ActionsMessage(
            protocol_version=1, type="actions", call_id=call_id, actions=list(actions)
        )
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
