"""Run a full episode using the installed wheel and no source-path injection."""

import json
import os
import subprocess
import sys
import tempfile

from test_subprocess_episode import FakeProvider, public_task, readline, write_observation

from reliable_agent_appworld.contracts import ActionsMessage, FinalMessage


def main():
    provider = FakeProvider()
    with tempfile.TemporaryDirectory() as workspace:
        env = os.environ.copy()
        env.update(OPENAI_BASE_URL=provider.base_url, OPENAI_API_KEY="local")
        env.pop("PYTHONPATH", None)
        env.pop("PYDANTIC_AI_NO_BANNER", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "reliable_agent_appworld",
                "--log-level",
                "ERROR",
                "--model-retries",
                "0",
            ],
            cwd=workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            process.stdin.write(json.dumps(public_task()) + "\n")
            process.stdin.flush()
            bootstrap = ActionsMessage.model_validate_json(readline(process))
            write_observation(process, bootstrap.call_id, "Public API schemas\n")
            action = ActionsMessage.model_validate_json(readline(process))
            write_observation(process, action.call_id, "subprocess-ok\n")
            completion_action = ActionsMessage.model_validate_json(readline(process))
            assert "complete_task()" in completion_action.actions[0].arguments["code"]
            write_observation(
                process, completion_action.call_id, "Marked the active task complete.\n"
            )
            final = FinalMessage.model_validate_json(readline(process))
            assert final.completed and not final.likely_incorrect
            assert process.wait(timeout=15) == 0
            assert process.stdout.read() == ""
            assert len(provider.requests) == 2
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            provider.close()
    print("installed wheel episode passed")


if __name__ == "__main__":
    main()
