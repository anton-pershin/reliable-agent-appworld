from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from typing import Literal

from pydantic import Field, model_validator

from .contracts import ContractModel

ENV_PREFIX = "RELIABLE_AGENT_APPWORLD_"
FLOAT_FIELDS = {
    "temperature",
    "top_p",
    "min_p",
    "presence_penalty",
    "repetition_penalty",
    "episode_timeout_seconds",
    "model_timeout_seconds",
    "final_response_reserve_seconds",
}


class AgentConfig(ContractModel):
    model: str = Field(default="openai:qwen/qwen3.5-9b", min_length=1)
    output_mode: Literal["auto", "tool", "text"] = "auto"
    temperature: float = Field(default=0.6, ge=0, allow_inf_nan=False)
    top_p: float = Field(default=0.95, gt=0, le=1, allow_inf_nan=False)
    top_k: int = Field(default=20, ge=0)
    min_p: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2, allow_inf_nan=False)
    repetition_penalty: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, ge=1)
    model_retries: int = Field(default=2, ge=0, le=10)
    output_retries: int = Field(default=2, ge=0, le=10)

    episode_timeout_seconds: float = Field(default=285, gt=0, allow_inf_nan=False)
    action_budget: int = Field(default=50, ge=1)
    model_timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    final_response_reserve_seconds: float = Field(default=25, ge=0, allow_inf_nan=False)

    max_observation_chars: int = Field(default=6000, ge=100)
    max_history_chars: int = Field(default=20000, ge=1000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_payload_chars: int = Field(default=2000, ge=0)
    max_log_bytes: int = Field(default=750_000, ge=1000, le=900_000)

    @model_validator(mode="after")
    def validate_budgets(self) -> AgentConfig:
        if self.final_response_reserve_seconds >= self.episode_timeout_seconds:
            raise ValueError("final_response_reserve_seconds must be below episode_timeout_seconds")
        if self.max_observation_chars > self.max_history_chars:
            raise ValueError("max_observation_chars cannot exceed max_history_chars")
        return self

    def public_dict(self) -> dict:
        return self.model_dump(mode="json")


def _environment_value(name: str, default: object) -> object:
    raw = os.environ.get(ENV_PREFIX + name.upper())
    if raw is None:
        return default
    if name in FLOAT_FIELDS:
        return float(raw)
    if isinstance(default, bool):
        return raw.lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def load_config(argv: Sequence[str] | None = None) -> AgentConfig:
    values = {
        name: _environment_value(name, field.default)
        for name, field in AgentConfig.model_fields.items()
    }
    parser = argparse.ArgumentParser(prog="reliable-agent-appworld")
    parser.add_argument("--model", default=argparse.SUPPRESS)
    parser.add_argument("--output-mode", choices=("auto", "tool", "text"), default=argparse.SUPPRESS)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default=argparse.SUPPRESS)
    for name in (
        "top_k",
        "max_output_tokens",
        "model_retries",
        "output_retries",
        "action_budget",
        "max_observation_chars",
        "max_history_chars",
        "log_payload_chars",
        "max_log_bytes",
    ):
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=argparse.SUPPRESS)
    for name in (
        "temperature",
        "top_p",
        "min_p",
        "presence_penalty",
        "repetition_penalty",
        "episode_timeout_seconds",
        "model_timeout_seconds",
        "final_response_reserve_seconds",
    ):
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=argparse.SUPPRESS)
    namespace = parser.parse_args(argv)
    values.update(vars(namespace))
    return AgentConfig.model_validate(values)
