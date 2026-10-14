from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.gated_acquisition import GatedAcquisitionSettings, GatedPlan
from core.preferences import get_bool, get_float, get_int, get_str


class GatedAcquisitionPanel(QWidget):
    preview_requested = Signal()
    run_requested = Signal()
    abort_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = False
        self._external_busy = False
        self._spectrometer_available = False
        self._lasers_available = False

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        common = QGridLayout()
        common.setColumnStretch(1, 1)
        common.setColumnStretch(3, 1)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("ON/OFF comparison", "on_off_pair")
        self.mode_combo.addItem("Fixed delays after switch-off", "delayed_after_off")
        self.mode_combo.addItem("Rapid before/after series", "transition_series")
        self.mode_combo.addItem("Interleaved decay curve", "interleaved_decay")
        self.mode_combo.currentIndexChanged.connect(self._update_mode_visibility)

        self.output_combo = QComboBox()
        self.output_combo.addItem("Individual spectrum files", "individual_frames")
        self.output_combo.addItem("One averaged series file", "averaged_series")

        self.timing_guard_combo = QComboBox()
        self.timing_guard_combo.addItem(
            "Automatic (flag files / discard averages)",
            "auto",
        )
        self.timing_guard_combo.addItem("Discard timing outliers", "discard")
        self.timing_guard_combo.addItem("Flag timing outliers", "flag")
        self.timing_guard_combo.addItem(
            "Retain N inside fixed window",
            "retain",
        )
        self.timing_guard_combo.addItem("Timing guard off", "off")
        self.timing_guard_combo.setToolTip(
            "Robust modes classify statistical outliers. Retain mode uses an "
            "explicit two-sided millisecond window and repeats complete pump/OFF "
            "attempts until every averaged trace reaches the requested count or "
            "a finite attempt limit."
        )
        self.timing_guard_combo.currentIndexChanged.connect(
            self._update_timing_guard_state
        )

        self.cycles = self._spin(1, 10_000, 1)
        self.inter_frame_gap_ms = self._spin(0, 3_600_000, 0, " ms")
        self.inter_repeat_gap_ms = self._spin(0, 3_600_000, 0, " ms")
        self.inter_frame_gap_ms.setToolTip(
            "Minimum pause between consecutive spectrum acquisitions inside one block."
        )
        self.inter_repeat_gap_ms.setToolTip(
            "Pause after a complete measurement block before the next repeat begins."
        )

        common.addWidget(QLabel("Mode"), 0, 0)
        common.addWidget(self.mode_combo, 0, 1, 1, 3)
        common.addWidget(QLabel("Output"), 1, 0)
        common.addWidget(self.output_combo, 1, 1, 1, 3)
        common.addWidget(QLabel("Repeats"), 2, 0)
        common.addWidget(self.cycles, 2, 1)
        common.addWidget(QLabel("Repeat gap"), 2, 2)
        common.addWidget(self.inter_repeat_gap_ms, 2, 3)
        common.addWidget(QLabel("Frame gap"), 3, 0)
        common.addWidget(self.inter_frame_gap_ms, 3, 1)
        common.addWidget(QLabel("Timing quality"), 3, 2)
        common.addWidget(self.timing_guard_combo, 3, 3)
        layout.addLayout(common)

        self.on_settle_ms = self._spin(0, 3_600_000, 250, " ms")
        self.off_settle_ms = self._spin(0, 3_600_000, 50, " ms")
        self.excitation_duration_ms = self._spin(0, 86_400_000, 1000, " ms")
        self.on_frames = self._spin(0, 100_000, 1)
        self.off_frames = self._spin(0, 100_000, 1)
        self.delayed_count = self._spin(1, 100_000, 10)
        self.delayed_start_ms = self._spin(0, 86_400_000, 0, " ms")
        self.delayed_step_ms = self._spin(0, 86_400_000, 100, " ms")
        self.transition_pre = self._spin(0, 100_000, 3)
        self.transition_post = self._spin(1, 100_000, 20)
        self.decay_start_ms = self._spin(0, 86_400_000, 0, " ms")
        self.decay_stop_ms = self._spin(0, 86_400_000, 1000, " ms")
        self.decay_resolution_ms = self._spin(1, 3_600_000, 1, " ms")
        self.decay_burst_spacing_ms = self._spin(1, 3_600_000, 100, " ms")
        midpoint_help = (
            "Delay is referenced to the estimated exposure midpoint. A frame "
            "centered just after switch-off can legitimately have a negative "
            "exposure-start value because the finite exposure straddles the transition."
        )
        self.delayed_start_ms.setToolTip(midpoint_help)
        self.delayed_step_ms.setToolTip(midpoint_help)
        self.decay_start_ms.setToolTip(midpoint_help)
        self.decay_stop_ms.setToolTip(midpoint_help)
        self.decay_resolution_ms.setToolTip(midpoint_help)

        self.timing_group = QGroupBox("Timing")
        timing = QGridLayout(self.timing_group)
        timing.setContentsMargins(8, 8, 8, 8)
        timing.setHorizontalSpacing(6)
        timing.setVerticalSpacing(4)
        timing.setColumnStretch(1, 1)
        timing.setColumnStretch(3, 1)
        self.on_settle_label = QLabel("ON settle")
        self.off_settle_label = QLabel("OFF settle")
        self.pump_time_label = QLabel("Pump time")
        timing.addWidget(self.on_settle_label, 0, 0)
        timing.addWidget(self.on_settle_ms, 0, 1)
        timing.addWidget(self.off_settle_label, 0, 2)
        timing.addWidget(self.off_settle_ms, 0, 3)
        timing.addWidget(self.pump_time_label, 1, 0)
        timing.addWidget(self.excitation_duration_ms, 1, 1)

        self.paired_group = self._settings_group(
            "ON/OFF settings",
            (
                ("ON frames", self.on_frames, "OFF frames", self.off_frames),
            ),
        )
        self.delayed_group = self._settings_group(
            "Fixed-delay settings",
            (
                ("Frames", self.delayed_count, "First delay", self.delayed_start_ms),
                ("Delay step", self.delayed_step_ms, "", None),
            ),
        )
        self.transition_group = self._settings_group(
            "Transition settings",
            (
                ("Before OFF", self.transition_pre, "After OFF", self.transition_post),
            ),
        )
        self.decay_group = self._settings_group(
            "Interleaved-decay settings",
            (
                ("Burst spacing", self.decay_burst_spacing_ms, "Grid", self.decay_resolution_ms),
                ("Start delay", self.decay_start_ms, "Stop delay", self.decay_stop_ms),
            ),
        )
        self.delayed_count.valueChanged.connect(self._update_delayed_step_enabled)
        layout.addWidget(self.timing_group)
        for group in (
            self.paired_group,
            self.delayed_group,
            self.transition_group,
            self.decay_group,
        ):
            layout.addWidget(group)

        self.timing_quality_group = QGroupBox("Advanced timing quality")
        self.timing_quality_group.setCheckable(True)
        self.timing_quality_group.setChecked(False)
        quality = QGridLayout(self.timing_quality_group)
        quality.setContentsMargins(8, 8, 8, 8)
        quality.setHorizontalSpacing(6)
        quality.setVerticalSpacing(4)
        quality.setColumnStretch(1, 1)
        quality.setColumnStretch(3, 1)

        self.timing_guard_sigma = self._double_spin(2.0, 20.0, 4.5, 0.5)
        self.timing_guard_warmup = self._spin(3, 10_000, 5)
        self.timing_guard_rejected_percent = self._double_spin(
            0.0, 95.0, 25.0, 1.0, "%"
        )
        self.timing_guard_min_evaluated = self._spin(3, 100_000, 10)
        self.timing_guard_reference = QComboBox()
        self.timing_guard_reference.addItem("Exposure midpoint", "exposure_midpoint")
        self.timing_guard_reference.addItem("Exposure start", "exposure_start")
        self.timing_guard_tolerance = self._double_spin(
            0.05, 86_400_000.0, 2.0, 0.5, " ms"
        )
        self.timing_guard_attempt_multiplier = self._spin(1, 100, 3, "×")
        self.timing_guard_sigma.setToolTip(
            "Reject a frame only when its timing residual is this many robust "
            "standard deviations from the rolling baseline."
        )
        self.timing_guard_warmup.setToolTip(
            "Accept this many eligible OFF frames to establish the timing baseline."
        )
        self.timing_guard_rejected_percent.setToolTip(
            "Abort when the rejected fraction exceeds this percentage after the "
            "minimum evaluated-frame count."
        )
        self.timing_guard_min_evaluated.setToolTip(
            "Do not apply the rejected-fraction abort rule before this many frames."
        )
        self.timing_guard_reference.setToolTip(
            "The point on the estimated detector exposure window compared with "
            "the requested delay. Midpoint is recommended for decay curves; "
            "start is available for protocols defined by exposure onset."
        )
        self.timing_guard_tolerance.setToolTip(
            "Retain a frame when the selected exposure reference is within this "
            "symmetric tolerance of the requested delay."
        )
        self.timing_guard_attempt_multiplier.setToolTip(
            "Maximum pump/OFF attempts per requested retained repeat. The run "
            "fails safely and saves a partial average if the target is not met."
        )
        self.timing_guard_warmup.valueChanged.connect(
            self._synchronize_timing_guard_minimum
        )

        quality.addWidget(QLabel("Robust sigma"), 0, 0)
        quality.addWidget(self.timing_guard_sigma, 0, 1)
        quality.addWidget(QLabel("Warm-up frames"), 0, 2)
        quality.addWidget(self.timing_guard_warmup, 0, 3)
        quality.addWidget(QLabel("Abort above"), 1, 0)
        quality.addWidget(self.timing_guard_rejected_percent, 1, 1)
        quality.addWidget(QLabel("Minimum evaluated"), 1, 2)
        quality.addWidget(self.timing_guard_min_evaluated, 1, 3)
        quality.addWidget(QLabel("Retain reference"), 2, 0)
        quality.addWidget(self.timing_guard_reference, 2, 1)
        quality.addWidget(QLabel("Tolerance"), 2, 2)
        quality.addWidget(self.timing_guard_tolerance, 2, 3)
        quality.addWidget(QLabel("Attempt ceiling"), 3, 0)
        quality.addWidget(self.timing_guard_attempt_multiplier, 3, 1)
        self._timing_quality_controls = tuple(
            quality.itemAt(index).widget()
            for index in range(quality.count())
            if quality.itemAt(index).widget() is not None
        )
        self.timing_quality_group.toggled.connect(
            self._update_timing_quality_visibility
        )
        layout.addWidget(self.timing_quality_group)

        self.enable_before = QCheckBox("Enable before start")
        self.enable_before.setChecked(True)
        self.disable_after = QCheckBox("Disable after finish")
        self.disable_after.setChecked(True)
        self.autosave = QCheckBox("Autosave output")
        self.autosave.setChecked(True)
        self.measure_power = QCheckBox("Power/frame")
        self.measure_power.setChecked(False)
        self.measure_power.setToolTip(
            "Read Newport before and after every gated frame. This adds variable "
            "latency; leave it off for the best software timing."
        )
        options = QHBoxLayout()
        options.addWidget(self.enable_before)
        options.addWidget(self.disable_after)
        options.addWidget(self.autosave)
        options.addWidget(self.measure_power)
        options.addStretch(1)
        layout.addLayout(options)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Preview")
        self.run_button = QPushButton("Run")
        self.abort_button = QPushButton("Abort")
        self.preview_button.clicked.connect(self.preview_requested.emit)
        self.run_button.clicked.connect(self.run_requested.emit)
        self.abort_button.clicked.connect(self.abort_requested.emit)
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.abort_button)
        layout.addLayout(buttons)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Action", "Laser", "Timing", "Spectra", "Details"]
        )
        self.table.verticalHeader().setDefaultSectionSize(22)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table, stretch=1)

        self._update_mode_visibility()
        self._apply_control_state()

    @staticmethod
    def _settings_group(title: str, rows: tuple[tuple, ...]) -> QGroupBox:
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        for row_index, (label1, widget1, label2, widget2) in enumerate(rows):
            grid.addWidget(QLabel(str(label1)), row_index, 0)
            grid.addWidget(widget1, row_index, 1)
            if widget2 is not None:
                grid.addWidget(QLabel(str(label2)), row_index, 2)
                grid.addWidget(widget2, row_index, 3)
        return group

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        spin.setValue(int(value))
        spin.setSuffix(str(suffix))
        spin.setMaximumWidth(112)
        return spin

    @staticmethod
    def _double_spin(
        minimum: float,
        maximum: float,
        value: float,
        step: float,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(float(minimum), float(maximum))
        spin.setValue(float(value))
        spin.setSingleStep(float(step))
        spin.setDecimals(1)
        spin.setSuffix(str(suffix))
        spin.setMaximumWidth(112)
        return spin

    def settings(self, *, frame_period_hint_ms: float = float("nan")) -> GatedAcquisitionSettings:
        return GatedAcquisitionSettings(
            mode=str(self.mode_combo.currentData()),
            cycles=self.cycles.value(),
            on_settle_ms=self.on_settle_ms.value(),
            off_settle_ms=self.off_settle_ms.value(),
            excitation_duration_ms=self.excitation_duration_ms.value(),
            inter_frame_gap_ms=self.inter_frame_gap_ms.value(),
            inter_repeat_gap_ms=self.inter_repeat_gap_ms.value(),
            on_frames_per_cycle=self.on_frames.value(),
            off_frames_per_cycle=self.off_frames.value(),
            delayed_frame_count=self.delayed_count.value(),
            delayed_start_ms=self.delayed_start_ms.value(),
            delayed_step_ms=self.delayed_step_ms.value(),
            transition_pre_frames=self.transition_pre.value(),
            transition_post_frames=self.transition_post.value(),
            decay_start_ms=self.decay_start_ms.value(),
            decay_stop_ms=self.decay_stop_ms.value(),
            decay_resolution_ms=self.decay_resolution_ms.value(),
            decay_burst_spacing_ms=self.decay_burst_spacing_ms.value(),
            frame_period_hint_ms=float(frame_period_hint_ms),
            enable_before_start=self.enable_before.isChecked(),
            disable_after_finish=self.disable_after.isChecked(),
            autosave_frames=self.autosave.isChecked(),
            measure_power_per_frame=self.measure_power.isChecked(),
            output_mode=str(self.output_combo.currentData()),
            timing_guard_mode=str(self.timing_guard_combo.currentData()),
            timing_guard_sigma=float(self.timing_guard_sigma.value()),
            timing_guard_warmup=int(self.timing_guard_warmup.value()),
            timing_guard_max_rejected_fraction=(
                float(self.timing_guard_rejected_percent.value()) / 100.0
            ),
            timing_guard_min_evaluated=int(
                self.timing_guard_min_evaluated.value()
            ),
            timing_guard_reference=str(self.timing_guard_reference.currentData()),
            timing_guard_tolerance_ms=float(self.timing_guard_tolerance.value()),
            timing_guard_attempt_multiplier=int(
                self.timing_guard_attempt_multiplier.value()
            ),
        )

    def _preference_widgets(self) -> tuple[tuple[str, QSpinBox], ...]:
        return (
            ("cycles", self.cycles),
            ("on_settle_ms", self.on_settle_ms),
            ("off_settle_ms", self.off_settle_ms),
            ("excitation_duration_ms", self.excitation_duration_ms),
            ("inter_frame_gap_ms", self.inter_frame_gap_ms),
            ("inter_repeat_gap_ms", self.inter_repeat_gap_ms),
            ("on_frames", self.on_frames),
            ("off_frames", self.off_frames),
            ("delayed_count", self.delayed_count),
            ("delayed_start_ms", self.delayed_start_ms),
            ("delayed_step_ms", self.delayed_step_ms),
            ("transition_pre", self.transition_pre),
            ("transition_post", self.transition_post),
            ("decay_start_ms", self.decay_start_ms),
            ("decay_stop_ms", self.decay_stop_ms),
            ("decay_resolution_ms", self.decay_resolution_ms),
            ("decay_burst_spacing_ms", self.decay_burst_spacing_ms),
        )

    def load_preferences(self, settings: QSettings) -> None:
        mode = get_str(settings, "gated/mode", "on_off_pair")
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        output_mode = get_str(settings, "gated/output_mode", "individual_frames")
        index = self.output_combo.findData(output_mode)
        if index >= 0:
            self.output_combo.setCurrentIndex(index)
        timing_guard_mode = get_str(settings, "gated/timing_guard_mode", "auto")
        index = self.timing_guard_combo.findData(timing_guard_mode)
        if index >= 0:
            self.timing_guard_combo.setCurrentIndex(index)
        for key, widget in self._preference_widgets():
            widget.setValue(get_int(settings, f"gated/{key}", widget.value()))
        self.timing_guard_sigma.setValue(
            get_float(settings, "gated/timing_guard_sigma", 4.5)
        )
        self.timing_guard_warmup.setValue(
            get_int(settings, "gated/timing_guard_warmup", 5)
        )
        self.timing_guard_rejected_percent.setValue(
            100.0
            * get_float(
                settings,
                "gated/timing_guard_max_rejected_fraction",
                0.25,
            )
        )
        self.timing_guard_min_evaluated.setValue(
            get_int(settings, "gated/timing_guard_min_evaluated", 10)
        )
        reference = get_str(
            settings,
            "gated/timing_guard_reference",
            "exposure_midpoint",
        )
        index = self.timing_guard_reference.findData(reference)
        if index >= 0:
            self.timing_guard_reference.setCurrentIndex(index)
        self.timing_guard_tolerance.setValue(
            get_float(settings, "gated/timing_guard_tolerance_ms", 2.0)
        )
        self.timing_guard_attempt_multiplier.setValue(
            get_int(settings, "gated/timing_guard_attempt_multiplier", 3)
        )
        self.enable_before.setChecked(
            get_bool(settings, "gated/enable_before", self.enable_before.isChecked())
        )
        self.disable_after.setChecked(
            get_bool(settings, "gated/disable_after", self.disable_after.isChecked())
        )
        self.autosave.setChecked(get_bool(settings, "gated/autosave", self.autosave.isChecked()))
        self.measure_power.setChecked(
            get_bool(settings, "gated/measure_power", self.measure_power.isChecked())
        )
        self._synchronize_timing_guard_minimum()
        self._update_timing_quality_visibility(
            self.timing_quality_group.isChecked()
        )
        self._update_mode_visibility()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("gated/mode", str(self.mode_combo.currentData()))
        settings.setValue("gated/output_mode", str(self.output_combo.currentData()))
        settings.setValue(
            "gated/timing_guard_mode",
            str(self.timing_guard_combo.currentData()),
        )
        for key, widget in self._preference_widgets():
            settings.setValue(f"gated/{key}", widget.value())
        settings.setValue("gated/timing_guard_sigma", self.timing_guard_sigma.value())
        settings.setValue("gated/timing_guard_warmup", self.timing_guard_warmup.value())
        settings.setValue(
            "gated/timing_guard_max_rejected_fraction",
            self.timing_guard_rejected_percent.value() / 100.0,
        )
        settings.setValue(
            "gated/timing_guard_min_evaluated",
            self.timing_guard_min_evaluated.value(),
        )
        settings.setValue(
            "gated/timing_guard_reference",
            str(self.timing_guard_reference.currentData()),
        )
        settings.setValue(
            "gated/timing_guard_tolerance_ms",
            self.timing_guard_tolerance.value(),
        )
        settings.setValue(
            "gated/timing_guard_attempt_multiplier",
            self.timing_guard_attempt_multiplier.value(),
        )
        settings.setValue("gated/enable_before", self.enable_before.isChecked())
        settings.setValue("gated/disable_after", self.disable_after.isChecked())
        settings.setValue("gated/autosave", self.autosave.isChecked())
        settings.setValue("gated/measure_power", self.measure_power.isChecked())

    def set_plan(self, plan: GatedPlan) -> None:
        rows = plan.preview_rows
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(rows))
            for row, preview in enumerate(rows):
                values = (
                    preview.action,
                    preview.state,
                    preview.timing,
                    preview.spectra,
                    preview.details,
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
        finally:
            self.table.setUpdatesEnabled(True)

        if 0 < plan.target_frame_count < plan.frame_count:
            frame_summary = (
                f"target {plan.target_frame_count:,} retained spectra; "
                f"up to {plan.frame_count:,} attempts"
            )
        else:
            frame_summary = f"{plan.frame_count:,} spectra"
        summary = (
            f"Plan: {frame_summary}, {plan.action_count:,} actions; estimated "
            "peak retained memory "
            f"{plan.estimated_peak_memory_bytes / (1024.0 * 1024.0):.1f} MiB."
        )
        self.warning_label.setText("\n".join((summary, *plan.warnings)))

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._apply_control_state()

    def set_external_busy(self, busy: bool) -> None:
        self._external_busy = bool(busy)
        self._apply_control_state()

    def set_instrument_availability(
        self,
        *,
        spectrometer_available: bool,
        lasers_available: bool,
    ) -> None:
        self._spectrometer_available = bool(spectrometer_available)
        self._lasers_available = bool(lasers_available)
        self._apply_control_state()

    def _apply_control_state(self) -> None:
        idle = not self._running and not self._external_busy
        self.preview_button.setEnabled(idle)
        self.run_button.setEnabled(
            idle and self._lasers_available and self._spectrometer_available
        )
        self.abort_button.setEnabled(self._running)
        for widget in (
            self.mode_combo,
            self.output_combo,
            self.timing_guard_combo,
            *(widget for _key, widget in self._preference_widgets()),
            self.enable_before,
            self.disable_after,
            self.autosave,
            self.measure_power,
            self.timing_guard_sigma,
            self.timing_guard_warmup,
            self.timing_guard_rejected_percent,
            self.timing_guard_min_evaluated,
            self.timing_guard_reference,
            self.timing_guard_tolerance,
            self.timing_guard_attempt_multiplier,
        ):
            widget.setEnabled(idle)
        self._update_timing_guard_state()
        self._update_delayed_step_enabled()

    def _update_mode_visibility(self) -> None:
        mode = str(self.mode_combo.currentData())
        settle_mode = mode in {"on_off_pair", "transition_series"}
        pump_mode = mode in {"delayed_after_off", "interleaved_decay"}
        for widget in (
            self.on_settle_label,
            self.on_settle_ms,
            self.off_settle_label,
            self.off_settle_ms,
        ):
            widget.setVisible(settle_mode)
        for widget in (self.pump_time_label, self.excitation_duration_ms):
            widget.setVisible(pump_mode)
        self.paired_group.setVisible(mode == "on_off_pair")
        self.delayed_group.setVisible(mode == "delayed_after_off")
        self.transition_group.setVisible(mode == "transition_series")
        self.decay_group.setVisible(mode == "interleaved_decay")
        self._update_delayed_step_enabled()

    def _synchronize_timing_guard_minimum(self) -> None:
        warmup = int(self.timing_guard_warmup.value())
        self.timing_guard_min_evaluated.setMinimum(warmup)
        if self.timing_guard_min_evaluated.value() < warmup:
            self.timing_guard_min_evaluated.setValue(warmup)

    def _update_timing_guard_state(self, *_args) -> None:
        mode = str(self.timing_guard_combo.currentData())
        expanded = self.timing_quality_group.isChecked()
        idle = not self._running and not self._external_busy
        enabled = (
            not self._running
            and not self._external_busy
            and mode not in {"off", "retain"}
            and expanded
        )
        for widget in (
            self.timing_guard_sigma,
            self.timing_guard_warmup,
            self.timing_guard_rejected_percent,
            self.timing_guard_min_evaluated,
        ):
            widget.setEnabled(enabled)
        retain_enabled = (
            not self._running
            and not self._external_busy
            and mode == "retain"
            and expanded
        )
        for widget in (
            self.timing_guard_reference,
            self.timing_guard_tolerance,
            self.timing_guard_attempt_multiplier,
        ):
            widget.setEnabled(retain_enabled)
        if mode == "retain":
            averaged_index = self.output_combo.findData("averaged_series")
            if averaged_index >= 0 and self.output_combo.currentIndex() != averaged_index:
                self.output_combo.setCurrentIndex(averaged_index)
            self.measure_power.setChecked(False)
        self.output_combo.setEnabled(idle and mode != "retain")
        self.measure_power.setEnabled(idle and mode != "retain")

    def _update_timing_quality_visibility(self, expanded: bool) -> None:
        for widget in self._timing_quality_controls:
            widget.setVisible(bool(expanded))
        self._update_timing_guard_state()

    def _update_delayed_step_enabled(self, *_args) -> None:
        enabled = (
            not self._running
            and not self._external_busy
            and str(self.mode_combo.currentData()) == "delayed_after_off"
            and int(self.delayed_count.value()) > 1
        )
        self.delayed_step_ms.setEnabled(enabled)
