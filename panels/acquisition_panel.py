# panels/acquisition_panel.py

from __future__ import annotations

import math

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
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
from panels.smoothing_preview import KernelPreview, SmoothingPreviewDialog


class AcquisitionPanel(QWidget):
    acquire_requested = Signal()
    background_requested = Signal()
    background_clear_requested = Signal()
    background_save_requested = Signal()
    background_plot_changed = Signal(bool)
    live_changed = Signal(bool)
    recommend_acquisition_requested = Signal()
    auto_tune_acquisition_requested = Signal()
    power_channels_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._acquiring = False
        self._sequence_owner: str | None = None
        self._dark_correction_supported = True
        self._nonlinearity_correction_supported = True

        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(5)
        form.setColumnStretch(3, 1)

        self.live_check = QCheckBox()
        self.live_check.setChecked(False)
        self.live_check.toggled.connect(self.live_changed.emit)

        self.integration_ms = QSpinBox()
        self.integration_ms.setRange(1, 600_000)
        self.integration_ms.setValue(100)
        self.integration_ms.setSuffix(" ms")
        # self.integration_ms.setMaximumWidth(112)

        self.averaging_mode_combo = QComboBox()
        self.averaging_mode_combo.addItem("Software averaging", "software")
        self.averaging_mode_combo.addItem("Device averaging if available", "device")

        self.subtract_background_check = QCheckBox()
        self.subtract_background_check.setChecked(False)

        self.measure_power_check = QCheckBox()
        self.measure_power_check.setChecked(True)
        self.measure_power_check.setToolTip(
            "Read the connected Newport immediately before and after each spectrum. "
            "Turn this off for maximum spectrometer throughput while keeping the "
            "power meter connected."
        )
        self.measure_power_check.toggled.connect(
            self._on_measure_power_toggled
        )

        self.power_channel_1_check = QCheckBox("Ch 1")
        self.power_channel_1_check.setChecked(True)
        self.power_channel_2_check = QCheckBox("Ch 2")
        for checkbox in (self.power_channel_1_check, self.power_channel_2_check):
            checkbox.setToolTip(
                "Associate this Newport channel with spectrum acquisitions. "
                "At least one channel is required while Measure power is enabled."
            )
            checkbox.toggled.connect(self._ensure_power_channel_selection)

        self.take_background_button = QPushButton("Take Background")
        self.take_background_button.clicked.connect(
            lambda _checked=False: self.background_requested.emit()
        )

        self.clear_background_button = QPushButton("Clear Background")
        self.clear_background_button.clicked.connect(
            lambda _checked=False: self.background_clear_requested.emit()
        )

        self.averages = QSpinBox()
        self.averages.setRange(1, 1000)
        self.averages.setValue(5)
        # self.averages.setMaximumWidth(90)

        self.boxcar_width = QSpinBox()
        self.boxcar_width.setRange(1, 501)
        self.boxcar_width.setValue(5)
        self.boxcar_width.setMaximumWidth(90)

        self.smoothing_window_combo = QComboBox()
        for label, value in (
            ("None", "none"),
            ("Boxcar", "boxcar"),
            ("Hann", "hann"),
            ("Hamming", "hamming"),
            ("Gaussian", "gaussian"),
            ("Triangle", "triangle"),
            ("Blackman", "blackman"),
            ("Cosine", "cosine"),
            ("Sine", "sine"),
        ):
            self.smoothing_window_combo.addItem(label, value)
        self.smoothing_boundary_combo = QComboBox()
        for label, value in (
            ("Nearest", "nearest"),
            ("Reflect", "reflect"),
            ("Wrap", "wrap"),
            ("Constant zero", "constant"),
        ):
            self.smoothing_boundary_combo.addItem(label, value)
        self.smoothing_preview = KernelPreview()
        self.smoothing_window_combo.currentIndexChanged.connect(
            self._update_smoothing_controls
        )
        self.boxcar_width.valueChanged.connect(self._update_smoothing_controls)
        self.smoothing_preview.clicked.connect(self._show_smoothing_preview)

        self.dark_check = QCheckBox()
        self.dark_check.setChecked(True)

        self.nonlinearity_check = QCheckBox()
        self.nonlinearity_check.setChecked(True)

        self.record_field = QCheckBox()
        self.record_field.setChecked(True)
        self.record_field.setToolTip(
            "When enabled, associate the entered magnetic field with acquired "
            "spectra, gated-series output, monitors, and automatic file names. "
            "A value of 0 mT is recorded; unchecked means not applicable."
        )

        self.field_input = QSpinBox()
        self.field_input.setRange(-100_000, 100_000)
        self.field_input.setSingleStep(10)
        self.field_input.setValue(0)
        self.field_input.setSuffix(" mT")
        self.field_input.setMaximumWidth(112)
        self.record_field.toggled.connect(self._apply_control_state)

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
        recommendation_row.setSpacing(5)

        live_row = QHBoxLayout()
        live_row.addWidget(QLabel("Live"))
        live_row.addWidget(self.live_check)
        live_row.addStretch(1)
        form.addLayout(live_row, 0, 0, 1, 4)
        self.save_background_button = QPushButton("Save background")
        self.save_background_button.clicked.connect(lambda: self.background_save_requested.emit())
        self.plot_background_check = QCheckBox("Plot background")
        self.plot_background_check.toggled.connect(self.background_plot_changed.emit)
        self._background_available = False
        background_row = QHBoxLayout()
        background_row.addWidget(self.take_background_button)
        background_row.addWidget(self.clear_background_button)
        background_row.addWidget(QLabel("Subtract"))
        background_row.addWidget(self.subtract_background_check)
        background_row.addStretch(1)
        background_row.setSpacing(5)
        form.addWidget(QLabel("Exposure"), 1, 0)
        form.addWidget(self.integration_ms, 1, 1)
        form.addWidget(QLabel("Averages"), 1, 2)
        form.addWidget(self.averages, 1, 3)
        form.addWidget(QLabel("Smoothing"), 2, 0)
        form.addWidget(self.smoothing_window_combo, 2, 1)
        smoothing_row = QHBoxLayout()
        smoothing_row.addWidget(QLabel("Width"))
        smoothing_row.addWidget(self.boxcar_width)
        smoothing_row.addWidget(self.smoothing_preview)
        smoothing_row.addStretch(1)
        form.addLayout(smoothing_row, 2, 2, 1, 2)
        form.addWidget(QLabel("SNR"), 3, 0)
        form.addWidget(self.snr_label, 3, 1, 1, 3)
        form.addWidget(QLabel("Tuning"), 4, 0)
        form.addLayout(recommendation_row, 4, 1, 1, 3)
        form.addWidget(QLabel("Background"), 5, 0)
        background_tools = QHBoxLayout()
        background_tools.addWidget(self.save_background_button)
        background_tools.addWidget(self.plot_background_check)
        background_layout = QVBoxLayout()
        background_layout.addLayout(background_row)
        background_layout.addLayout(background_tools)
        form.addLayout(background_layout, 5, 1, 1, 3)
        layout.addLayout(form)

        self.acquire_button = QPushButton("Take Spectrum")
        self.acquire_button.clicked.connect(
            lambda _checked=False: self.acquire_requested.emit()
        )
        layout.addWidget(self.acquire_button)

        self.advanced_group = QGroupBox("Advanced acquisition options")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_group_layout = QVBoxLayout(self.advanced_group)
        advanced_group_layout.setContentsMargins(6, 4, 6, 6)
        self.advanced_contents = QWidget()
        advanced_group_layout.addWidget(self.advanced_contents)
        advanced_layout = QGridLayout(self.advanced_contents)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setHorizontalSpacing(8)
        advanced_layout.setVerticalSpacing(5)
        advanced_layout.addWidget(QLabel("Averaging backend"), 0, 0)
        advanced_layout.addWidget(self.averaging_mode_combo, 0, 1, 1, 3)
        advanced_layout.addWidget(QLabel("Boundary"), 1, 0)
        advanced_layout.addWidget(self.smoothing_boundary_combo, 1, 1)
        correction_row = QHBoxLayout()
        correction_row.addWidget(QLabel("Electric dark"))
        correction_row.addWidget(self.dark_check)
        correction_row.addWidget(QLabel("Nonlinearity"))
        correction_row.addWidget(self.nonlinearity_check)
        correction_row.addStretch(1)
        advanced_layout.addLayout(correction_row, 2, 0, 1, 4)
        power_row = QHBoxLayout()
        power_row.addWidget(self.measure_power_check)
        power_row.addWidget(self.power_channel_1_check)
        power_row.addWidget(self.power_channel_2_check)
        power_row.addStretch(1)
        advanced_layout.addWidget(QLabel("Measure power"), 3, 0)
        advanced_layout.addLayout(power_row, 3, 1, 1, 3)
        field_row = QHBoxLayout()
        field_row.addWidget(self.record_field)
        field_row.addWidget(self.field_input)
        field_row.addStretch(1)
        advanced_layout.addWidget(QLabel("Magnetic field"), 4, 0)
        advanced_layout.addLayout(field_row, 4, 1, 1, 3)
        self.advanced_group.toggled.connect(self.advanced_contents.setVisible)
        self.advanced_contents.hide()
        layout.addWidget(self.advanced_group)

        layout.addStretch(1)
        self._update_smoothing_controls()
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
            field_value=float(self.field_input.value()) if self.record_field.isChecked() else None,
            run_identifier=str(run_identifier),
            notes=str(notes),
            smoothing_window=str(self.smoothing_window_combo.currentData()),
            smoothing_boundary=str(self.smoothing_boundary_combo.currentData()),
            averaging_mode=str(self.averaging_mode_combo.currentData()),
            subtract_background=bool(self.subtract_background_check.isChecked()),
            measure_power=bool(self.measure_power_check.isChecked()),
            power_channels=self.selected_power_channels(),
        )

    def selected_power_channels(self) -> tuple[int, ...]:
        return tuple(
            index
            for index, checkbox in enumerate(
                (self.power_channel_1_check, self.power_channel_2_check)
            )
            if checkbox.isChecked()
        )

    def _ensure_power_channel_selection(self, *_args) -> None:
        if self.measure_power_check.isChecked() and not self.selected_power_channels():
            self.power_channel_1_check.blockSignals(True)
            self.power_channel_1_check.setChecked(True)
            self.power_channel_1_check.blockSignals(False)
        self.power_channels_changed.emit(self.selected_power_channels())
        self._apply_control_state()

    def _on_measure_power_toggled(self, _enabled: bool) -> None:
        self._ensure_power_channel_selection()

    def _update_smoothing_controls(self, *_args) -> None:
        window = str(self.smoothing_window_combo.currentData() or "none")
        # All acquisition/sequence gating belongs to one implementation.
        self._apply_control_state()
        self.boxcar_width.setToolTip(
            "Gaussian width is FWHM in pixels. Other widths are window length in pixels."
        )
        self.smoothing_preview.set_kernel(window, int(self.boxcar_width.value()))

    def _show_smoothing_preview(self) -> None:
        dialog = SmoothingPreviewDialog(
            str(self.smoothing_window_combo.currentData() or "none"),
            int(self.boxcar_width.value()),
            self,
        )
        dialog.exec()

    def is_live_enabled(self) -> bool:
        return bool(self.live_check.isChecked())

    def set_live_enabled(self, enabled: bool) -> None:
        self.live_check.setChecked(bool(enabled))

    def set_acquiring(self, acquiring: bool) -> None:
        self._acquiring = bool(acquiring)
        self._apply_control_state()

    def set_background_available(self, available: bool) -> None:
        self._background_available = available
        if not available:
            self.subtract_background_check.setChecked(False)
            self.plot_background_check.setChecked(False)
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
            self.smoothing_window_combo,
            self.smoothing_boundary_combo,
            self.dark_check,
            self.nonlinearity_check,
            self.subtract_background_check,
            self.measure_power_check,
            self.power_channel_1_check,
            self.power_channel_2_check,
            self.record_field,
            self.advanced_group,
        ):
            widget.setEnabled(parameters_enabled)
        available = getattr(self, "_background_available", False)
        self.subtract_background_check.setEnabled(parameters_enabled and available)
        self.subtract_background_check.setToolTip("Capture a compatible background first." if not available else "Strict configuration matching is enforced.")
        self.save_background_button.setEnabled(available)
        self.plot_background_check.setEnabled(available)
        self.boxcar_width.setEnabled(
            parameters_enabled
            and str(self.smoothing_window_combo.currentData()) != "none"
        )
        for checkbox in (self.power_channel_1_check, self.power_channel_2_check):
            checkbox.setEnabled(
                parameters_enabled and self.measure_power_check.isChecked()
            )
        self.field_input.setEnabled(
            parameters_enabled and self.record_field.isChecked()
        )
        self.dark_check.setEnabled(
            parameters_enabled and self._dark_correction_supported
        )
        self.nonlinearity_check.setEnabled(
            parameters_enabled and self._nonlinearity_correction_supported
        )

    def set_correction_availability(
        self,
        *,
        electric_dark: bool,
        nonlinearity: bool,
    ) -> None:
        self._dark_correction_supported = bool(electric_dark)
        self._nonlinearity_correction_supported = bool(nonlinearity)
        if not self._dark_correction_supported:
            self.dark_check.setChecked(False)
            self.dark_check.setToolTip(
                "This correction is not exposed by the connected spectrometer adapter."
            )
        else:
            self.dark_check.setToolTip("")
        if not self._nonlinearity_correction_supported:
            self.nonlinearity_check.setChecked(False)
            self.nonlinearity_check.setToolTip(
                "This correction is not exposed by the connected spectrometer adapter."
            )
        else:
            self.nonlinearity_check.setToolTip("")
        self._apply_control_state()

    def set_integration_limits_us(
        self,
        min_us: int,
        max_us: int,
        *,
        minimum_frame_period_ms: float | None = None,
    ) -> None:
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

        tooltip = f"Allowed exposure range: {min_ms} to {max_ms} ms."
        if (
            minimum_frame_period_ms is not None
            and math.isfinite(float(minimum_frame_period_ms))
            and float(minimum_frame_period_ms) > 0.0
        ):
            tooltip += (
                " The current full-detector readout needs at least "
                f"{float(minimum_frame_period_ms):.6g} ms per frame; this is a "
                "throughput limit, not necessarily a minimum exposure."
            )
        self.integration_ms.setToolTip(tooltip)

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
                "Enabled"
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
            max(1, get_int(settings, "acquisition/boxcar_width", self.boxcar_width.value()))
        )
        smoothing_window = get_str(
            settings,
            "acquisition/smoothing_window",
            (
                "boxcar"
                if settings.contains("acquisition/boxcar_width")
                and get_int(settings, "acquisition/boxcar_width", 0) > 1
                else "none"
            ),
        )
        index = self.smoothing_window_combo.findData(smoothing_window)
        if index >= 0:
            self.smoothing_window_combo.setCurrentIndex(index)
        smoothing_boundary = get_str(
            settings,
            "acquisition/smoothing_boundary",
            "nearest",
        )
        index = self.smoothing_boundary_combo.findData(smoothing_boundary)
        if index >= 0:
            self.smoothing_boundary_combo.setCurrentIndex(index)
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
        self.measure_power_check.setChecked(
            get_bool(settings, "acquisition/measure_power", True)
        )
        self.record_field.setChecked(
            get_bool(settings, "acquisition/record_field", True)
        )
        self.power_channel_1_check.blockSignals(True)
        self.power_channel_2_check.blockSignals(True)
        self.power_channel_1_check.setChecked(
            get_bool(settings, "acquisition/power_channel_1", True)
        )
        self.power_channel_2_check.setChecked(
            get_bool(settings, "acquisition/power_channel_2", False)
        )
        self.power_channel_1_check.blockSignals(False)
        self.power_channel_2_check.blockSignals(False)
        self.advanced_group.setChecked(
            get_bool(settings, "acquisition/advanced_expanded", False)
        )
        self._ensure_power_channel_selection()
        self._update_smoothing_controls()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("acquisition/live_enabled", self.live_check.isChecked())
        settings.setValue("acquisition/integration_ms", self.integration_ms.value())
        settings.setValue("acquisition/averages", self.averages.value())
        settings.setValue("acquisition/boxcar_width", self.boxcar_width.value())
        settings.setValue(
            "acquisition/smoothing_window",
            str(self.smoothing_window_combo.currentData()),
        )
        settings.setValue(
            "acquisition/smoothing_boundary",
            str(self.smoothing_boundary_combo.currentData()),
        )
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
        settings.setValue(
            "acquisition/measure_power",
            self.measure_power_check.isChecked(),
        )
        settings.setValue(
            "acquisition/record_field",
            self.record_field.isChecked(),
        )
        settings.setValue(
            "acquisition/power_channel_1",
            self.power_channel_1_check.isChecked(),
        )
        settings.setValue(
            "acquisition/power_channel_2",
            self.power_channel_2_check.isChecked(),
        )
        settings.setValue(
            "acquisition/advanced_expanded",
            self.advanced_group.isChecked(),
        )
