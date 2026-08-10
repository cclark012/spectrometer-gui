from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


class SlidingRate:
    def __init__(self, window_s: float = 5.0) -> None:
        self.window_s = float(window_s)
        self.timestamps: deque[float] = deque()

    def mark(self) -> None:
        now = time.perf_counter()
        self.timestamps.append(now)
        self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s

        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def rate_hz(self) -> float:
        now = time.perf_counter()
        self._trim(now)

        if len(self.timestamps) < 2:
            return 0.0

        elapsed = self.timestamps[-1] - self.timestamps[0]

        if elapsed <= 0:
            return 0.0

        return (len(self.timestamps) - 1) / elapsed


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    acquisition_hz: float
    spectrum_draw_hz: float
    monitor_draw_hz: float
    power_draw_hz: float
    event_loop_lag_ms: float
    spectrum_draw_fraction: float
