from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import sys
import time
from collections.abc import Sequence
from functools import partial

from .config import AgentConfig, load_config
from .contracts import controlled_failure
from .deadline import EpisodeDeadline
from .engine import DecisionEngine
from .model import PydanticAIModelClient
from .observability import BoundedRedactingStream, EventLogger
from .protocol import (
    JsonLinesToolExecutor,
    JsonLinesTransport,
    appworld_completion_action,
    appworld_completion_observed,
    appworld_discovery_action,
    decode_appworld_public_input,
)
from .runner import TaskRunner


def _versions() -> dict[str, str]:
    result = {}
    for package in ("reliable-agent-appworld", "pydantic", "pydantic-ai", "jsonschema"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "unavailable"
    return result


async def run_episode(config: AgentConfig) -> None:
    transport = JsonLinesTransport(stdout=sys.stdout.buffer)
    stderr = BoundedRedactingStream(sys.stderr)
    sys.stdout = stderr
    sys.stderr = stderr
    logging.basicConfig(stream=stderr, force=True)
    episode_id, task = await transport.read_task()
    deadline = EpisodeDeadline(config.episode_timeout_seconds)
    logger = EventLogger(episode_id, config, stream=stderr)
    try:
        logger.emit(
            "effective_configuration", configuration=config.public_dict(), versions=_versions()
        )
        if any(tool.name == "execute_code" for tool in task.tools):
            task = decode_appworld_public_input(task)
            logger.emit("appworld_public_input_decoded", task_id=task.id)
        model = PydanticAIModelClient(config, logger)
        completion_check = (
            appworld_completion_observed
            if any(tool.name == "execute_code" for tool in task.tools)
            else None
        )
        engine = DecisionEngine(
            config,
            model,
            logger,
            completion_check=completion_check,
            bootstrap_action=(
                partial(
                    appworld_discovery_action,
                    authenticate=config.bootstrap_authentication,
                    private_only=config.adaptive_bootstrap,
                )
                if config.bootstrap_discovery and completion_check is not None
                else None
            ),
            completion_action=(
                appworld_completion_action
                if config.automatic_completion and completion_check is not None
                else None
            ),
        )
        executor = JsonLinesToolExecutor(transport)
        runner = TaskRunner(config, engine, executor, logger)
        final = await runner.run(task, deadline=deadline)
    except asyncio.CancelledError:
        logger.emit("process_cancellation", level="WARNING")
        final = controlled_failure("Episode cancellation was handled by the process boundary")
    except Exception as exc:
        logger.emit(
            "unhandled_episode_error",
            level="ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        final = controlled_failure(
            logger.redact_text(f"Controlled process stop: {type(exc).__name__}: {exc}")
        )

    delivery_started = time.monotonic()
    try:
        logger.emit(
            "final_delivery_started",
            remaining_seconds=deadline.remaining(),
            final_response=final.model_dump(mode="json"),
        )
        await transport.write_final(final, timeout=deadline.remaining())
        logger.emit(
            "final_delivery_succeeded",
            duration_seconds=time.monotonic() - delivery_started,
            protocol_bytes_written=transport.bytes_written,
        )
    except Exception as exc:
        logger.emit(
            "final_delivery_failed",
            level="ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
            remaining_seconds=deadline.remaining(),
        )
        if deadline.remaining() > 0 and not transport.poisoned:
            fallback = controlled_failure("Controlled finalization after protocol delivery failure")
            await transport.write_final(fallback, timeout=deadline.remaining())


def main(argv: Sequence[str] | None = None) -> None:
    config = load_config(argv)
    asyncio.run(run_episode(config))


if __name__ == "__main__":
    main()
