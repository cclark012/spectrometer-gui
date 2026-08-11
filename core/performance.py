from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot


class SlidingRate:
    """Estimate event rate over a finite rolling time window."""

    def __init__(self, window_s: float = 5.0) -> None:
        if not math.isfinite(window_s) or window_s <= 0:
            raise ValueError("window_s must be finite and positive")
        self.window_s = float(window_s)
        self._timestamps: deque[float] = deque()

    def clear(self) -> None:
        self._timestamps.clear()

    def mark(self, timestamp_s: float | None = None) -> None:
        now = time.perf_counter() if timestamp_s is None else float(timestamp_s)
        self._timestamps.append(now)
        self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def rate_hz(self, now_s: float | None = None) -> float:
        now = time.perf_counter() if now_s is None else float(now_s)
        self._trim(now)
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / elapsed

    def count(self, now_s: float | None = None) -> int:
        now = time.perf_counter() if now_s is None else float(now_s)
        self._trim(now)
        return len(self._timestamps)


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    acquisition_hz: float
    spectrum_draw_hz: float
    monitor_draw_hz: float
    power_draw_hz: float
    event_loop_lag_ms: float
    spectrum_draw_fraction: float

    def format_status(self) -> str:
        fraction = (
            f"{100.0 * self.spectrum_draw_fraction:.0f}%"
            if math.isfinite(self.spectrum_draw_fraction)
            else "--"
        )
        return (
            f"Acq {self.acquisition_hz:.2f} Hz | "
            f"Spec {self.spectrum_draw_hz:.2f} FPS | "
            f"Mon {self.monitor_draw_hz:.2f} FPS | "
            f"Pwr {self.power_draw_hz:.2f} FPS | "
            f"UI lag {self.event_loop_lag_ms:.1f} ms | "
            f"Drawn {fraction}"
        )


class PerformanceMonitor(QObject):
    """Collect acquisition/redraw rates and coarse Qt event-loop latency."""

    updated = Signal(object)

    def __init__(
        self,
        *,
        enabled: bool = True,
        rate_window_s: float = 5.0,
        report_interval_ms: int = 1000,
        probe_interval_ms: int = 250,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.enabled = bool(enabled)
        self._rate_window_s = float(rate_window_s)
        self._acquisition = SlidingRate(rate_window_s)
        self._spectrum = SlidingRate(rate_window_s)
        self._monitor = SlidingRate(rate_window_s)
        self._power = SlidingRate(rate_window_s)
        self._lag_ms = 0.0
        self._expected_probe_s = time.perf_counter()

        self._report_timer = QTimer(self)
        self._report_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._report_timer.setInterval(max(100, int(report_interval_ms)))
        self._report_timer.timeout.connect(self._emit_snapshot)

        self._probe_timer = QTimer(self)
        self._probe_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._probe_timer.setInterval(max(20, int(probe_interval_ms)))
        self._probe_timer.timeout.connect(self._probe_event_loop)

        self._restart_timers()

    def configure(
        self,
        *,
        enabled: bool,
        rate_window_s: float,
        report_interval_ms: int,
        probe_interval_ms: int,
    ) -> None:
        self.enabled = bool(enabled)
        if not math.isfinite(rate_window_s) or rate_window_s <= 0:
            rate_window_s = 5.0
        if float(rate_window_s) != self._rate_window_s:
            self._rate_window_s = float(rate_window_s)
            self._acquisition = SlidingRate(rate_window_s)
            self._spectrum = SlidingRate(rate_window_s)
            self._monitor = SlidingRate(rate_window_s)
            self._power = SlidingRate(rate_window_s)
        self._report_timer.setInterval(max(100, int(report_interval_ms)))
        self._probe_timer.setInterval(max(20, int(probe_interval_ms)))
        self._restart_timers()

    def _restart_timers(self) -> None:
        self._report_timer.stop()
        self._probe_timer.stop()
        if not self.enabled:
            return
        self._expected_probe_s = (
            time.perf_counter() + self._probe_timer.interval() / 1000.0
        )
        self._report_timer.start()
        self._probe_timer.start()

    @Slot()
    def mark_acquisition(self) -> None:
        if self.enabled:
            self._acquisition.mark()

    @Slot()
    def mark_spectrum_redraw(self) -> None:
        if self.enabled:
            self._spectrum.mark()

    @Slot()
    def mark_monitor_redraw(self) -> None:
        if self.enabled:
            self._monitor.mark()

    @Slot()
    def mark_power_redraw(self) -> None:
        if self.enabled:
            self._power.mark()

    @Slot()
    def _probe_event_loop(self) -> None:
        now = time.perf_counter()
        lag_ms = max(0.0, 1000.0 * (now - self._expected_probe_s))
        # Exponential smoothing keeps the label readable while still exposing stalls.
        self._lag_ms = 0.80 * self._lag_ms + 0.20 * lag_ms
        interval_s = self._probe_timer.interval() / 1000.0
        expected = self._expected_probe_s + interval_s
        # Do not carry a large stall forward indefinitely.
        self._expected_probe_s = now + interval_s if now - expected > 5 * interval_s else expected

    def snapshot(self) -> PerformanceSnapshot:
        now = time.perf_counter()
        acquisition_hz = self._acquisition.rate_hz(now)
        spectrum_hz = self._spectrum.rate_hz(now)
        fraction = (
            min(1.0, spectrum_hz / acquisition_hz)
            if acquisition_hz > 0
            else float("nan")
        )
        return PerformanceSnapshot(
            acquisition_hz=acquisition_hz,
            spectrum_draw_hz=spectrum_hz,
            monitor_draw_hz=self._monitor.rate_hz(now),
            power_draw_hz=self._power.rate_hz(now),
            event_loop_lag_ms=float(self._lag_ms),
            spectrum_draw_fraction=float(fraction),
        )

    @Slot()
    def _emit_snapshot(self) -> None:
        if self.enabled:
            self.updated.emit(self.snapshot())
