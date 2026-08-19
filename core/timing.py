from __future__ import annotations

import time


class StepTimer:
    """Small opt-in profiler for state-machine and hardware-I/O steps."""

    def __init__(self, label: str = "timer", enabled: bool = False) -> None:
        self.label = str(label)
        self.enabled = bool(enabled)
        self._t0 = time.perf_counter()
        self._last = self._t0

    def reset(self, label: str | None = None) -> None:
        if label is not None:
            self.label = str(label)
        self._t0 = time.perf_counter()
        self._last = self._t0

    def log(self, message: str) -> None:
        if not self.enabled:
            return

        now = time.perf_counter()
        print(
            f"[{self.label}] total={now - self._t0:9.3f} s, "
            f"dt={now - self._last:8.3f} s, {message}",
            flush=True,
        )
        self._last = now
