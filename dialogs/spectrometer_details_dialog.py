from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from core.records import SpectrometerCapabilities


class SpectrometerDetailsDialog(QDialog):
    tec_target_requested = Signal(float)
    tec_enabled_requested = Signal(bool)
    temperature_refresh_requested = Signal()
    configuration_requested = Signal(object)

    def __init__(self, capabilities: SpectrometerCapabilities, parent=None) -> None:
        super().__init__(parent)

        self.capabilities = capabilities

        self.setWindowTitle("Spectrometer Details")
        self.resize(650, 520)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.tec_enabled_check = QCheckBox()
        self.tec_enabled_check.setEnabled(bool(capabilities.tec_supported))
        self.tec_enabled_check.toggled.connect(self.tec_enabled_requested.emit)

        self.tec_target_spin = QDoubleSpinBox()
        self.tec_target_spin.setRange(-50.0, 50.0)
        self.tec_target_spin.setDecimals(2)
        self.tec_target_spin.setValue(-10.0)
        self.tec_target_spin.setSuffix(" °C")
        self.tec_target_spin.setEnabled(bool(capabilities.tec_supported))
        if capabilities.backend == "andor":
            camera_schema = dict(capabilities.control_schema.get("camera", {}))
            temperature_min = int(camera_schema.get("temperature_min_c", -50))
            temperature_max = int(camera_schema.get("temperature_max_c", 50))
            if temperature_min < temperature_max:
                self.tec_target_spin.setRange(temperature_min, temperature_max)

        self.set_tec_button = QPushButton("Set TEC Target")
        self.set_tec_button.setEnabled(bool(capabilities.tec_supported))
        self.set_tec_button.clicked.connect(
            lambda: self.tec_target_requested.emit(float(self.tec_target_spin.value()))
        )

        self.refresh_temp_button = QPushButton("Read CCD Temperature")
        self.refresh_temp_button.setEnabled(bool(capabilities.tec_supported))
        self.refresh_temp_button.clicked.connect(
            lambda _checked=False: self.temperature_refresh_requested.emit()
        )

        form.addRow("TEC supported", QLabel("Yes" if capabilities.tec_supported else "No"))
        form.addRow("TEC enabled", self.tec_enabled_check)
        form.addRow("TEC target", self.tec_target_spin)
        form.addRow("", self.set_tec_button)
        form.addRow("", self.refresh_temp_button)

        form.addRow(
            "Device averaging supported",
            QLabel("Yes" if capabilities.device_averaging_supported else "No"),
        )

        layout.addLayout(form)

        self._andor_widgets: dict[str, object] = {}
        if capabilities.backend == "andor" and capabilities.control_schema:
            self.resize(780, 760)
            andor_scroll = QScrollArea()
            andor_scroll.setWidgetResizable(True)
            andor_scroll.setMinimumHeight(330)
            andor_scroll.setWidget(
                self._build_andor_controls(capabilities.control_schema)
            )
            layout.addWidget(andor_scroll, stretch=2)

        text = QTextEdit()
        text.setReadOnly(True)

        lines = [
            f"Model: {capabilities.model}",
            f"Serial: {capabilities.serial_number}",
            f"Pixels: {capabilities.pixels}",
            f"Max intensity: {capabilities.max_intensity}",
            (
                f"Integration limits: {capabilities.integration_time_min_us} "
                f"- {capabilities.integration_time_max_us} us"
            ),
            "",
            "Features:",
        ]

        for feature in capabilities.features:
            lines.append(f"  {feature}")

        lines.append("")
        lines.append("Feature methods:")

        for feature, methods in capabilities.feature_methods.items():
            lines.append(f"{feature}:")
            for method in methods:
                lines.append(f"  {method}")

        text.setPlainText("\n".join(lines))
        layout.addWidget(text, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _build_andor_controls(self, schema: dict[str, object]) -> QGroupBox:
        group = QGroupBox("Andor acquisition and Kymera controls")
        form = QFormLayout(group)
        camera = dict(schema.get("camera", {}))
        spectrograph = dict(schema.get("spectrograph", {}))
        state = dict(schema.get("state", {}))
        camera_state = dict(state.get("camera", {}))
        spectrograph_state = dict(state.get("spectrograph", {}))

        grating = QComboBox()
        grating_limits: dict[int, tuple[float, float]] = {}
        for item in spectrograph.get("gratings", ()):
            data = dict(item)
            grating_index = int(data.get("index", 0))
            grating_limits[grating_index] = (
                float(data.get("minimum_nm", 0.0)),
                float(data.get("maximum_nm", 20_000.0)),
            )
            grating.addItem(
                f"{data.get('index')}: {float(data.get('lines_per_mm', 0.0)):.3g} "
                f"l/mm, {data.get('blaze', '')}",
                grating_index,
            )
        self._set_combo_data(grating, int(spectrograph_state.get("grating", 0)))
        form.addRow("Grating", grating)

        center = QDoubleSpinBox()
        center.setRange(0.0, 20_000.0)
        center.setDecimals(3)
        center.setSuffix(" nm")
        center.setValue(float(spectrograph_state.get("center_wavelength_nm", 0.0)))

        def refresh_center_range() -> None:
            selected = int(grating.currentData() or 0)
            minimum, maximum = grating_limits.get(selected, (0.0, 20_000.0))
            if (
                math.isfinite(minimum)
                and math.isfinite(maximum)
                and maximum > minimum
            ):
                center.setRange(minimum, maximum)
                center.setToolTip(
                    f"Grating {selected} range: {minimum:g} to {maximum:g} nm"
                )

        grating.currentIndexChanged.connect(refresh_center_range)
        refresh_center_range()
        form.addRow("Center wavelength", center)

        filter_position = QComboBox()
        for position in spectrograph.get("filter_positions", ()):
            filter_position.addItem(f"Position {position}", int(position))
        if filter_position.count():
            self._set_combo_data(
                filter_position,
                int(spectrograph_state.get("filter_position", 1)),
            )
            form.addRow("Kymera filter", filter_position)

        flipper_widgets: dict[str, QComboBox] = {}
        flipper_state = dict(spectrograph_state.get("flipper_positions", {}))
        for flipper_index, positions in dict(
            spectrograph.get("flipper_mirrors", {})
        ).items():
            combo = QComboBox()
            for position in positions:
                combo.addItem(f"Port {int(position)}", int(position))
            self._set_combo_data(combo, int(flipper_state.get(str(flipper_index), 0)))
            form.addRow(f"Flipper {flipper_index} / port", combo)
            flipper_widgets[str(flipper_index)] = combo

        focus = QSpinBox()
        focus_position = int(spectrograph_state.get("focus_mirror_position", 0))
        focus_maximum = int(spectrograph.get("focus_mirror_max_steps", 0))
        if focus_maximum <= 0:
            focus_maximum = max(100_000, focus_position)
            focus.setToolTip(
                "The driver did not report a focus-step maximum; the current "
                "position is preserved and a conservative GUI limit is used."
            )
        focus.setRange(0, max(focus_maximum, focus_position))
        focus.setValue(focus_position)
        focus.setEnabled(bool(spectrograph.get("focus_mirror_present", False)))
        if focus.isEnabled():
            form.addRow("Focus mirror", focus)

        ad_channel = QComboBox()
        for index in camera.get("ad_channels", ()):
            ad_channel.addItem(str(index), int(index))
        self._set_combo_data(ad_channel, int(camera_state.get("ad_channel", 0)))
        form.addRow("A/D channel", ad_channel)

        amplifier = QComboBox()
        for index in camera.get("output_amplifiers", ()):
            amplifier.addItem(str(index), int(index))
        self._set_combo_data(amplifier, int(camera_state.get("output_amplifier", 0)))
        form.addRow("Output amplifier", amplifier)

        horizontal_speed = QComboBox()
        horizontal_schema = dict(camera.get("horizontal_speeds_mhz", {}))

        def refresh_horizontal_speed() -> None:
            previous = int(camera_state.get("horizontal_speed_index", 0))
            horizontal_speed.clear()
            key = f"{int(ad_channel.currentData() or 0)}:{int(amplifier.currentData() or 0)}"
            for index, speed in enumerate(horizontal_schema.get(key, ())):
                horizontal_speed.addItem(f"{float(speed):.4g} MHz", index)
            self._set_combo_data(horizontal_speed, previous)

        ad_channel.currentIndexChanged.connect(refresh_horizontal_speed)
        amplifier.currentIndexChanged.connect(refresh_horizontal_speed)
        refresh_horizontal_speed()
        form.addRow("Horizontal readout", horizontal_speed)

        vertical_speed = QComboBox()
        for index, speed in enumerate(camera.get("vertical_speeds_us", ())):
            vertical_speed.addItem(f"{float(speed):.4g} µs/pixel", index)
        self._set_combo_data(vertical_speed, int(camera_state.get("vertical_speed_index", 0)))
        form.addRow("Vertical shift", vertical_speed)

        preamp_gain = QComboBox()
        gain_values = tuple(camera.get("preamp_gains", ()))
        gain_availability = dict(camera.get("preamp_gain_indices", {}))

        def refresh_preamp_gain() -> None:
            previous = int(camera_state.get("preamp_gain_index", 0))
            preamp_gain.clear()
            key = (
                f"{int(ad_channel.currentData() or 0)}:"
                f"{int(amplifier.currentData() or 0)}:"
                f"{int(horizontal_speed.currentData() or 0)}"
            )
            indices = gain_availability.get(key, range(len(gain_values)))
            for index in indices:
                if 0 <= int(index) < len(gain_values):
                    preamp_gain.addItem(
                        f"{float(gain_values[int(index)]):.4g}×",
                        int(index),
                    )
            self._set_combo_data(preamp_gain, previous)

        ad_channel.currentIndexChanged.connect(refresh_preamp_gain)
        amplifier.currentIndexChanged.connect(refresh_preamp_gain)
        horizontal_speed.currentIndexChanged.connect(refresh_preamp_gain)
        refresh_preamp_gain()
        form.addRow("Preamp gain", preamp_gain)

        binning = QComboBox()
        detector_pixels = max(1, int(camera.get("detector_pixels_x", 1)))
        for value in range(1, detector_pixels + 1):
            if detector_pixels % value == 0:
                binning.addItem(f"{value} pixel{'s' if value != 1 else ''}", value)
        self._set_combo_data(binning, int(camera_state.get("horizontal_binning", 1)))
        binning.setToolTip(
            "Horizontal binning changes the Kymera calibration geometry. Andor's "
            "SDK2 guide recommends a value of 1 for iDus cameras."
        )
        form.addRow("Horizontal binning", binning)

        advanced_offsets = QCheckBox("Apply offsets below")
        grating_offset = QSpinBox()
        grating_offset.setRange(-1_000_000, 1_000_000)
        grating_offsets = {
            int(dict(item).get("index", 0)): int(dict(item).get("offset", 0))
            for item in spectrograph.get("gratings", ())
        }
        detector_offset = QSpinBox()
        detector_offset.setRange(-1_000_000, 1_000_000)
        entrance_port = QComboBox()
        exit_port = QComboBox()
        for combo in (entrance_port, exit_port):
            combo.addItem("Port 0", 0)
            combo.addItem("Port 1", 1)
        detector_offsets = {
            str(key): int(value)
            for key, value in dict(spectrograph.get("detector_offsets", {})).items()
        }

        def refresh_grating_offset() -> None:
            grating_offset.setValue(
                grating_offsets.get(int(grating.currentData() or 0), 0)
            )

        def refresh_detector_offset() -> None:
            key = (
                f"{int(entrance_port.currentData() or 0)}:"
                f"{int(exit_port.currentData() or 0)}"
            )
            detector_offset.setValue(detector_offsets.get(key, 0))

        grating.currentIndexChanged.connect(refresh_grating_offset)
        entrance_port.currentIndexChanged.connect(refresh_detector_offset)
        exit_port.currentIndexChanged.connect(refresh_detector_offset)
        refresh_grating_offset()
        refresh_detector_offset()
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Grating"))
        offset_row.addWidget(grating_offset)
        offset_row.addWidget(QLabel("Detector"))
        offset_row.addWidget(detector_offset)
        offset_row.addWidget(entrance_port)
        offset_row.addWidget(exit_port)
        form.addRow("Advanced offsets", advanced_offsets)
        form.addRow("", offset_row)

        step_and_glue = dict(schema.get("step_and_glue", {}))
        message = QLabel(str(step_and_glue.get("reason", "")))
        message.setWordWrap(True)
        form.addRow("Step-and-Glue", message)

        apply_button = QPushButton("Apply Andor Settings")
        form.addRow("", apply_button)
        self._andor_widgets = {
            "grating": grating,
            "center": center,
            "filter": filter_position,
            "flippers": flipper_widgets,
            "focus": focus,
            "ad": ad_channel,
            "amplifier": amplifier,
            "hs": horizontal_speed,
            "vs": vertical_speed,
            "gain": preamp_gain,
            "binning": binning,
            "advanced": advanced_offsets,
            "grating_offset": grating_offset,
            "detector_offset": detector_offset,
            "entrance_port": entrance_port,
            "exit_port": exit_port,
        }
        apply_button.clicked.connect(self._emit_andor_configuration)
        return group

    def _emit_andor_configuration(self) -> None:
        widgets = self._andor_widgets
        camera = {
            "ad_channel": int(widgets["ad"].currentData() or 0),
            "output_amplifier": int(widgets["amplifier"].currentData() or 0),
            "horizontal_speed_index": int(widgets["hs"].currentData() or 0),
            "vertical_speed_index": int(widgets["vs"].currentData() or 0),
            "preamp_gain_index": int(widgets["gain"].currentData() or 0),
            "horizontal_binning": int(widgets["binning"].currentData() or 1),
        }
        spectrograph: dict[str, object] = {
            "grating": int(widgets["grating"].currentData() or 1),
            "center_wavelength_nm": float(widgets["center"].value()),
            "flipper_positions": {
                key: int(combo.currentData() or 0)
                for key, combo in widgets["flippers"].items()
            },
        }
        if widgets["filter"].count():
            spectrograph["filter_position"] = int(widgets["filter"].currentData())
        if widgets["focus"].isEnabled():
            spectrograph["focus_mirror_position"] = int(widgets["focus"].value())
        if widgets["advanced"].isChecked():
            spectrograph["grating_offset"] = int(widgets["grating_offset"].value())
            spectrograph["detector_offset"] = {
                "entrance_port": int(widgets["entrance_port"].currentData()),
                "exit_port": int(widgets["exit_port"].currentData()),
                "offset": int(widgets["detector_offset"].value()),
            }
        self.configuration_requested.emit(
            {"camera": camera, "spectrograph": spectrograph}
        )
