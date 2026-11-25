from __future__ import annotations

import time


class EpisodeDeadline:
    def __init__(self, timeout_seconds: float, clock=time.monotonic) -> None:
        self.clock = clock
        self.started_at = clock()
        self.ends_at = self.started_at + timeout_seconds

    def remaining(self) -> float:
        return max(0.0, self.ends_at - self.clock())
