# panels/acquisition_panel.py

from __future__ import annotations

import math

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.preferences import get_bool, get_int, get_str
from core.settings import AcquisitionSettings
from core.snr_records import SNRMetrics


class AcquisitionPanel(QWidget):
    acquire_requested = Signal()
    background_requested = Signal()
    background_clear_requested = Signal()
    live_changed = Signal(bool)
    recommend_acquisition_requested = Signal()
    auto_tune_acquisition_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._acquiring = False
        self._sequence_owner: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.live_check = QCheckBox()
        self.live_check.setChecked(False)
        self.live_check.toggled.connect(self.live_changed.emit)

        self.integration_ms = QSpinBox()
        self.integration_ms.setRange(1, 600_000)
        self.integration_ms.setValue(100)
        self.integration_ms.setSuffix(" ms")

        self.averaging_mode_combo = QComboBox()
        self.averaging_mode_combo.addItem("Software averaging", "software")
        self.averaging_mode_combo.addItem("Device averaging if available", "device")

        self.subtract_background_check = QCheckBox()
        self.subtract_background_check.setChecked(False)

        self.take_background_button = QPushButton("Take Background")
        self.take_background_button.clicked.connect(
            lambda _checked=False: self.background_requested.emit()
        )

        self.clear_background_button = QPushButton("Clear Background")
        self.clear_background_button.clicked.connect(
            lambda _checked=False: self.background_clear_requested.emit()
        )

        background_row = QHBoxLayout()
        background_row.addWidget(self.take_background_button, stretch=1)
        background_row.addWidget(self.clear_background_button, stretch=1)

        self.averages = QSpinBox()
        self.averages.setRange(1, 1000)
        self.averages.setValue(5)

        self.boxcar_width = QSpinBox()
        self.boxcar_width.setRange(0, 501)
        self.boxcar_width.setValue(0)

        self.dark_check = QCheckBox()
        self.dark_check.setChecked(True)

        self.nonlinearity_check = QCheckBox()
        self.nonlinearity_check.setChecked(True)

        self.field_input = QSpinBox()
        self.field_input.setRange(-100_000, 100_000)
        self.field_input.setSingleStep(10)
        self.field_input.setValue(0)
        self.field_input.setSuffix(" mT")

        self._snr_enabled = False

        self.snr_label = QLabel("Disabled")
        self.snr_label.setToolTip(
            "SNR estimation is disabled"
        )

        self.recommend_button = QPushButton("Recommend")
        self.recommend_button.setToolTip(
            "Recommend integration time and averaging "
            "from the most recent valid SNR estimate."
        )
        self.recommend_button.clicked.connect(
            self.recommend_acquisition_requested.emit
        )

        self.auto_tune_button = QPushButton("Auto Tune")
        self.auto_tune_button.setToolTip(
            "Acquire verification spectra and adjust "
            "integration time / averaging toward the "
            "configured target SNR."
        )
        self.auto_tune_button.clicked.connect(
            self.auto_tune_acquisition_requested.emit
        )

        recommendation_row = QHBoxLayout()
        recommendation_row.addWidget(self.recommend_button)
        recommendation_row.addWidget(self.auto_tune_button)

        live_label = QLabel("Live")
        electric_dark_label = QLabel("Electric Dark")
        nonlinearity_label = QLabel("Nonlinearity")
        sub_background_label = QLabel("Subtract Background")
        labels = [
            live_label, electric_dark_label, nonlinearity_label, sub_background_label
        ]

        checks = [
            self.live_check, self.dark_check,
            self.nonlinearity_check, self.subtract_background_check
        ]

        options = QHBoxLayout()
        for label, check in zip(labels, checks, strict=True):
            options.addWidget(label)
            options.addWidget(check)

        # average_mode_label = QLabel("Averaging mode")
        averages_label = QLabel("Averages")
        boxcar_label = QLabel("Boxcar width")
        
        smoothing_labels = [averages_label, boxcar_label]
        smoothing_values = [self.averages, self.boxcar_width]
        smoothing_options = QHBoxLayout()

        for label, value in zip(smoothing_labels, smoothing_values, strict=True):
            smoothing_options.addWidget(label)
            smoothing_options.addWidget(value)
        

        form.addRow(options)
        # form.addRow("Background", self.take_background_button)
        # form.addRow("", self.clear_background_button)
        form.addRow(background_row)
        form.addRow("Integration time", self.integration_ms)
        form.addRow("Averaging mode", self.averaging_mode_combo)
        form.addRow("Averages", self.averages)
        form.addRow("Boxcar width", self.boxcar_width)
        # form.addRow(smoothing_options)
        form.addRow("Magnetic field", self.field_input)
        form.addRow("SNR", self.snr_label)
        form.addRow("Acquisition tuning", recommendation_row)

        layout.addLayout(form)

        self.acquire_button = QPushButton("Take Spectrum")
        self.acquire_button.clicked.connect(
            lambda _checked=False: self.acquire_requested.emit()
        )
        layout.addWidget(self.acquire_button)

        layout.addStretch(1)
        self._apply_control_state()

    def settings(self, *, run_identifier: str = "", notes: str = "") -> AcquisitionSettings:
        integration_ms = int(self.integration_ms.value())
        integration_ms = min(
            max(integration_ms, int(self.integration_ms.minimum())),
            int(self.integration_ms.maximum()),
        )
        return AcquisitionSettings(
            integration_ms=int(integration_ms),
            averages=int(self.averages.value()),
            boxcar_width=int(self.boxcar_width.value()),
            correct_dark=bool(self.dark_check.isChecked()),
            correct_nonlinearity=bool(self.nonlinearity_check.isChecked()),
            field_value=float(self.field_input.value()),
            run_identifier=str(run_identifier),
            notes=str(notes),
            averaging_mode=str(self.averaging_mode_combo.currentData()),
            subtract_background=bool(self.subtract_background_check.isChecked()),
        )

    def is_live_enabled(self) -> bool:
        return bool(self.live_check.isChecked())

    def set_live_enabled(self, enabled: bool) -> None:
        self.live_check.setChecked(bool(enabled))

    def set_acquiring(self, acquiring: bool) -> None:
        self._acquiring = bool(acquiring)
        self._apply_control_state()

    def set_sequence_owner(self, owner: str | None) -> None:
        self._sequence_owner = str(owner) if owner is not None else None
        self._apply_control_state()

    def _apply_control_state(self) -> None:
        owner = self._sequence_owner
        acquiring = self._acquiring
        idle = owner is None and not acquiring

        self.acquire_button.setEnabled(idle)
        self.take_background_button.setEnabled(idle)
        self.clear_background_button.setEnabled(idle)
        self.recommend_button.setEnabled(idle)

        # Live stays clickable during a live frame so the user can stop the
        # chain. Auto Tune likewise remains clickable while it owns the lease
        # so its button can act as Abort.
        self.live_check.setEnabled(owner in {None, "live"})
        self.auto_tune_button.setEnabled(
            owner in {None, "auto_tune"}
            and (not acquiring or owner == "auto_tune")
        )

        parameters_enabled = not acquiring and owner in {None, "live"}
        for widget in (
            self.integration_ms,
            self.averaging_mode_combo,
            self.averages,
            self.boxcar_width,
            self.dark_check,
            self.nonlinearity_check,
            self.subtract_background_check,
            self.field_input,
        ):
            widget.setEnabled(parameters_enabled)

    def set_integration_limits_us(self, min_us: int, max_us: int) -> None:
        min_us = int(min_us or 0)
        max_us = int(max_us or 0)

        if min_us <= 0 or max_us <= 0 or max_us <= min_us:
            # Keep conservative defaults if capabilities are unavailable.
            return

        min_ms = max(1, int(math.ceil(min_us / 1000.0)))
        max_ms = max(min_ms, int(math.floor(max_us / 1000.0)))

        old_value = int(self.integration_ms.value())

        self.integration_ms.setRange(min_ms, max_ms)
        self.integration_ms.setValue(min(max(old_value, min_ms), max_ms))

        self.integration_ms.setToolTip(
            f"Allowed integration range: {min_ms} to {max_ms} ms"
        )

    def set_snr(
        self,
        result: SNRMetrics | None,
    ) -> None:
        if not self._snr_enabled:
            return

        # None means this spectrum was skipped because the user selected
        # evaluation every N spectra. Retain the previous valid result.
        if result is None:
            return

        if not result.valid:
            self.snr_label.setText("Unavailable")
            self.snr_label.setToolTip(result.message)
            return

        self.snr_label.setText(
            f"Peak {result.peak_snr:.2f} | "
            f"Area {result.integrated_snr:.2f}"
        )

        self.snr_label.setToolTip(
            f"Noise sigma: "
            f"{result.noise_sigma_counts:.4g} counts\n"
            f"Signal pixels: {result.n_signal_pixels}\n"
            f"Noise pixels: {result.n_noise_pixels}\n"
            f"Peak fraction: "
            f"{100.0 * result.peak_fraction_of_full_scale:.2f}%"
        )

    def set_snr_enabled(self, enabled: bool) -> None:
        self._snr_enabled = bool(enabled)

        if self._snr_enabled:
            self.snr_label.setText(
                "Enabled — waiting for spectrum"
            )
            self.snr_label.setToolTip(
                "SNR estimation is enabled. "
                "A result will appear after the next "
                "scheduled SNR evaluation."
            )
        else:
            self.snr_label.setText("Disabled")
            self.snr_label.setToolTip(
                "SNR estimation is disabled"
            )

    def set_acquisition_parameters(
        self,
        *,
        integration_ms: int,
        averages: int,
    ) -> None:
        self.integration_ms.setValue(
            int(integration_ms)
        )
        self.averages.setValue(
            int(averages)
        )

    def set_auto_tuning(self, active: bool) -> None:
        self.auto_tune_button.setText(
            "Stop Auto Tune" if active else "Auto Tune"
        )
        self._apply_control_state()

    def load_preferences(self, settings: QSettings) -> None:
        self.live_check.setChecked(
            get_bool(settings, "acquisition/live_enabled", False)
        )
        self.integration_ms.setValue(
            get_int(settings, "acquisition/integration_ms", self.integration_ms.value())
        )
        self.averages.setValue(
            get_int(settings, "acquisition/averages", self.averages.value())
        )
        self.boxcar_width.setValue(
            get_int(settings, "acquisition/boxcar_width", self.boxcar_width.value())
        )
        self.dark_check.setChecked(
            get_bool(settings, "acquisition/correct_dark", self.dark_check.isChecked())
        )
        self.nonlinearity_check.setChecked(
            get_bool(
                settings,
                "acquisition/correct_nonlinearity",
                self.nonlinearity_check.isChecked(),
            )
        )
        self.field_input.setValue(
            get_int(settings, "acquisition/field_mT", self.field_input.value())
        )
        avg_mode = get_str(settings, "acquisition/averaging_mode", "software")
        index = self.averaging_mode_combo.findData(avg_mode)
        if index >= 0:
            self.averaging_mode_combo.setCurrentIndex(index)

        self.subtract_background_check.setChecked(
            get_bool(settings, "acquisition/subtract_background", False)
        )

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("acquisition/live_enabled", self.live_check.isChecked())
        settings.setValue("acquisition/integration_ms", self.integration_ms.value())
        settings.setValue("acquisition/averages", self.averages.value())
        settings.setValue("acquisition/boxcar_width", self.boxcar_width.value())
        settings.setValue("acquisition/correct_dark", self.dark_check.isChecked())
        settings.setValue(
            "acquisition/correct_nonlinearity",
            self.nonlinearity_check.isChecked(),
        )
        settings.setValue("acquisition/field_mT", self.field_input.value())
        settings.setValue(
            "acquisition/averaging_mode",
            str(self.averaging_mode_combo.currentData()),
        )
        settings.setValue(
            "acquisition/subtract_background",
            self.subtract_background_check.isChecked(),
        )
