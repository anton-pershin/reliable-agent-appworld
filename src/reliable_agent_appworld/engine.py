from __future__ import annotations

import time
from collections.abc import Callable

import jsonschema

from .config import AgentConfig
from .contracts import ActionDecision, Decision, FinalDecision, TaskState, controlled_failure
from .model import DecisionModel
from .observability import EventLogger


class DecisionEngine:
    def __init__(
        self,
        config: AgentConfig,
        model: DecisionModel,
        logger: EventLogger,
        completion_check: Callable[[TaskState], bool] | None = None,
        completion_call_id: Callable[[TaskState], str | None] | None = None,
        bootstrap_action: Callable[[TaskState], ActionDecision | None] | None = None,
        completion_action: Callable[[TaskState, FinalDecision], ActionDecision | None]
        | None = None,
        action_check: Callable[[TaskState, ActionDecision], None] | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.logger = logger
        self.completion_check = completion_check
        self.completion_call_id = completion_call_id
        self.bootstrap_action = bootstrap_action
        self.completion_action = completion_action
        self.action_check = action_check

    def controlled_stop(self, reason: str) -> FinalDecision:
        return controlled_failure(self.logger.redact_text(reason))

    def _validate_action(self, state: TaskState, decision: ActionDecision) -> None:
        tools = {tool.name: tool for tool in state.task.tools}
        action = decision.action
        if action.name not in tools:
            raise ValueError(f"Unknown tool {action.name!r}; choose a supplied tool")
        schema = tools[action.name].arguments_schema
        jsonschema.validate(action.arguments, schema)
        if self.action_check is not None:
            self.action_check(state, decision)

    def _completion_observed(self, state: TaskState) -> bool:
        if self.completion_check is not None:
            return self.completion_check(state)
        return bool(state.attempts)

    def _completion_call_id(self, state: TaskState) -> str | None:
        if self.completion_call_id is not None:
            return self.completion_call_id(state)
        if self._completion_observed(state) and state.attempts:
            return state.attempts[-1].call_id
        return None

    def release_deferred_final(self, state: TaskState) -> FinalDecision | None:
        if (
            state.pending_final is None
            or state.pending_completion_call_id is None
            or self._completion_call_id(state) != state.pending_completion_call_id
        ):
            return None
        decision = state.pending_final
        state.pending_final = None
        state.pending_completion_call_id = None
        state.automatic_completion_call_id = None
        self.logger.emit(
            "deferred_final_released",
            call_id=state.completion_call_id,
        )
        return decision

    async def next_decision(
        self, state: TaskState, remaining_seconds: float, *, allow_actions: bool = True
    ) -> Decision:
        deferred = self.release_deferred_final(state)
        if deferred is not None:
            return deferred
        if allow_actions and not state.attempts and self.bootstrap_action is not None:
            decision = self.bootstrap_action(state)
            if decision is not None:
                self._validate_action(state, decision)
                self.logger.emit(
                    "bootstrap_action_selected", action=decision.action.model_dump(mode="json")
                )
                return decision
        deadline = time.monotonic() + remaining_seconds
        for attempt in range(self.config.output_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.controlled_stop("Decision budget exhausted")
            decision = await self.model.decide(state, remaining)
            if isinstance(decision, ActionDecision):
                if not allow_actions:
                    state.validation_feedback.append(
                        "The action budget is exhausted. Return a FinalDecision based on existing public evidence; do not request another action."
                    )
                    if attempt < self.config.output_retries:
                        continue
                    return self.controlled_stop(
                        "The model requested another action after the action budget was exhausted"
                    )
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
                if state.pending_final is not None and state.pending_completion_call_id is None:
                    state.pending_completion_call_id = state.next_call_id
                return decision
            if not decision.completed:
                state.pending_final = None
                state.pending_completion_call_id = None
                state.automatic_completion_call_id = None
                if not decision.likely_incorrect:
                    decision = decision.model_copy(update={"likely_incorrect": True})
                if not allow_actions:
                    return decision
                if attempt < self.config.output_retries:
                    state.validation_feedback.append(
                        "Recoverable public actions and budget remain. Return an ActionDecision to inspect docs, correct token usage, or otherwise make progress; give up only when public evidence shows further attempts cannot help."
                    )
                    continue
                return decision
            if self._completion_observed(state):
                return decision
            state.pending_final = decision
            state.pending_completion_call_id = None
            if self.completion_action is not None and state.attempts:
                completion = self.completion_action(state, decision)
                if completion is not None:
                    self._validate_action(state, completion)
                    state.pending_completion_call_id = state.next_call_id
                    state.automatic_completion_call_id = state.next_call_id
                    self.logger.emit(
                        "completion_action_selected",
                        action=completion.action.model_dump(mode="json"),
                    )
                    return completion
            if attempt < self.config.output_retries:
                state.validation_feedback.append(
                    "A successful final is not yet supported by the required public completion observation. Your next action must obtain that evidence now and print its public result."
                )
                continue
            return self.controlled_stop(
                "The model repeatedly declared completion without public task evidence or the required completion observation"
            )
        return self.controlled_stop("The model could not produce a valid task decision")

    def record_attempt(self, state: TaskState, attempt) -> None:
        state.record_attempt(attempt)
        observed_call_id = self._completion_call_id(state)
        state.completion_call_id = observed_call_id
        state.completion_observed = observed_call_id is not None
        if state.pending_completion_call_id == attempt.call_id:
            if observed_call_id != attempt.call_id:
                state.pending_final = None
                state.pending_completion_call_id = None
                state.automatic_completion_call_id = None
                state.validation_feedback.append(
                    "The deferred final was invalidated because its completion attempt did not produce matching public success evidence."
                )
        if state.automatic_completion_call_id == attempt.call_id:
            state.automatic_completion_call_id = None
        self.logger.emit(
            "action_attempt_recorded",
            call_id=attempt.call_id,
            action=attempt.action.model_dump(mode="json"),
            results=[result.model_dump(mode="json") for result in attempt.results],
            duration_seconds=attempt.duration_seconds,
        )
