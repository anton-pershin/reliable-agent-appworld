from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

from reliable_agent_appworld.contracts import ActionsMessage, FinalMessage


class EpisodeCase(NamedTuple):
    name: str
    objective: str
    completion_code: str


EPISODE_CASES = (
    EpisodeCase(
        "private-action",
        "Print subprocess-ok for my playlist.",
        "print(apis.supervisor.complete_task())",
    ),
    EpisodeCase(
        "private-answer",
        "What is my subprocess answer?",
        'print(apis.supervisor.complete_task(answer="subprocess-ok"))',
    ),
)


class FakeProvider:
    def __init__(self):
        self.requests = []
        self.responses = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.requests.append(payload)
                tools = payload.get("tools") or []
                request_number = len(provider.requests)
                action = request_number == 1
                if not tools or payload.get("tool_choice") == "none":
                    content = (
                        "ACTION\n```python\nprint('subprocess-ok')\n```"
                        if action
                        else "FINAL\n"
                        + json.dumps(
                            {
                                "completed": True,
                                "answer": "subprocess-ok",
                                "confidence": 0.95,
                                "likely_incorrect": False,
                                "reason": "The public observation confirmed the printed output.",
                            }
                        )
                    )
                    if "input" in payload:
                        response = {
                            "id": f"resp-{request_number}",
                            "object": "response",
                            "created_at": int(time.time()),
                            "status": "completed",
                            "model": payload["model"],
                            "output": [
                                {
                                    "id": f"msg-{request_number}",
                                    "type": "message",
                                    "status": "completed",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": content,
                                            "annotations": [],
                                        }
                                    ],
                                }
                            ],
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "total_tokens": 120,
                            },
                        }
                    else:
                        response = {
                            "id": f"chatcmpl-{request_number}",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": payload["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 20,
                                "total_tokens": 120,
                            },
                        }
                    encoded = json.dumps(response).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                fragment = "ActionDecision" if action else "FinalDecision"
                tool_names = [
                    item.get("function", {}).get("name") or item.get("name", "")
                    for item in tools
                ]
                tool = next(name for name in tool_names if fragment in name)
                output = (
                    {
                        "kind": "action",
                        "action": {
                            "name": "execute_code",
                            "arguments": {
                                "code": (
                                    "print('subprocess-ok')"
                                    if request_number == 1
                                    else "print(apis.supervisor.complete_task(answer='subprocess-ok'))"
                                )
                            },
                        },
                    }
                    if action
                    else {
                        "kind": "final",
                        "completed": True,
                        "answer": "subprocess-ok",
                        "confidence": 0.95,
                        "likely_incorrect": False,
                        "reason": "The public observation confirmed the printed output.",
                    }
                )
                if "input" in payload:
                    response = {
                        "id": f"resp-{len(provider.requests)}",
                        "object": "response",
                        "created_at": int(time.time()),
                        "status": "completed",
                        "model": payload["model"],
                        "output": [
                            {
                                "type": "function_call",
                                "id": f"fc-{len(provider.requests)}",
                                "call_id": f"provider-call-{len(provider.requests)}",
                                "name": tool,
                                "arguments": json.dumps(output),
                            }
                        ],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    }
                else:
                    response = {
                        "id": f"chatcmpl-{len(provider.requests)}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": payload["model"],
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": f"provider-call-{len(provider.requests)}",
                                            "type": "function",
                                            "function": {
                                                "name": tool,
                                                "arguments": json.dumps(output),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                        },
                    }
                encoded = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def readline(process, timeout=15):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        ready = selector.select(timeout)
        assert ready, "Timed out waiting for protocol output; stderr may contain diagnostics"
        line = process.stdout.readline()
        assert line, "Protocol stdout closed unexpectedly"
        return line
    finally:
        selector.close()


def write_observation(process, call_id, output, **extra_observation):
    process.stdin.write(
        json.dumps(
            {
                "protocol_version": 1,
                "type": "observation",
                "call_id": call_id,
                "results": [
                    {
                        "status": "ok",
                        "observation": {"output": output, **extra_observation},
                        "error": None,
                    }
                ],
            }
        )
        + "\n"
    )
    process.stdin.flush()


