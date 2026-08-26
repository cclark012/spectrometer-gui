from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from core.preferences import get_bool, get_int, get_str


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

        self.cycles = self._spin(1, 10_000, 1)
        self.inter_frame_gap_ms = self._spin(0, 3_600_000, 0, " ms")

        common.addWidget(QLabel("Mode"), 0, 0)
        common.addWidget(self.mode_combo, 0, 1, 1, 3)
        common.addWidget(QLabel("Output"), 1, 0)
        common.addWidget(self.output_combo, 1, 1, 1, 3)
        common.addWidget(QLabel("Repeats"), 2, 0)
        common.addWidget(self.cycles, 2, 1)
        common.addWidget(QLabel("Frame gap"), 2, 2)
        common.addWidget(self.inter_frame_gap_ms, 2, 3)
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
        layout.addWidget(self.timing_group)
        for group in (
            self.paired_group,
            self.delayed_group,
            self.transition_group,
            self.decay_group,
        ):
            layout.addWidget(group)

        self.enable_before = QCheckBox("Enable before start")
        self.enable_before.setChecked(True)
        self.disable_after = QCheckBox("Disable after finish")
        self.disable_after.setChecked(True)
        self.autosave = QCheckBox("Autosave output")
        self.autosave.setChecked(True)
        options = QHBoxLayout()
        options.addWidget(self.enable_before)
        options.addWidget(self.disable_after)
        options.addWidget(self.autosave)
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
            ["Action", "State", "Wait", "Frame", "Requested delay"]
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

    def settings(self, *, frame_period_hint_ms: float = float("nan")) -> GatedAcquisitionSettings:
        return GatedAcquisitionSettings(
            mode=str(self.mode_combo.currentData()),
            cycles=self.cycles.value(),
            on_settle_ms=self.on_settle_ms.value(),
            off_settle_ms=self.off_settle_ms.value(),
            excitation_duration_ms=self.excitation_duration_ms.value(),
            inter_frame_gap_ms=self.inter_frame_gap_ms.value(),
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
            output_mode=str(self.output_combo.currentData()),
        )

    def _preference_widgets(self) -> tuple[tuple[str, QSpinBox], ...]:
        return (
            ("cycles", self.cycles),
            ("on_settle_ms", self.on_settle_ms),
            ("off_settle_ms", self.off_settle_ms),
            ("excitation_duration_ms", self.excitation_duration_ms),
            ("inter_frame_gap_ms", self.inter_frame_gap_ms),
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
        for key, widget in self._preference_widgets():
            widget.setValue(get_int(settings, f"gated/{key}", widget.value()))
        self.enable_before.setChecked(
            get_bool(settings, "gated/enable_before", self.enable_before.isChecked())
        )
        self.disable_after.setChecked(
            get_bool(settings, "gated/disable_after", self.disable_after.isChecked())
        )
        self.autosave.setChecked(get_bool(settings, "gated/autosave", self.autosave.isChecked()))
        self._update_mode_visibility()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("gated/mode", str(self.mode_combo.currentData()))
        settings.setValue("gated/output_mode", str(self.output_combo.currentData()))
        for key, widget in self._preference_widgets():
            settings.setValue(f"gated/{key}", widget.value())
        settings.setValue("gated/enable_before", self.enable_before.isChecked())
        settings.setValue("gated/disable_after", self.disable_after.isChecked())
        settings.setValue("gated/autosave", self.autosave.isChecked())

    def set_plan(self, plan: GatedPlan) -> None:
        display_actions = plan.actions[:5000]
        self.table.setRowCount(len(display_actions))
        for row, action in enumerate(display_actions):
            frame = action.frame
            values = [
                action.kind,
                (
                    "ON"
                    if action.laser_enabled is True
                    else "OFF" if action.laser_enabled is False else ""
                ),
                f"{action.wait_ms} ms" if action.wait_ms else "",
                frame.label if frame is not None else "",
                f"{action.target_delay_ms} ms" if action.kind == "acquire_at_delay" else "",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        warnings = list(plan.warnings)
        if len(plan.actions) > len(display_actions):
            warnings.append(
                f"Preview table shows the first {len(display_actions)} of "
                f"{len(plan.actions)} actions. The complete plan will run."
            )
        self.warning_label.setText("\n".join(warnings))

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
            *(widget for _key, widget in self._preference_widgets()),
            self.enable_before,
            self.disable_after,
            self.autosave,
        ):
            widget.setEnabled(idle)

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
