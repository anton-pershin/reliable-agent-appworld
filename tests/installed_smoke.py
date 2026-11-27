"""Run default episodes using the installed wheel and no source-path injection."""

import tempfile

from episode_harness import EPISODE_CASES, run_episode


def main():
    for case in EPISODE_CASES:
        with tempfile.TemporaryDirectory() as workspace:
            run_episode(case, cwd=workspace, pythonpath=None, secret_tag="installed")
        print(f"installed wheel {case.name} episode passed")


if __name__ == "__main__":
    main()
