from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.auto_acquisition_records import AutoAcquisitionResult
from core.records import SpectrumRecord
from core.settings import AcquisitionSettings, SNRSettings
from processing.snr import selected_snr, suggest_acquisition


class AutoAcquisitionCoordinator(QObject):
    """Bounded event-driven integration/averaging adjustment.

    The coordinator lives in the GUI thread. It never calls hardware directly; it
    emits a request for the acquisition panel to apply settings and a request for
    MainWindow to start a spectrum. Every suggested change is verified by a real
    acquisition before another adjustment is made.
    """

    apply_settings_requested = Signal(int, int)
    spectrum_requested = Signal()
    snr_settings_requested = Signal(object)
    status_requested = Signal(str, int)
    active_changed = Signal(bool)
    completed = Signal(object)
    failed = Signal(str)
    aborted = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._iterations = 0
        self._seen_settings: set[tuple[int, int]] = set()
        self._minimum_integration_ms = 1
        self._maximum_integration_ms = 600_000
        self._current_integration_ms = 1
        self._current_averages = 1
        self._settings = SNRSettings()
        self._original_snr_settings = SNRSettings()
        self._last_result: SpectrumRecord | None = None

    @property
    def active(self) -> bool:
        return self._active

    @Slot()
    def abort(self) -> None:
        if not self._active:
            return
        self._restore_snr_settings()
        self._set_active(False)
        self.status_requested.emit("Automatic acquisition tuning aborted.", 10_000)
        self.aborted.emit()

    def start(
        self,
        *,
        current_settings: AcquisitionSettings,
        snr_settings: SNRSettings,
        minimum_integration_ms: int,
        maximum_integration_ms: int,
        initial_record: SpectrumRecord | None = None,
    ) -> None:
        if self._active:
            return

        self._settings = replace(snr_settings)
        self._original_snr_settings = replace(snr_settings)
        self._minimum_integration_ms = max(1, int(minimum_integration_ms))
        self._maximum_integration_ms = max(
            self._minimum_integration_ms,
            min(
                int(maximum_integration_ms),
                int(self._settings.maximum_integration_ms),
            ),
        )
        self._current_integration_ms = max(1, int(current_settings.integration_ms))
        self._current_averages = max(1, int(current_settings.averages))
        self._iterations = 0
        self._seen_settings = {
            (self._current_integration_ms, self._current_averages)
        }
        self._last_result = None
        self._set_active(True)

        # Auto-tuning requires an SNR value for every verification acquisition.
        worker_settings = replace(
            self._settings,
            enabled=True,
            update_every_n_spectra=1,
        )
        self.snr_settings_requested.emit(worker_settings)
        self.status_requested.emit(
            "Automatic acquisition tuning started.",
            10_000,
        )

        if self._record_matches_current_settings(initial_record):
            QTimer.singleShot(0, lambda: self.handle_spectrum_ready(initial_record))
        else:
            QTimer.singleShot(0, self.spectrum_requested.emit)

    def _record_matches_current_settings(
        self,
        record: SpectrumRecord | None,
    ) -> bool:
        return bool(
            record is not None
            and record.snr is not None
            and record.snr.valid
            and int(record.integration_ms) == self._current_integration_ms
            and int(record.averages) == self._current_averages
        )

    @Slot(object)
    def handle_spectrum_ready(self, record: SpectrumRecord) -> bool:
        """Consume a verification spectrum.

        Returns ``True`` when the record belonged to an active auto-tune run.
        MainWindow can use this return value to suppress unrelated live chaining.
        """

        if not self._active:
            return False

        self._last_result = record
        self._current_integration_ms = max(1, int(record.integration_ms))
        self._current_averages = max(1, int(record.averages))

        if record.snr is None:
            self._fail("The verification spectrum did not contain an SNR estimate.")
            return True
        if not record.snr.valid:
            self._fail(f"SNR estimation failed: {record.snr.message}")
            return True

        try:
            achieved_snr = selected_snr(
                record.snr,
                self._settings.recommendation_metric,
            )
        except Exception as exc:
            self._fail(str(exc))
            return True

        peak_fraction = float(record.snr.peak_fraction_of_full_scale)
        if not math.isfinite(achieved_snr) or achieved_snr <= 0:
            self._fail("The selected SNR metric is non-finite or non-positive.")
            return True

        tolerance = max(0.0, float(self._settings.auto_adjust_tolerance_fraction))
        target = max(0.1, float(self._settings.target_snr))
        snr_reached = achieved_snr >= target * (1.0 - tolerance)
        safely_below_saturation = (
            not math.isfinite(peak_fraction) or peak_fraction <= 0.90
        )

        if snr_reached and safely_below_saturation:
            self._complete(
                message=(
                    f"Target SNR reached: {achieved_snr:.3g} "
                    f"at {self._current_integration_ms} ms × "
                    f"{self._current_averages}."
                ),
                achieved_snr=achieved_snr,
                peak_fraction=peak_fraction,
                limit_reached=False,
            )
            return True

        if self._iterations >= max(1, int(self._settings.auto_adjust_max_iterations)):
            self._complete(
                message=(
                    "Automatic tuning stopped at the configured iteration limit."
                ),
                achieved_snr=achieved_snr,
                peak_fraction=peak_fraction,
                limit_reached=True,
            )
            return True

        suggestion = suggest_acquisition(
            result=record.snr,
            metric=self._settings.recommendation_metric,
            current_integration_ms=self._current_integration_ms,
            current_averages=self._current_averages,
            target_snr=target,
            target_peak_fraction=self._settings.target_peak_fraction,
            minimum_integration_ms=self._minimum_integration_ms,
            maximum_integration_ms=self._maximum_integration_ms,
            maximum_averages=self._settings.maximum_averages,
            maximum_total_acquisition_s=self._settings.maximum_total_acquisition_s,
        )

        next_pair = (int(suggestion.integration_ms), int(suggestion.averages))
        if not suggestion.changed or next_pair == (
            self._current_integration_ms,
            self._current_averages,
        ):
            self._complete(
                message=(
                    "Automatic tuning cannot improve the settings within the "
                    f"configured limits ({suggestion.limiting_reason})."
                ),
                achieved_snr=achieved_snr,
                peak_fraction=peak_fraction,
                limit_reached=True,
            )
            return True

        if next_pair in self._seen_settings:
            self._complete(
                message=(
                    "Automatic tuning stopped because the settings began to repeat."
                ),
                achieved_snr=achieved_snr,
                peak_fraction=peak_fraction,
                limit_reached=True,
            )
            return True

        self._seen_settings.add(next_pair)
        self._iterations += 1
        self._current_integration_ms, self._current_averages = next_pair
        self.status_requested.emit(
            (
                f"Auto tune iteration {self._iterations}: applying "
                f"{next_pair[0]} ms × {next_pair[1]} averages; "
                f"predicted SNR {suggestion.predicted_snr:.3g}."
            ),
            15_000,
        )
        self.apply_settings_requested.emit(*next_pair)
        QTimer.singleShot(0, self.spectrum_requested.emit)
        return True

    @Slot(str)
    def handle_acquisition_failed(self, message: str) -> None:
        if self._active:
            summary = self._last_line(message)
            self._fail(f"Verification acquisition failed: {summary}")

    @staticmethod
    def _last_line(message: str) -> str:
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        return lines[-1] if lines else "unknown error"

    def _set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.active_changed.emit(self._active)

    def _restore_snr_settings(self) -> None:
        self.snr_settings_requested.emit(replace(self._original_snr_settings))

    def _complete(
        self,
        *,
        message: str,
        achieved_snr: float,
        peak_fraction: float,
        limit_reached: bool,
    ) -> None:
        result = AutoAcquisitionResult(
            success=not limit_reached,
            message=str(message),
            iterations=int(self._iterations),
            integration_ms=int(self._current_integration_ms),
            averages=int(self._current_averages),
            achieved_snr=float(achieved_snr),
            peak_fraction=float(peak_fraction),
            limit_reached=bool(limit_reached),
        )
        self._restore_snr_settings()
        self._set_active(False)
        self.status_requested.emit(message, 15_000)
        self.completed.emit(result)

    def _fail(self, message: str) -> None:
        self._restore_snr_settings()
        self._set_active(False)
        self.status_requested.emit(message, 15_000)
        self.failed.emit(str(message))
