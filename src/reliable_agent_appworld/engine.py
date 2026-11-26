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
    ) -> None:
        self.config = config
        self.model = model
        self.logger = logger
        self.completion_check = completion_check

    def controlled_stop(self, reason: str) -> FinalDecision:
        return controlled_failure(self.logger.redact_text(reason))

    def _validate_action(self, state: TaskState, decision: ActionDecision) -> None:
        tools = {tool.name: tool for tool in state.task.tools}
        action = decision.action
        if action.name not in tools:
            raise ValueError(f"Unknown tool {action.name!r}; choose a supplied tool")
        schema = tools[action.name].arguments_schema
        jsonschema.validate(action.arguments, schema)

    def _completion_observed(self, state: TaskState) -> bool:
        if self.completion_check is not None:
            return self.completion_check(state)
        return bool(state.attempts)

    def release_deferred_final(self, state: TaskState) -> FinalDecision | None:
        if state.pending_final is None or not self._completion_observed(state):
            return None
        decision = state.pending_final
        state.pending_final = None
        self.logger.emit(
            "deferred_final_released",
            call_id=state.attempts[-1].call_id if state.attempts else None,
        )
        return decision

    async def next_decision(
        self, state: TaskState, remaining_seconds: float, *, allow_actions: bool = True
    ) -> Decision:
        deferred = self.release_deferred_final(state)
        if deferred is not None:
            return deferred
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
                return decision
            if not decision.completed:
                state.pending_final = None
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
            if attempt < self.config.output_retries:
                state.validation_feedback.append(
                    "A successful final is not yet supported by the required public completion observation. Your next action must obtain that evidence now; for AppWorld call apis.supervisor.complete_task(...) with a concise direct answer only for a question, then print its result."
                )
                continue
            return self.controlled_stop(
                "The model repeatedly declared completion without public task evidence or the required completion observation"
            )
        return self.controlled_stop("The model could not produce a valid task decision")

    def record_attempt(self, state: TaskState, attempt) -> None:
        previous_call_ids = [
            previous.call_id for previous in state.attempts if previous.action == attempt.action
        ]
        state.record_attempt(attempt)
        state.completion_observed = self._completion_observed(state)
        if previous_call_ids:
            state.validation_feedback.append(
                f"Action {attempt.call_id} exactly repeats {', '.join(previous_call_ids[-3:])}. "
                "Repeated reads/documentation do not add evidence. Choose a different, targeted "
                "next step; repeat only an intentional state-advancing operation and verify its new state."
            )
        code = attempt.action.arguments.get("code")
        if (
            isinstance(code, str)
            and "print(" not in code
            and any(
                result.observation.get("output") == "Execution successful."
                for result in attempt.results
            )
        ):
            state.validation_feedback.append(
                f"Action {attempt.call_id} printed no returned values, so its observation has no data. "
                "If it was an intentional write, proceed to verification/completion; otherwise inspect "
                "a saved variable or make a different call and print the relevant result. Do not repeat "
                "the same unprinted call merely to observe it."
            )
        self.logger.emit(
            "action_attempt_recorded",
            call_id=attempt.call_id,
            action=attempt.action.model_dump(mode="json"),
            results=[result.model_dump(mode="json") for result in attempt.results],
            duration_seconds=attempt.duration_seconds,
        )
