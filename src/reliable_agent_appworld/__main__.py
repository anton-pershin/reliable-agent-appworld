from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import sys
from collections.abc import Sequence

from .config import AgentConfig, load_config
from .engine import DecisionEngine
from .model import PydanticAIModelClient
from .observability import EventLogger
from .protocol import JsonLinesToolExecutor, JsonLinesTransport, decode_appworld_public_input
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
    sys.stdout = sys.stderr
    episode_id, task = await transport.read_task()
    logger = EventLogger(episode_id, config)
    logger.emit("effective_configuration", configuration=config.public_dict(), versions=_versions())
    if any(tool.name == "execute_code" for tool in task.tools):
        task = decode_appworld_public_input(task)
        logger.emit("appworld_public_input_decoded", task_id=task.id)
    model = PydanticAIModelClient(config, logger)
    engine = DecisionEngine(config, model, logger)
    executor = JsonLinesToolExecutor(transport)
    runner = TaskRunner(config, engine, executor, logger)
    try:
        final = await runner.run(task)
    except Exception as exc:
        logger.emit(
            "unhandled_episode_error",
            level="ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        final = engine.controlled_stop(f"Controlled process stop: {type(exc).__name__}: {exc}")
    await transport.write_final(
        final, timeout=max(1.0, config.final_response_reserve_seconds)
    )


def main(argv: Sequence[str] | None = None) -> None:
    config = load_config(argv)
    logging.basicConfig(stream=sys.stderr)
    asyncio.run(run_episode(config))


if __name__ == "__main__":
    main()
