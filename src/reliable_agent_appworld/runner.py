from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol

from .config import AgentConfig
from .contracts import (
    ActionAttempt,
    ActionError,
    ActionRequest,
    ActionResult,
    FinalDecision,
    Task,
    TaskState,
)
from .deadline import EpisodeDeadline
from .engine import DecisionEngine
from .observability import EventLogger


class ToolExecutor(Protocol):
    async def execute(
        self, call_id: str, actions: Sequence[ActionRequest], timeout: float
    ) -> list[ActionResult]: ...


class TaskRunner:
    def __init__(
        self,
        config: AgentConfig,
        engine: DecisionEngine,
        executor: ToolExecutor,
        logger: EventLogger,
        clock=time.monotonic,
    ) -> None:
        self.config = config
        self.engine = engine
        self.executor = executor
        self.logger = logger
        self.clock = clock
        self.last_state: TaskState | None = None

    async def run(
        self, task: Task, deadline: EpisodeDeadline | None = None
    ) -> FinalDecision:
        deadline = deadline or EpisodeDeadline(self.config.episode_timeout_seconds, self.clock)
        state = TaskState(task=task)
        self.last_state = state
        self.logger.emit("task_started", task=task.model_dump(mode="json"))
        try:
            while True:
                remaining = deadline.remaining()
                usable = remaining - self.config.final_response_reserve_seconds
                if usable <= 0:
                    return await self._finish("Final-response reserve reached")
                if state.action_attempts >= self.config.action_budget:
                    return await self._finish("Solution action budget exhausted")
                decision = await asyncio.wait_for(
                    self.engine.next_decision(state, usable), timeout=usable
                )
                if isinstance(decision, FinalDecision):
                    self.logger.emit(
                        "task_finished",
                        final_response=decision.model_dump(mode="json"),
                        action_attempts=state.action_attempts,
                    )
                    return decision
                call_id = state.next_call_id
                started = self.clock()
                action_time = deadline.remaining() - self.config.final_response_reserve_seconds
                if action_time <= 0:
                    return await self._finish("Final-response reserve reached before action execution")
                try:
                    results = await asyncio.wait_for(
                        self.executor.execute(
                            call_id, [decision.action], timeout=action_time
                        ),
                        timeout=action_time,
                    )
                except asyncio.CancelledError as exc:
                    self._record_unknown(state, call_id, decision.action, started, exc)
                    raise
                except Exception as exc:
                    self._record_unknown(state, call_id, decision.action, started, exc)
                    return await self._finish(
                        f"Action outcome is uncertain: {type(exc).__name__}: {exc}"
                    )
                attempt = ActionAttempt(
                    call_id=call_id,
                    action=decision.action,
                    results=results,
                    duration_seconds=max(0, self.clock() - started),
                )
                self.engine.record_attempt(state, attempt)
        except asyncio.CancelledError:
            self.logger.emit("episode_cancelled", level="WARNING")
            raise
        except Exception as exc:
            self.logger.emit(
                "runner_error",
                level="ERROR",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return await self._finish(f"Controlled stop after {type(exc).__name__}: {exc}")

    def _record_unknown(self, state, call_id, action, started, exc: BaseException) -> None:
        uncertain = ActionResult(
            status="error",
            observation={},
            error=ActionError(
                type="execution_outcome_unknown",
                message=self.logger.redact_text(f"{type(exc).__name__}: {exc}"),
            ),
        )
        attempt = ActionAttempt(
            call_id=call_id,
            action=action,
            results=[uncertain],
            duration_seconds=max(0, self.clock() - started),
        )
        self.engine.record_attempt(state, attempt)

    async def _finish(self, reason: str) -> FinalDecision:
        decision = self.engine.controlled_stop(reason)
        self.logger.emit(
            "task_finished",
            final_response=decision.model_dump(mode="json"),
            action_attempts=self.last_state.action_attempts if self.last_state else 0,
        )
        return decision