def public_task(case: EpisodeCase | None = None):
    objective = case.objective if case else "Print subprocess-ok."
    episode_id = f"subprocess-{case.name}" if case else "subprocess-episode"
    task_id = f"public-{case.name}" if case else "public-task"
    return {
        "protocol_version": 1,
        "type": "task",
        "episode_id": episode_id,
        "task": {
            "id": task_id,
            "instructions": "Use execute_code, then return the final response.",
            "input": json.dumps(
                {
                    "instruction": objective,
                    "supervisor": {
                        "first_name": "Test",
                        "last_name": "User",
                        "email": "test@example.invalid",
                        "phone_number": "1",
                    },
                    "app_descriptions": {"supervisor": "Supervisor app"},
                }
            ),
            "tools": [
                {
                    "name": "execute_code",
                    "description": "Run Python",
                    "arguments_schema": {
                        "type": "object",
                        "properties": {"code": {"type": "string", "minLength": 1}},
                        "required": ["code"],
                        "additionalProperties": False,
                    },
                }
            ],
        },
    }


def run_episode(case: EpisodeCase, *, cwd, pythonpath, secret_tag):
    provider = FakeProvider()
    api_key = f"sk-{secret_tag}-{case.name}-api-canary"
    session_secret = f"session-{secret_tag}-{case.name}-canary"
    jwt_secret = f"eyJhbGciOiJIUzI1NiJ9.{secret_tag}-{case.name}.jwt-signature"
    secrets = (api_key, session_secret, jwt_secret)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RELIABLE_AGENT_APPWORLD_")
    }
    env.pop("PYTHONPATH", None)
    env.pop("PYDANTIC_AI_NO_BANNER", None)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    env.update(OPENAI_BASE_URL=provider.base_url, OPENAI_API_KEY=api_key)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reliable_agent_appworld",
            "--log-level",
            "INFO",
        ],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_parts = []
    stderr_reader = threading.Thread(
        target=lambda: stderr_parts.append(process.stderr.read()), daemon=True
    )
    stderr_reader.start()
    protocol_lines = []

    def read_message(message_type):
        line = readline(process)
        protocol_lines.append(line)
        return message_type.model_validate_json(line)

    try:
        process.stdin.write(json.dumps(public_task(case)) + "\n")
        process.stdin.flush()
        bootstrap = read_message(ActionsMessage)
        assert bootstrap.call_id == "call-1"
        bootstrap_code = bootstrap.actions[0].arguments["code"]
        assert "Deterministic public API onboarding" in bootstrap_code
        assert f"objective = {json.dumps(case.objective)}" in bootstrap_code
        assert "access_tokens = {}" in bootstrap_code
        write_observation(
            process,
            bootstrap.call_id,
            "Public API schemas\nAuthenticated apps: ['spotify']\n"
            f"OPENAI_API_KEY={api_key}\n"
            f"access_token='{session_secret}'\n"
            f"id_token='{jwt_secret}'\n",
            api_key=api_key,
            access_token=session_secret,
            id_token=jwt_secret,
        )

        action = read_message(ActionsMessage)
        assert action.call_id == "call-2"
        assert action.actions[0].arguments["code"] == "print('subprocess-ok')\n"
        write_observation(process, action.call_id, "subprocess-ok\n")

        completion_action = read_message(ActionsMessage)
        assert completion_action.call_id == "call-3"
        assert len(completion_action.actions) == 1
        assert completion_action.actions[0].arguments["code"] == case.completion_code
        write_observation(
            process, completion_action.call_id, "Marked the active task complete.\n"
        )
        final = read_message(FinalMessage)
        assert final.completed is True
        assert final.answer == "subprocess-ok"
        assert final.confidence == 0.95
        assert final.likely_incorrect is False
        assert final.reason == "The public observation confirmed the printed output."
        assert process.wait(timeout=15) == 0
        assert process.stdout.read() == ""
        stderr_reader.join(timeout=5)
        assert not stderr_reader.is_alive()
        stdout = "".join(protocol_lines)
        stderr = "".join(stderr_parts)
        for line in protocol_lines:
            assert isinstance(json.loads(line), dict)
        for secret in secrets:
            assert secret not in stdout
            assert secret not in stderr
        assert len(provider.requests) == 2
        for request in provider.requests:
            assert request.get("seed") is None
            assert request["top_k"] == 20
            assert request["min_p"] == 0
            assert request["repetition_penalty"] == 1.0
            assert request.get("max_tokens", request.get("max_output_tokens")) == 1024
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        provider.close()
