from __future__ import annotations

import asyncio
import json
import os
import time
from importlib.resources import files
from typing import Any, Protocol

from pydantic import TypeAdapter
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import (
    ModelConnectionError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelRequestRejectedError,
    RunTimedOut,
)
from pydantic_ai.messages import RetryFeedbackPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.models import Model

from .config import AgentConfig
from .contracts import Decision, TaskState
from .errors import ModelRequestError
from .observability import EventLogger

_DECISION_ADAPTER = TypeAdapter(Decision)
_TRANSIENT_STATUS_CODES = {408, 409, 425, 429}


class DecisionModel(Protocol):
    async def decide(self, state: TaskState, remaining_seconds: float) -> Decision: ...


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, RunTimedOut, ModelConnectionError, ModelQuotaExceededError)):
        return True
    return isinstance(exc, ModelHTTPError) and (
        exc.status_code in _TRANSIENT_STATUS_CODES or exc.status_code >= 500
    )


def _usage(result: Any) -> dict[str, int]:
    usage = result.usage
    values = usage.model_dump() if hasattr(usage, "model_dump") else vars(usage)
    return {key: value for key, value in values.items() if isinstance(value, int)}


class PydanticAIModelClient:
    def __init__(
        self,
        config: AgentConfig,
        logger: EventLogger,
        model: str | Model | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
        self.model = model or config.model
        self.instructions = files("reliable_agent_appworld").joinpath("prompts/react.txt").read_text()

    def _settings(self, *, text: bool = False) -> ModelSettings:
        values: dict[str, Any] = {
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "top_k": self.config.top_k,
            "presence_penalty": self.config.presence_penalty,
            "max_tokens": self.config.max_output_tokens,
            "max_retries": 0,
            "extra_body": {
                "top_k": self.config.top_k,
                "min_p": self.config.min_p,
                "repetition_penalty": self.config.repetition_penalty,
                "chat_template_kwargs": {"enable_thinking": self.config.thinking},
            },
        }
        if text:
            values["tool_choice"] = "none"
        return ModelSettings(**values)

    def _public_settings(self) -> dict[str, Any]:
        return {
            **self.config.public_dict(),
            "seed": "not-configured",
            "thinking": self.config.thinking,
        }

    def _attempt_payload(self, attempt, observation_limit: int) -> dict[str, Any]:
        payload = attempt.model_dump(mode="json")
        for result in payload["results"]:
            observation = result.get("observation", {})
            rendered = json.dumps(observation, ensure_ascii=False, default=str)
            if len(rendered) <= observation_limit:
                continue
            marker = f"...<observation truncated {len(rendered) - observation_limit} chars; middle omitted>..."
            content_budget = max(2, observation_limit - len(marker))
            head_length = content_budget // 2
            tail_length = content_budget - head_length
            result["observation"] = {
                "truncated_json": (
                    rendered[:head_length] + marker + rendered[-tail_length:]
                ),
                "original_chars": len(rendered),
                "recovery": "Reprint a smaller targeted value if the omitted middle is needed.",
            }
        return payload

    def public_history(self, state: TaskState) -> list[Any]:
        entries = [
            self._attempt_payload(attempt, self.config.max_observation_chars)
            for attempt in state.attempts
        ]
        if not entries:
            return []
        marker = "<older history trimmed for context budget>"

        def size(values: list[Any]) -> int:
            return len(json.dumps(values, ensure_ascii=False, default=str))

        if size(entries) <= self.config.max_history_chars:
            return entries
        trimmed = False
        for index in range(len(entries) - 1):
            if size([marker, *entries]) <= self.config.max_history_chars:
                break
            entries[index]["results"] = "<old observation trimmed for context budget>"
            entries[index]["observation_trimmed"] = True
            trimmed = True
        while len(entries) > 1 and size([marker, *entries]) > self.config.max_history_chars:
            entries.pop(0)
            trimmed = True
        if size([marker, *entries]) > self.config.max_history_chars:
            latest = state.attempts[-1]
            entries = [
                {
                    "call_id": latest.call_id[:100],
                    "action": {
                        "name": latest.action.name[:100],
                        "arguments": "<truncated for context budget>",
                    },
                    "results": "<recent observation truncated for context budget>",
                    "observation_trimmed": True,
                }
            ]
            trimmed = True
        result: list[Any] = [marker, *entries] if trimmed else entries
        if size(result) > self.config.max_history_chars:
            result = [
                marker,
                {"latest_action": state.attempts[-1].action.name[:50], "results": "<trimmed>"},
            ]
        if size(result) > self.config.max_history_chars:
            result = ["<history trimmed; latest attempt retained but too large to display>"]
        if trimmed:
            state.history_trimmed = True
            self.logger.emit("context_trimmed", retained_attempts=len(entries))
        return result

    def _task_payload(self, state: TaskState) -> dict[str, Any]:
        payload = state.task.model_dump(mode="json")
        raw_input = payload["input"]
        try:
            decoded = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError):
            return payload
        if not isinstance(decoded, dict):
            return payload
        instruction = decoded.get("instruction")
        if isinstance(instruction, str):
            payload["objective"] = instruction
            payload["input"] = {
                key: value for key, value in decoded.items() if key != "instruction"
            }
        else:
            payload["input"] = decoded
        return payload

    def prompt(self, state: TaskState, remaining_seconds: float | None = None) -> str:
        budget = {}
        if remaining_seconds is not None:
            budget = {"remaining_episode_seconds": max(0, round(remaining_seconds, 1))}
        return json.dumps(
            {
                "task": self._task_payload(state),
                **budget,
                "action_attempts": state.action_attempts,
                "completion_observed": state.completion_observed,
                "remaining_action_attempts": max(
                    0, self.config.action_budget - state.action_attempts
                ),
                "history": self.public_history(state),
                "validation_feedback": state.validation_feedback[-10:],
                "prompt_version": "react-v4",
                "format": "Return ActionDecision or FinalDecision only",
            },
            ensure_ascii=False,
            default=str,
        )

    async def decide(self, state: TaskState, remaining_seconds: float) -> Decision:
        deadline = time.monotonic() + remaining_seconds
        last_error: Exception | None = None
        for attempt in range(self.config.model_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return await self._structured(state, remaining, deadline)
            except Exception as exc:
                last_error = exc
                self.logger.emit(
                    "model_error",
                    level="ERROR",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    attempt=attempt + 1,
                )
                if not _is_transient(exc) and not isinstance(exc, ModelRequestError):
                    break
                if isinstance(exc, ModelRequestError) and not exc.transient:
                    break
        raise ModelRequestError(
            f"Model decision failed: {type(last_error).__name__}: {last_error}",
            transient=_is_transient(last_error) if last_error else False,
        ) from last_error

    async def _structured(
        self, state: TaskState, remaining_seconds: float, deadline: float
    ) -> Decision:
        if self.config.output_mode == "text":
            return await self._text_output(state, deadline)
        try:
            return await self._tool_output(state, remaining_seconds)
        except ModelRequestRejectedError as exc:
            if self.config.output_mode != "auto":
                raise ModelRequestError(f"Tool output was rejected: {exc}") from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelRequestError("No time remains for text fallback", transient=True) from exc
            self.logger.emit("text_fallback", reason=str(exc))
            return await self._text_output(state, deadline)

    async def _tool_output(self, state: TaskState, remaining_seconds: float) -> Decision:
        prompt = self.prompt(state, remaining_seconds)
        operation_id = f"decision-{state.model_requests + 1}"
        state.model_requests += 1
        self.logger.emit(
            "model_request",
            operation_id=operation_id,
            output_mode="tool",
            prompt=prompt,
            generation_settings=self._public_settings(),
        )
        try:
            agent = Agent(
                self.model,
                output_type=Decision,
                instructions=self.instructions,
                model_settings=self._settings(),
                retries=self.config.output_retries,
            )
            result = await agent.run(
                prompt, timeout=min(self.config.model_timeout_seconds, remaining_seconds)
            )
        except ModelRequestRejectedError:
            raise
        except Exception as exc:
            raise ModelRequestError(
                f"Tool model request failed: {type(exc).__name__}: {exc}",
                transient=_is_transient(exc),
            ) from exc
        self._log_result(result, operation_id, "tool")
        return result.output

    async def _text_output(self, state: TaskState, deadline: float) -> Decision:
        base_prompt = self.prompt(state, deadline - time.monotonic()) + "\nReturn only one JSON object matching this schema:\n"
        schema = json.dumps(_DECISION_ADAPTER.json_schema(), ensure_ascii=False)
        feedback = ""
        for attempt in range(self.config.output_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelRequestError("Text output deadline exhausted", transient=True)
            prompt = base_prompt + schema + feedback
            operation_id = f"decision-{state.model_requests + 1}-text-{attempt + 1}"
            state.model_requests += 1
            self.logger.emit(
                "model_request",
                operation_id=operation_id,
                output_mode="text",
                prompt=prompt,
                generation_settings=self._public_settings(),
            )
            try:
                agent = Agent(
                    self.model,
                    output_type=str,
                    instructions=self.instructions,
                    model_settings=self._settings(text=True),
                    retries=0,
                )
                result = await agent.run(
                    prompt, timeout=min(self.config.model_timeout_seconds, remaining)
                )
            except Exception as exc:
                raise ModelRequestError(
                    f"Text model request failed: {type(exc).__name__}: {exc}",
                    transient=_is_transient(exc),
                ) from exc
            try:
                text = result.output.strip().removeprefix("```json").removesuffix("```").strip()
                output = _DECISION_ADAPTER.validate_python(json.loads(text))
            except (ValueError, TypeError, RecursionError) as exc:
                feedback = (
                    f"\nThe previous response was invalid: {exc}\n"
                    "Return only corrected JSON without Markdown or commentary."
                )
                self.logger.emit(
                    "model_repair",
                    operation_id=operation_id,
                    validation_feedback=str(exc),
                    public_output=text,
                )
                continue
            self._log_result(result, operation_id, "text")
            return output
        raise ModelRequestError("Text output did not satisfy the decision schema")

    def _log_result(self, result: Any, operation_id: str, output_mode: str) -> None:
        response = result.response
        reported_model = getattr(response, "model_id", None) or getattr(
            response, "model_name", None
        )
        provider = getattr(response, "provider_name", None)
        finish_reason = getattr(response, "finish_reason", None)
        self.logger.emit(
            "model_response",
            operation_id=operation_id,
            output_mode=output_mode,
            requested_model=self.config.model,
            reported_model=reported_model,
            provider=provider,
            finish_reason=finish_reason,
            usage=_usage(result),
            public_output=result.output.model_dump(mode="json")
            if hasattr(result.output, "model_dump")
            else result.output,
            unavailable_metadata=[
                name
                for name, value in (
                    ("reported_model", reported_model),
                    ("provider", provider),
                    ("finish_reason", finish_reason),
                )
                if value is None
            ],
        )
        for message in result.new_messages():
            for part in message.parts:
                if isinstance(part, (RetryPromptPart, RetryFeedbackPart)) or (
                    isinstance(part, ToolReturnPart) and part.outcome == "retried"
                ):
                    self.logger.emit(
                        "model_repair",
                        operation_id=operation_id,
                        repair_type=type(part).__name__,
                        validation_feedback=str(part.content),
                    )
