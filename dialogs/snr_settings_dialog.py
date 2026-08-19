from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from core.settings import SNRSettings


class SNRSettingsDialog(QDialog):
    """Configure robust SNR estimation and bounded acquisition suggestions."""

    def __init__(self, settings: SNRSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SNR Settings")
        self.resize(520, 620)
        self._settings = replace(settings)

        root = QVBoxLayout(self)

        estimation = QGroupBox("Estimation")
        estimation_form = QFormLayout(estimation)

        self.enabled = QCheckBox()
        self.enabled.setChecked(bool(settings.enabled))
        self.signal_start = self._wavelength_spin(settings.signal_start_nm)
        self.signal_stop = self._wavelength_spin(settings.signal_stop_nm)
        self.noise1_start = self._wavelength_spin(settings.noise1_start_nm)
        self.noise1_stop = self._wavelength_spin(settings.noise1_stop_nm)

        self.use_noise2 = QCheckBox()
        self.use_noise2.setChecked(bool(settings.use_noise2))
        self.noise2_start = self._wavelength_spin(settings.noise2_start_nm)
        self.noise2_stop = self._wavelength_spin(settings.noise2_stop_nm)

        self.baseline_order = QComboBox()
        self.baseline_order.addItem("Constant", 0)
        self.baseline_order.addItem("Linear", 1)
        self.baseline_order.addItem("Quadratic", 2)
        index = self.baseline_order.findData(int(settings.baseline_order))
        self.baseline_order.setCurrentIndex(index if index >= 0 else 1)

        self.minimum_noise_pixels = QSpinBox()
        self.minimum_noise_pixels.setRange(5, 100_000)
        self.minimum_noise_pixels.setValue(int(settings.minimum_noise_pixels))

        self.peak_percentile = QDoubleSpinBox()
        self.peak_percentile.setRange(50.0, 100.0)
        self.peak_percentile.setDecimals(2)
        self.peak_percentile.setSingleStep(0.1)
        self.peak_percentile.setSuffix(" %")
        self.peak_percentile.setValue(float(settings.peak_percentile))

        self.update_every_n = QSpinBox()
        self.update_every_n.setRange(1, 100_000)
        self.update_every_n.setValue(int(settings.update_every_n_spectra))

        estimation_form.addRow("Enable SNR estimation", self.enabled)
        estimation_form.addRow("Signal start", self.signal_start)
        estimation_form.addRow("Signal stop", self.signal_stop)
        estimation_form.addRow("Noise 1 start", self.noise1_start)
        estimation_form.addRow("Noise 1 stop", self.noise1_stop)
        estimation_form.addRow("Use second noise interval", self.use_noise2)
        estimation_form.addRow("Noise 2 start", self.noise2_start)
        estimation_form.addRow("Noise 2 stop", self.noise2_stop)
        estimation_form.addRow("Baseline model", self.baseline_order)
        estimation_form.addRow("Minimum noise pixels", self.minimum_noise_pixels)
        estimation_form.addRow("Peak percentile", self.peak_percentile)
        estimation_form.addRow("Evaluate every N spectra", self.update_every_n)
        root.addWidget(estimation)

        suggestions = QGroupBox("Acquisition suggestion limits")
        suggestion_form = QFormLayout(suggestions)

        # Enable/disable auto-adjust
        self.auto_suggest = QCheckBox()
        self.auto_suggest.setChecked(bool(settings.auto_suggest_enabled))

        # Metric for auto-adjust
        self.snr_metric = QComboBox()
        self.snr_metric.addItem("Integrated SNR", "integrated")
        self.snr_metric.addItem("Peak SNR", "peak")
        index = self.snr_metric.findData(str(settings.recommendation_metric))
        self.snr_metric.setCurrentIndex(index if index >= 0 else 1)

        # Target SNR
        self.target_snr = QDoubleSpinBox()
        self.target_snr.setRange(1.0, 1.0e9)
        self.target_snr.setDecimals(2)
        self.target_snr.setValue(float(settings.target_snr))

        # Target percent of maximum
        self.target_peak_percent = QDoubleSpinBox()
        self.target_peak_percent.setRange(1.0, 95.0)
        self.target_peak_percent.setDecimals(1)
        self.target_peak_percent.setSuffix(" % full scale")
        self.target_peak_percent.setValue(100.0 * float(settings.target_peak_fraction))

        # Maximum integration time allowed
        self.max_integration_ms = QSpinBox()
        self.max_integration_ms.setRange(1, 86_400_000) # One day
        self.max_integration_ms.setSuffix(" ms")
        self.max_integration_ms.setValue(int(settings.maximum_integration_ms))

        # Maximum number of averages allowed
        self.max_averages = QSpinBox()
        self.max_averages.setRange(1, 1_000_000)
        self.max_averages.setValue(int(settings.maximum_averages))

        # Maximum total acquisition time
        self.max_total_s = QDoubleSpinBox()
        self.max_total_s.setRange(0.001, 86_400.0) # One day
        self.max_total_s.setDecimals(2)
        self.max_total_s.setSuffix(" s")
        self.max_total_s.setValue(float(settings.maximum_total_acquisition_s))

        # Maximum number of auto-adjust iterations
        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(1, 1000)
        self.max_iterations.setValue(int(settings.auto_adjust_max_iterations))
        
        # Auto-adjust target tolerance
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(1.0, 100.0)
        self.tolerance.setDecimals(1)
        self.tolerance.setSuffix(" %")
        self.tolerance.setValue(100 * float(settings.auto_adjust_tolerance_fraction))

        suggestion_form.addRow("Generate suggestions", self.auto_suggest)
        suggestion_form.addRow("Recommendation metric", self.snr_metric)
        suggestion_form.addRow("Target SNR", self.target_snr)
        suggestion_form.addRow("Target detector level", self.target_peak_percent)
        suggestion_form.addRow("Maximum integration", self.max_integration_ms)
        suggestion_form.addRow("Maximum averages", self.max_averages)
        suggestion_form.addRow("Maximum total acquisition", self.max_total_s)
        suggestion_form.addRow("Maximum auto-adjust iterations", self.max_iterations)
        suggestion_form.addRow("Target tolerance", self.tolerance)
        root.addWidget(suggestions)

        note = QLabel(
            "SNR is calculated from the unsmoothed spectrum. Acquisition suggestions "
            "should initially be displayed for review rather than applied automatically."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.use_noise2.toggled.connect(self._update_enabled_state)
        self.auto_suggest.toggled.connect(self._update_enabled_state)
        self._update_enabled_state()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _wavelength_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 100_000.0)
        spin.setDecimals(2)
        spin.setSingleStep(1.0)
        spin.setSuffix(" nm")
        spin.setValue(float(value))
        return spin

    def _update_enabled_state(self) -> None:
        use_noise2 = self.use_noise2.isChecked()
        self.noise2_start.setEnabled(use_noise2)
        self.noise2_stop.setEnabled(use_noise2)
        suggest = self.auto_suggest.isChecked()
        for widget in (
            self.target_snr,
            self.target_peak_percent,
            self.max_integration_ms,
            self.max_averages,
            self.max_total_s,
        ):
            widget.setEnabled(suggest)

    @staticmethod
    def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
        return max(a0, b0) < min(a1, b1)

    def _validate(self) -> str | None:
        intervals = {
            "signal": (self.signal_start.value(), self.signal_stop.value()),
            "noise 1": (self.noise1_start.value(), self.noise1_stop.value()),
        }
        if self.use_noise2.isChecked():
            intervals["noise 2"] = (self.noise2_start.value(), self.noise2_stop.value())

        for name, (start, stop) in intervals.items():
            if stop <= start:
                return f"{name.title()} stop wavelength must exceed its start wavelength."

        signal = intervals["signal"]
        for name, interval in intervals.items():
            if name != "signal" and self._overlap(*signal, *interval):
                return f"The signal interval overlaps {name}."
        return None

    def accept(self) -> None:
        error = self._validate()
        if error is not None:
            QMessageBox.warning(self, "Invalid SNR settings", error)
            return
        super().accept()

    def settings(self) -> SNRSettings:
        return replace(
            self._settings,
            enabled=bool(self.enabled.isChecked()),
            signal_start_nm=float(self.signal_start.value()),
            signal_stop_nm=float(self.signal_stop.value()),
            noise1_start_nm=float(self.noise1_start.value()),
            noise1_stop_nm=float(self.noise1_stop.value()),
            use_noise2=bool(self.use_noise2.isChecked()),
            noise2_start_nm=float(self.noise2_start.value()),
            noise2_stop_nm=float(self.noise2_stop.value()),
            baseline_order=int(self.baseline_order.currentData()),
            minimum_noise_pixels=int(self.minimum_noise_pixels.value()),
            peak_percentile=float(self.peak_percentile.value()),
            update_every_n_spectra=int(self.update_every_n.value()),
            target_snr=float(self.target_snr.value()),
            auto_suggest_enabled=bool(self.auto_suggest.isChecked()),
            recommendation_metric=str(self.snr_metric.currentData()),
            target_peak_fraction=float(self.target_peak_percent.value()) / 100.0,
            maximum_integration_ms=int(self.max_integration_ms.value()),
            maximum_averages=int(self.max_averages.value()),
            maximum_total_acquisition_s=float(self.max_total_s.value()),
            auto_adjust_tolerance_fraction=float(self.tolerance.value()) / 100.0,
            auto_adjust_max_iterations=int(self.max_iterations.value()),
        )
