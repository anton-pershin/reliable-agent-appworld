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
_SECRET_KEY = re.compile(r"(api[_-]?key|authorization|password|secret)", re.IGNORECASE)
_TOKEN_KEYS = {"token", "access_token", "auth_token", "refresh_token", "id_token"}
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|access_token|auth_token|refresh_token|id_token)(\s*=\s*)(['\"])(.*?)(\3)"
)
_JSON_SECRET = re.compile(
    r"(?i)(['\"](?:password|access_token|auth_token|refresh_token|id_token)['\"]\s*:\s*['\"])(.*?)(['\"])"
)
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_LOG_MARKER_RESERVE = len(b'{"event":"log_limit_reached"}\n')
_STDERR_LIMIT_MARKER = b'{"event":"stderr_limit_reached"}\n'
_STDERR_MARKER_RESERVE = len(_STDERR_LIMIT_MARKER)


def _environment_secrets() -> set[str]:
    return {
        value
        for name, value in os.environ.items()
        if value
        and (
            _SECRET_KEY.search(name)
            or name.lower().replace("-", "_") in _TOKEN_KEYS
        )
    }


def _sanitize_string(value: str, secrets: set[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "<redacted>")
    value = _BEARER.sub("Bearer <redacted>", value)
    value = _INLINE_SECRET.sub(
        lambda match: match.group(1) + match.group(2) + match.group(3)
        + "<redacted>" + match.group(3),
        value,
    )
    return _JSON_SECRET.sub(
        lambda match: match.group(1) + "<redacted>" + match.group(3), value
    )


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
        self._secrets = _environment_secrets()

    def _redact(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "<depth-truncated>"
        if isinstance(value, str):
            value = _sanitize_string(value, self._secrets)
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
                normalized_key = str(key).lower().replace("-", "_")
                if _SECRET_KEY.search(str(key)) or normalized_key in _TOKEN_KEYS:
                    if isinstance(item, str) and item:
                        self._secrets.add(item)
                        register = getattr(self.stream, "register_secret", None)
                        if register is not None:
                            register(item)
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

    def redact_text(self, value: object) -> str:
        return self._redact(str(value))

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


class BoundedRedactingStream:
    def __init__(self, stream: TextIO, max_bytes: int = 900_000) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        self.bytes_written = 0
        self._secrets = _environment_secrets()
        self._limited = False
        self._lock = threading.Lock()

    def register_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def write(self, text: str) -> int:
        original_length = len(text)
        with self._lock:
            if self._limited:
                return original_length
            sanitized = _sanitize_string(str(text), self._secrets)
            encoded = sanitized.encode("utf-8", errors="replace")
            if self.bytes_written + len(encoded) > self.max_bytes - _STDERR_MARKER_RESERVE:
                self._limited = True
                marker = _STDERR_LIMIT_MARKER
                if self.bytes_written + len(marker) <= self.max_bytes:
                    self.stream.write(marker.decode())
                    self.bytes_written += len(marker)
                self.stream.flush()
                return original_length
            self.stream.write(sanitized)
            self.bytes_written += len(encoded)
        return original_length

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        return self.stream.isatty()

    @property
    def encoding(self) -> str:
        return getattr(self.stream, "encoding", "utf-8")
