from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reliable_agent_appworld.contracts import ActionsMessage, FinalMessage


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
                action = len(provider.requests) == 1
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
                            "arguments": {"code": "print('subprocess-ok')"},
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


def public_task():
    return {
        "protocol_version": 1,
        "type": "task",
        "episode_id": "subprocess-episode",
        "task": {
            "id": "public-task",
            "instructions": "Use execute_code, then return the final response.",
            "input": json.dumps(
                {
                    "instruction": "Print subprocess-ok.",
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


def test_installed_style_subprocess_protocol_has_no_provider_banner():
    provider = FakeProvider()
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(root / "src"),
            "OPENAI_BASE_URL": provider.base_url,
            "OPENAI_API_KEY": "local",
        }
    )
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
        cwd=root,
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
        raw_action = readline(process)
        action = ActionsMessage.model_validate_json(raw_action)
        assert action.call_id == "call-1"
        assert action.actions[0].name == "execute_code"
        process.stdin.write(
            json.dumps(
                {
                    "protocol_version": 1,
                    "type": "observation",
                    "call_id": action.call_id,
                    "results": [
                        {
                            "status": "ok",
                            "observation": {"output": "subprocess-ok\n"},
                            "error": None,
                        }
                    ],
                }
            )
            + "\n"
        )
        process.stdin.flush()
        final = FinalMessage.model_validate_json(readline(process))
        assert final.completed is True
        assert final.likely_incorrect is False
        assert process.wait(timeout=15) == 0
        assert process.stdout.read() == ""
        assert len(provider.requests) == 2
        for request in provider.requests:
            assert request.get("seed") is None
            assert request["top_k"] == 20
            assert request["min_p"] == 0
            assert request["repetition_penalty"] == 1.0
            assert request.get("max_tokens", request.get("max_output_tokens")) == 4096
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        provider.close()
