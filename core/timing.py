from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


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
        logger.info(
            "[%s] total=%9.3f s, dt=%8.3f s, %s",
            self.label,
            now - self._t0,
            now - self._last,
            message,
        )
        self._last = now
