from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
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
        self._spectrometer_available = False
        self._lasers_available = False

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.form = form

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Paired ON / OFF", "on_off_pair")
        self.mode_combo.addItem("Delayed frames after OFF", "delayed_after_off")
        self.mode_combo.addItem("Transition series", "transition_series")
        self.mode_combo.currentIndexChanged.connect(self._update_mode_visibility)

        self.cycles = self._spin(1, 10_000, 1)
        self.on_settle_ms = self._spin(0, 3_600_000, 250, " ms")
        self.off_settle_ms = self._spin(0, 3_600_000, 50, " ms")
        self.excitation_duration_ms = self._spin(0, 86_400_000, 1000, " ms")
        self.inter_frame_gap_ms = self._spin(0, 3_600_000, 0, " ms")

        self.on_frames = self._spin(0, 100_000, 1)
        self.off_frames = self._spin(0, 100_000, 1)

        self.delayed_count = self._spin(1, 100_000, 10)
        self.delayed_start_ms = self._spin(0, 86_400_000, 0, " ms")
        self.delayed_step_ms = self._spin(0, 86_400_000, 100, " ms")

        self.transition_pre = self._spin(0, 100_000, 3)
        self.transition_post = self._spin(1, 100_000, 20)

        self.enable_before = QCheckBox()
        self.enable_before.setChecked(True)
        self.disable_after = QCheckBox()
        self.disable_after.setChecked(True)
        self.autosave = QCheckBox()
        self.autosave.setChecked(True)

        form.addRow("Mode", self.mode_combo)
        form.addRow("Cycles", self.cycles)
        form.addRow("Laser ON settle", self.on_settle_ms)
        form.addRow("Laser OFF settle", self.off_settle_ms)
        form.addRow("Excitation duration", self.excitation_duration_ms)
        form.addRow("Inter-frame gap", self.inter_frame_gap_ms)
        form.addRow("ON frames / cycle", self.on_frames)
        form.addRow("OFF frames / cycle", self.off_frames)
        form.addRow("Delayed frame count", self.delayed_count)
        form.addRow("First delay", self.delayed_start_ms)
        form.addRow("Delay step", self.delayed_step_ms)
        form.addRow("Pre-transition frames", self.transition_pre)
        form.addRow("Post-transition frames", self.transition_post)
        form.addRow("Enable before start", self.enable_before)
        form.addRow("Disable after finish", self.disable_after)
        form.addRow("Autosave frames", self.autosave)
        layout.addLayout(form)

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
        layout.addWidget(self.table, stretch=1)

        self._update_mode_visibility()
        self._apply_control_state()

    @staticmethod
    def _spin(
        minimum: int,
        maximum: int,
        value: int,
        suffix: str = "",
    ) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        spin.setValue(int(value))
        spin.setSuffix(str(suffix))
        return spin

    def settings(self) -> GatedAcquisitionSettings:
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
            enable_before_start=self.enable_before.isChecked(),
            disable_after_finish=self.disable_after.isChecked(),
            autosave_frames=self.autosave.isChecked(),
        )


    def load_preferences(self, settings: QSettings) -> None:
        mode = get_str(settings, "gated/mode", "on_off_pair")
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        for key, widget in (
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
        ):
            widget.setValue(get_int(settings, f"gated/{key}", widget.value()))
        self.enable_before.setChecked(
            get_bool(settings, "gated/enable_before", self.enable_before.isChecked())
        )
        self.disable_after.setChecked(
            get_bool(settings, "gated/disable_after", self.disable_after.isChecked())
        )
        self.autosave.setChecked(
            get_bool(settings, "gated/autosave", self.autosave.isChecked())
        )
        self._update_mode_visibility()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("gated/mode", str(self.mode_combo.currentData()))
        for key, widget in (
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
        ):
            settings.setValue(f"gated/{key}", widget.value())
        settings.setValue("gated/enable_before", self.enable_before.isChecked())
        settings.setValue("gated/disable_after", self.disable_after.isChecked())
        settings.setValue("gated/autosave", self.autosave.isChecked())

    def set_plan(self, plan: GatedPlan) -> None:
        self.table.setRowCount(len(plan.actions))
        for row, action in enumerate(plan.actions):
            frame = action.frame
            values = [
                action.kind,
                (
                    "ON"
                    if action.laser_enabled is True
                    else "OFF"
                    if action.laser_enabled is False
                    else ""
                ),
                f"{action.wait_ms} ms" if action.wait_ms else "",
                frame.label if frame is not None else "",
                f"{action.target_delay_ms} ms" if action.kind == "acquire_at_delay" else "",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.warning_label.setText("\n".join(plan.warnings))

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
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
        idle = not self._running
        self.preview_button.setEnabled(idle and self._lasers_available)
        self.run_button.setEnabled(
            idle and self._lasers_available and self._spectrometer_available
        )
        self.abort_button.setEnabled(self._running)
        for widget in (
            self.mode_combo,
            self.cycles,
            self.on_settle_ms,
            self.off_settle_ms,
            self.excitation_duration_ms,
            self.inter_frame_gap_ms,
            self.on_frames,
            self.off_frames,
            self.delayed_count,
            self.delayed_start_ms,
            self.delayed_step_ms,
            self.transition_pre,
            self.transition_post,
            self.enable_before,
            self.disable_after,
            self.autosave,
        ):
            widget.setEnabled(idle)

    def _update_mode_visibility(self) -> None:
        mode = str(self.mode_combo.currentData())
        paired = mode == "on_off_pair"
        delayed = mode == "delayed_after_off"
        transition = mode == "transition_series"

        for widget in (self.on_frames, self.off_frames):
            self.form.setRowVisible(widget, paired)
        for widget in (
            self.delayed_count,
            self.delayed_start_ms,
            self.delayed_step_ms,
            self.excitation_duration_ms,
        ):
            self.form.setRowVisible(widget, delayed)
        for widget in (self.transition_pre, self.transition_post):
            self.form.setRowVisible(widget, transition)
