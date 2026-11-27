from __future__ import annotations

import json
import math
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _json_object(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("expected a JSON object string") from exc
    if not isinstance(decoded, dict):
        raise ValueError("expected a JSON object string")
    return decoded


class ToolDescription(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    arguments_schema: dict = Field(default_factory=dict)


class Task(ContractModel):
    id: str = Field(min_length=1)
    instructions: str = ""
    input: str = ""
    tools: list[ToolDescription] = Field(default_factory=list)


class ActionRequest(ContractModel):
    name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments_object(cls, value: object) -> object:
        return _json_object(value)


class ActionBatch(ContractModel):
    actions: list[ActionRequest] = Field(min_length=1)


class ActionError(ContractModel):
    type: str = Field(min_length=1)
    message: str = ""


class ActionResult(ContractModel):
    status: Literal["ok", "error", "rejected"]
    observation: dict = Field(default_factory=dict)
    error: ActionError | None = None


class Observation(ContractModel):
    call_id: str
    results: list[ActionResult]


class VersionedMessage(ContractModel):
    protocol_version: int = Field(strict=True)

    @field_validator("protocol_version")
    @classmethod
    def version_must_be_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("protocol_version must be 1")
        return value


class TaskMessage(VersionedMessage):
    type: Literal["task"]
    episode_id: str = Field(min_length=1)
    task: Task


class ActionsMessage(VersionedMessage):
    type: Literal["actions"]
    call_id: str = Field(min_length=1)
    actions: list[ActionRequest] = Field(min_length=1)


class ObservationMessage(VersionedMessage):
    type: Literal["observation"]
    call_id: str
    results: list[ActionResult]

    def as_observation(self) -> Observation:
        return Observation(call_id=self.call_id, results=self.results)


class SelfAssessment(ContractModel):
    completed: StrictBool
    answer: str = Field(max_length=4000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    likely_incorrect: StrictBool
    reason: str = Field(max_length=1000)

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_non_json_numbers(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("confidence must be a finite JSON number")
        try:
            converted = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError("confidence must be a finite JSON number") from exc
        if not math.isfinite(converted):
            raise ValueError("confidence must be a finite JSON number")
        return value


class FinalMessage(VersionedMessage, SelfAssessment):
    type: Literal["final"]


class ActionDecision(ContractModel):
    kind: Literal["action"] = "action"
    action: ActionRequest

    @field_validator("action", mode="before")
    @classmethod
    def parse_action_object(cls, value: object) -> object:
        return _json_object(value)


class FinalDecision(SelfAssessment):
    kind: Literal["final"] = "final"

    def as_message(self) -> FinalMessage:
        return FinalMessage(
            protocol_version=1,
            type="final",
            completed=self.completed,
            answer=self.answer,
            confidence=self.confidence,
            likely_incorrect=self.likely_incorrect,
            reason=self.reason,
        )


def controlled_failure(reason: str) -> FinalDecision:
    bounded = reason if len(reason) <= 900 else reason[:897] + "..."
    return FinalDecision(
        completed=False,
        answer="Task stopped before completion.",
        confidence=0.0,
        likely_incorrect=True,
        reason=bounded,
    )


Decision: TypeAlias = ActionDecision | FinalDecision


class ActionAttempt(ContractModel):
    call_id: str = Field(min_length=1)
    action: ActionRequest
    results: list[ActionResult]
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


class TaskState(ContractModel):
    task: Task
    attempts: list[ActionAttempt] = Field(default_factory=list)
    validation_feedback: list[str] = Field(default_factory=list)
    history_trimmed: bool = False
    model_requests: int = 0
    pending_final: FinalDecision | None = None
    pending_completion_call_id: str | None = None
    automatic_completion_call_id: str | None = None
    completion_observed: bool = False
    completion_call_id: str | None = None

    @property
    def action_attempts(self) -> int:
        return len(self.attempts)

    @property
    def next_call_id(self) -> str:
        return f"call-{len(self.attempts) + 1}"

    def record_attempt(self, attempt: ActionAttempt) -> None:
        if attempt.call_id != self.next_call_id:
            raise ValueError(f"Expected {self.next_call_id}, got {attempt.call_id}")
        self.attempts.append(attempt)
