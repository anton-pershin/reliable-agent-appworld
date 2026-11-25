from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any, TextIO

from .config import AgentConfig

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_SECRET_KEY = re.compile(r"(api[_-]?key|authorization|password|secret|token)", re.IGNORECASE)
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_LOG_MARKER_RESERVE = len(b'{"event":"log_limit_reached"}\n')


class EventLogger:
    def __init__(
        self,
        episode_id: str,
        config: AgentConfig,
        stream: TextIO | None = None,
        clock=time.monotonic,
    ) -> None:
        self.episode_id = episode_id
        self.config = config
        self.stream = stream or sys.stderr
        self.clock = clock
        self.started = clock()
        self.event_index = 0
        self.bytes_emitted = 0
        self.suppressed_events = 0
        self._limit_recorded = False
        self._lock = threading.Lock()
        self._secrets = {
            os.environ[name]
            for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
            if os.environ.get(name)
        }

    def _redact(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "<depth-truncated>"
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "<redacted>")
            value = _BEARER.sub("Bearer <redacted>", value)
            limit = self.config.log_payload_chars
            if len(value) > limit:
                omitted = len(value) - limit
                value = value[:limit] + f"...<truncated {omitted} chars>"
            return value
        if isinstance(value, dict):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 50:
                    result["_truncated_items"] = len(value) - 50
                    break
                if _SECRET_KEY.search(str(key)):
                    result[str(key)] = "<redacted>"
                else:
                    result[str(key)] = self._redact(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            items = [self._redact(item, depth + 1) for item in value[:50]]
            if len(value) > 50:
                items.append(f"<truncated {len(value) - 50} items>")
            return items
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)

    def emit(self, event: str, level: str = "INFO", **fields: Any) -> None:
        if _LEVELS[level] < _LEVELS[self.config.log_level]:
            return
        with self._lock:
            if self._limit_recorded:
                self.suppressed_events += 1
                return
            self.event_index += 1
            payload = {
                "episode_id": self.episode_id,
                "event_index": self.event_index,
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(self.clock() - self.started, 6),
                "level": level,
                "event": event,
                "private_reasoning": "not-retained",
                **self._redact(fields),
            }
            encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if self.bytes_emitted + len(encoded) > self.config.max_log_bytes - _LOG_MARKER_RESERVE:
                self._limit_recorded = True
                self.suppressed_events += 1
                marker = {
                    "episode_id": self.episode_id[:100],
                    "event_index": self.event_index + 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": round(self.clock() - self.started, 6),
                    "level": "WARNING",
                    "event": "log_limit_reached",
                    "private_reasoning": "not-retained",
                }
                marker_bytes = (json.dumps(marker) + "\n").encode()
                if self.bytes_emitted + len(marker_bytes) > self.config.max_log_bytes:
                    marker_bytes = b'{"event":"log_limit_reached"}\n'
                if self.bytes_emitted + len(marker_bytes) <= self.config.max_log_bytes:
                    try:
                        self.stream.write(marker_bytes.decode())
                        self.stream.flush()
                        self.bytes_emitted += len(marker_bytes)
                    except OSError:
                        pass
                return
            try:
                self.stream.write(encoded.decode())
                self.stream.flush()
                self.bytes_emitted += len(encoded)
            except OSError:
                self._limit_recorded = True
