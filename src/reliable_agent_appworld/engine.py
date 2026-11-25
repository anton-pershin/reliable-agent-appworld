from __future__ import annotations

import time

import jsonschema

from .config import AgentConfig
from .contracts import ActionDecision, Decision, FinalDecision, TaskState
from .model import DecisionModel
from .observability import EventLogger


class DecisionEngine:
    def __init__(
        self, config: AgentConfig, model: DecisionModel, logger: EventLogger
    ) -> None:
        self.config = config
        self.model = model
        self.logger = logger

    @staticmethod
    def controlled_stop(reason: str) -> FinalDecision:
        return FinalDecision(
            completed=False,
            answer="Task stopped before completion.",
            confidence=0.0,
            likely_incorrect=True,
            reason=reason,
        )

    def _validate_action(self, state: TaskState, decision: ActionDecision) -> None:
        tools = {tool.name: tool for tool in state.task.tools}
        action = decision.action
        if action.name not in tools:
            raise ValueError(f"Unknown tool {action.name!r}; choose a supplied tool")
        schema = tools[action.name].arguments_schema
        jsonschema.validate(action.arguments, schema)

    async def next_decision(self, state: TaskState, remaining_seconds: float) -> Decision:
        deadline = time.monotonic() + remaining_seconds
        for attempt in range(self.config.output_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.controlled_stop("Decision budget exhausted")
            decision = await self.model.decide(state, remaining)
            if isinstance(decision, ActionDecision):
                try:
                    self._validate_action(state, decision)
                except (ValueError, TypeError, jsonschema.ValidationError) as exc:
                    state.validation_feedback.append(str(exc))
                    self.logger.emit(
                        "action_validation_failed",
                        attempt=attempt + 1,
                        validation_feedback=str(exc),
                        action=decision.action.model_dump(mode="json"),
                    )
                    continue
                return decision
            if not decision.completed:
                if not decision.likely_incorrect:
                    decision = decision.model_copy(update={"likely_incorrect": True})
                if attempt < self.config.output_retries:
                    state.validation_feedback.append(
                        "Recoverable public actions and budget remain. Return an ActionDecision to inspect docs, correct token usage, or otherwise make progress; give up only when public evidence shows further attempts cannot help."
                    )
                    continue
                return decision
            if not state.attempts and attempt < self.config.output_retries:
                state.validation_feedback.append(
                    "Completion requires public task evidence and, for AppWorld, a complete_task action first."
                )
                continue
            return decision
        return self.controlled_stop("The model could not produce a valid task decision")

    def record_attempt(self, state: TaskState, attempt) -> None:
        state.record_attempt(attempt)
        self.logger.emit(
            "action_attempt_recorded",
            call_id=attempt.call_id,
            action=attempt.action.model_dump(mode="json"),
            results=[result.model_dump(mode="json") for result in attempt.results],
            duration_seconds=attempt.duration_seconds,
        )
