from pathlib import Path

import pytest
from episode_harness import (
    EPISODE_CASES,
    FakeProvider,
    public_task,
    readline,
    run_episode,
    write_observation,
)

__all__ = [
    "EPISODE_CASES",
    "FakeProvider",
    "public_task",
    "readline",
    "run_episode",
    "write_observation",
]


@pytest.mark.parametrize("case", EPISODE_CASES, ids=lambda case: case.name)
def test_installed_style_subprocess_protocol_has_no_provider_banner(case):
    root = Path(__file__).resolve().parents[1]
    run_episode(case, cwd=root, pythonpath=root / "src", secret_tag="source")
