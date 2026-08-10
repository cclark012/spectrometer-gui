from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.records import SpectrometerInfo
from core.settings import (
    FileNameSettings,
    PlotStyleSettings,
    PowerMonitorSettings,
    SignalWarningSettings,
)


class AppSettingsDialog(QDialog):
    """Edits application settings without resetting fields not shown in the UI."""

    def __init__(
        self,
        parent,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
        warning_settings: SignalWarningSettings,
        plot_style_settings: PlotStyleSettings,
        spectrometer_info: SpectrometerInfo,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(560, 500)

        self._file_settings = replace(file_settings)
        self._power_settings = replace(power_settings)
        self._warning_settings = replace(warning_settings)
        self._plot_style_settings = replace(plot_style_settings)
        self._spectrometer_info = spectrometer_info

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_file_tab(), "Files")
        tabs.addTab(self._build_power_tab(), "Power monitor")
        tabs.addTab(self._build_warning_tab(), "Signal warning")
        tabs.addTab(self._build_plot_style_tab(), "Plot style")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_file_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.save_directory_edit = QLineEdit(str(self._file_settings.save_directory))
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_save_directory)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.save_directory_edit)
        directory_row.addWidget(browse_button)

        self.base_name_edit = QLineEdit(self._file_settings.base_name)
        self.run_identifier_edit = QLineEdit(self._file_settings.run_identifier)
        self.notes_edit = QTextEdit(self._file_settings.notes)
        self.notes_edit.setFixedHeight(90)

        self.include_run_identifier_check = self._checked(
            self._file_settings.include_run_identifier
        )
        self.include_date_check = self._checked(self._file_settings.include_date)
        self.include_time_check = self._checked(self._file_settings.include_time)
        self.include_power_check = self._checked(self._file_settings.include_power)
        self.include_field_check = self._checked(self._file_settings.include_field)
        self.include_enum_check = self._checked(
            self._file_settings.include_enumeration
        )
        self.autosave_check = self._checked(self._file_settings.autosave_spectra)

        form.addRow("Save directory", directory_row)
        form.addRow("Base name", self.base_name_edit)
        form.addRow("Run identifier", self.run_identifier_edit)
        form.addRow("Include run identifier", self.include_run_identifier_check)
        form.addRow("Include date", self.include_date_check)
        form.addRow("Include time", self.include_time_check)
        form.addRow("Include power", self.include_power_check)
        form.addRow("Include magnetic field", self.include_field_check)
        form.addRow("Automatic enumeration", self.include_enum_check)
        form.addRow("Autosave spectra", self.autosave_check)
        form.addRow("Notes", self.notes_edit)
        return tab

    def _build_power_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.power_mode_combo = QComboBox()
        self.power_mode_combo.addItem("Live readings", "live")
        self.power_mode_combo.addItem("Spectra only", "spectra_only")
        self._set_combo_data(self.power_mode_combo, self._power_settings.mode)

        self.append_spectrum_power_check = self._checked(
            self._power_settings.append_spectrum_power
        )

        self.max_power_points_spin = QSpinBox()
        self.max_power_points_spin.setRange(10, 1_000_000)
        self.max_power_points_spin.setValue(int(self._power_settings.max_points))

        self.power_interval_spin = QSpinBox()
        self.power_interval_spin.setRange(100, 3_600_000)
        self.power_interval_spin.setValue(int(self._power_settings.interval_ms))
        self.power_interval_spin.setSuffix(" ms")

        self.validation_enabled_check = self._checked(
            self._power_settings.validation_enabled
        )
        self.max_valid_power_mw_spin = QDoubleSpinBox()
        self.max_valid_power_mw_spin.setRange(0.0, 1.0e9)
        self.max_valid_power_mw_spin.setDecimals(3)
        self.max_valid_power_mw_spin.setValue(
            float(self._power_settings.max_valid_power_w) * 1e3
        )
        self.max_valid_power_mw_spin.setSuffix(" mW")

        self.invalid_retries_spin = QSpinBox()
        self.invalid_retries_spin.setRange(1, 100)
        self.invalid_retries_spin.setValue(
            int(self._power_settings.invalid_power_retries)
        )
        self.invalid_retry_delay_spin = QDoubleSpinBox()
        self.invalid_retry_delay_spin.setRange(0.0, 10.0)
        self.invalid_retry_delay_spin.setDecimals(3)
        self.invalid_retry_delay_spin.setValue(
            float(self._power_settings.invalid_power_retry_delay_s)
        )
        self.invalid_retry_delay_spin.setSuffix(" s")

        self.validate_status_check = self._checked(
            self._power_settings.validate_status_words
        )
        self.reject_range_change_check = self._checked(
            self._power_settings.reject_range_changing
        )
        self.reject_saturated_check = self._checked(
            self._power_settings.reject_detector_saturated
        )
        self.reject_overrange_check = self._checked(
            self._power_settings.reject_overrange
        )

        form.addRow("Mode", self.power_mode_combo)
        form.addRow("Add spectrum readings to trace", self.append_spectrum_power_check)
        form.addRow("Max displayed/saved points", self.max_power_points_spin)
        form.addRow("Polling interval", self.power_interval_spin)
        form.addRow("Validate readings", self.validation_enabled_check)
        form.addRow("Maximum valid power", self.max_valid_power_mw_spin)
        form.addRow("Invalid-read retries", self.invalid_retries_spin)
        form.addRow("Retry delay", self.invalid_retry_delay_spin)
        form.addRow("Validate status words", self.validate_status_check)
        form.addRow("Reject range-changing", self.reject_range_change_check)
        form.addRow("Reject saturated", self.reject_saturated_check)
        form.addRow("Reject overrange", self.reject_overrange_check)

        note = QLabel(
            "Spectra-only mode stops background polling but retains the power "
            "readings associated with spectrum acquisition."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return tab

    def _build_warning_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.warning_enabled_check = self._checked(self._warning_settings.enabled)
        self.warning_use_max_check = self._checked(
            self._warning_settings.use_spectrometer_max
        )

        self.warning_fraction_spin = QDoubleSpinBox()
        self.warning_fraction_spin.setRange(0.1, 1.0)
        self.warning_fraction_spin.setDecimals(3)
        self.warning_fraction_spin.setSingleStep(0.01)
        self.warning_fraction_spin.setValue(
            float(self._warning_settings.fraction_of_spectrometer_max)
        )

        self.warning_abs_spin = QDoubleSpinBox()
        self.warning_abs_spin.setRange(1.0, 1.0e9)
        self.warning_abs_spin.setDecimals(1)
        self.warning_abs_spin.setValue(
            float(self._warning_settings.absolute_threshold_counts)
        )
        self.warning_abs_spin.setSuffix(" counts")

        self.warning_popup_check = self._checked(
            self._warning_settings.popup_enabled
        )
        self.warning_cooldown_spin = QDoubleSpinBox()
        self.warning_cooldown_spin.setRange(0.0, 3600.0)
        self.warning_cooldown_spin.setDecimals(1)
        self.warning_cooldown_spin.setValue(
            float(self._warning_settings.popup_cooldown_s)
        )
        self.warning_cooldown_spin.setSuffix(" s")

        max_intensity = float(self._spectrometer_info.max_intensity)
        maximum_text = (
            f"{max_intensity:.0f} counts" if math.isfinite(max_intensity) else "unknown"
        )
        description = QLabel(
            f"Current spectrometer: {self._spectrometer_info.name or '--'}, "
            f"serial: {self._spectrometer_info.serial_number or '--'}, "
            f"max intensity: {maximum_text}"
        )
        description.setWordWrap(True)

        form.addRow(description)
        form.addRow("Enable warning", self.warning_enabled_check)
        form.addRow("Use spectrometer max", self.warning_use_max_check)
        form.addRow("Fraction of max", self.warning_fraction_spin)
        form.addRow("Absolute threshold", self.warning_abs_spin)
        form.addRow("Popup enabled", self.warning_popup_check)
        form.addRow("Popup cooldown", self.warning_cooldown_spin)
        return tab

    def _build_plot_style_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        settings = self._plot_style_settings

        self.spectrum_color_combo = self._color_combo(settings.spectrum_color)
        self.monitor_color_combo = self._color_combo(settings.monitor_color)
        self.power_color_combo = self._color_combo(settings.power_color)

        self.spectrum_line_width_spin = self._line_width_spin(
            settings.spectrum_line_width
        )
        self.monitor_line_width_spin = self._line_width_spin(
            settings.monitor_line_width
        )
        self.power_line_width_spin = self._line_width_spin(settings.power_line_width)

        self.spectrum_show_line_check = self._checked(settings.spectrum_show_line)
        self.monitor_show_line_check = self._checked(settings.monitor_show_line)
        self.power_show_line_check = self._checked(settings.power_show_line)
        self.spectrum_show_symbols_check = self._checked(
            settings.spectrum_show_symbols
        )
        self.monitor_show_symbols_check = self._checked(settings.monitor_show_symbols)
        self.power_show_symbols_check = self._checked(settings.power_show_symbols)

        self.symbol_combo = QComboBox()
        for label, value in (
            ("Circle", "o"),
            ("Square", "s"),
            ("Triangle", "t"),
            ("Diamond", "d"),
            ("Plus", "+"),
            ("None", ""),
        ):
            self.symbol_combo.addItem(label, value)
        self._set_combo_data(self.symbol_combo, settings.symbol)

        self.symbol_size_spin = QSpinBox()
        self.symbol_size_spin.setRange(1, 50)
        self.symbol_size_spin.setValue(int(settings.symbol_size))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 30)
        self.font_size_spin.setValue(int(settings.font_size))

        self.spectrum_auto_range_check = self._checked(settings.spectrum_auto_range)
        self.monitor_auto_range_check = self._checked(settings.monitor_auto_range)
        self.power_auto_range_check = self._checked(settings.power_auto_range)
        self.spectrum_x_min_spin = self._axis_spin(settings.spectrum_x_min, " nm")
        self.spectrum_x_max_spin = self._axis_spin(settings.spectrum_x_max, " nm")
        self.spectrum_y_min_spin = self._axis_spin(settings.spectrum_y_min)
        self.spectrum_y_max_spin = self._axis_spin(settings.spectrum_y_max)

        form.addRow("Spectrum color", self.spectrum_color_combo)
        form.addRow("Monitor color", self.monitor_color_combo)
        form.addRow("Power color", self.power_color_combo)
        form.addRow("Spectrum line width", self.spectrum_line_width_spin)
        form.addRow("Monitor line width", self.monitor_line_width_spin)
        form.addRow("Power line width", self.power_line_width_spin)
        form.addRow("Spectrum line", self.spectrum_show_line_check)
        form.addRow("Monitor line", self.monitor_show_line_check)
        form.addRow("Power line", self.power_show_line_check)
        form.addRow("Spectrum markers", self.spectrum_show_symbols_check)
        form.addRow("Monitor markers", self.monitor_show_symbols_check)
        form.addRow("Power markers", self.power_show_symbols_check)
        form.addRow("Marker symbol", self.symbol_combo)
        form.addRow("Marker size", self.symbol_size_spin)
        form.addRow("Font size", self.font_size_spin)
        form.addRow("Spectrum auto range", self.spectrum_auto_range_check)
        form.addRow("Monitor auto range", self.monitor_auto_range_check)
        form.addRow("Power auto range", self.power_auto_range_check)
        form.addRow("Spectrum x min", self.spectrum_x_min_spin)
        form.addRow("Spectrum x max", self.spectrum_x_max_spin)
        form.addRow("Spectrum y min", self.spectrum_y_min_spin)
        form.addRow("Spectrum y max", self.spectrum_y_max_spin)
        return tab

    @staticmethod
    def _checked(value: bool) -> QCheckBox:
        checkbox = QCheckBox()
        checkbox.setChecked(bool(value))
        return checkbox

    @staticmethod
    def _line_width_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 20.0)
        spin.setDecimals(1)
        spin.setValue(float(value))
        return spin

    @staticmethod
    def _axis_spin(value: float, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1.0e12, 1.0e12)
        spin.setDecimals(3)
        spin.setValue(float(value))
        spin.setSuffix(suffix)
        return spin

    @staticmethod
    def _set_combo_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _color_combo(current: str) -> QComboBox:
        combo = QComboBox()
        for label, value in (
            ("Yellow", "y"),
            ("Cyan", "c"),
            ("Green", "g"),
            ("Red", "r"),
            ("Blue", "b"),
            ("White", "w"),
            ("Black", "k"),
            ("Magenta", "m"),
        ):
            combo.addItem(label, value)
        AppSettingsDialog._set_combo_data(combo, current)
        return combo

    def _browse_save_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose save directory",
            self.save_directory_edit.text().strip() or ".",
        )
        if directory:
            self.save_directory_edit.setText(directory)

    def settings(
        self,
    ) -> tuple[
        FileNameSettings,
        PowerMonitorSettings,
        SignalWarningSettings,
        PlotStyleSettings,
    ]:
        file_settings = replace(
            self._file_settings,
            save_directory=Path(self.save_directory_edit.text().strip() or "data"),
            base_name=self.base_name_edit.text().strip() or "spectrum",
            run_identifier=self.run_identifier_edit.text().strip(),
            notes=self.notes_edit.toPlainText().strip(),
            include_date=self.include_date_check.isChecked(),
            include_time=self.include_time_check.isChecked(),
            include_power=self.include_power_check.isChecked(),
            include_field=self.include_field_check.isChecked(),
            include_run_identifier=self.include_run_identifier_check.isChecked(),
            include_enumeration=self.include_enum_check.isChecked(),
            autosave_spectra=self.autosave_check.isChecked(),
        )

        mode = str(self.power_mode_combo.currentData())
        power_settings = replace(
            self._power_settings,
            mode=mode,
            append_spectrum_power=self.append_spectrum_power_check.isChecked(),
            max_points=int(self.max_power_points_spin.value()),
            interval_ms=int(self.power_interval_spin.value()),
            validation_enabled=self.validation_enabled_check.isChecked(),
            max_valid_power_w=float(self.max_valid_power_mw_spin.value()) * 1e-3,
            invalid_power_retries=int(self.invalid_retries_spin.value()),
            invalid_power_retry_delay_s=float(self.invalid_retry_delay_spin.value()),
            validate_status_words=self.validate_status_check.isChecked(),
            reject_range_changing=self.reject_range_change_check.isChecked(),
            reject_detector_saturated=self.reject_saturated_check.isChecked(),
            reject_overrange=self.reject_overrange_check.isChecked(),
        )

        warning_settings = replace(
            self._warning_settings,
            enabled=self.warning_enabled_check.isChecked(),
            use_spectrometer_max=self.warning_use_max_check.isChecked(),
            fraction_of_spectrometer_max=float(self.warning_fraction_spin.value()),
            absolute_threshold_counts=float(self.warning_abs_spin.value()),
            popup_enabled=self.warning_popup_check.isChecked(),
            popup_cooldown_s=float(self.warning_cooldown_spin.value()),
        )

        plot_style_settings = replace(
            self._plot_style_settings,
            spectrum_color=str(self.spectrum_color_combo.currentData()),
            monitor_color=str(self.monitor_color_combo.currentData()),
            power_color=str(self.power_color_combo.currentData()),
            spectrum_line_width=float(self.spectrum_line_width_spin.value()),
            monitor_line_width=float(self.monitor_line_width_spin.value()),
            power_line_width=float(self.power_line_width_spin.value()),
            spectrum_show_line=self.spectrum_show_line_check.isChecked(),
            monitor_show_line=self.monitor_show_line_check.isChecked(),
            power_show_line=self.power_show_line_check.isChecked(),
            spectrum_show_symbols=self.spectrum_show_symbols_check.isChecked(),
            monitor_show_symbols=self.monitor_show_symbols_check.isChecked(),
            power_show_symbols=self.power_show_symbols_check.isChecked(),
            symbol=str(self.symbol_combo.currentData()),
            symbol_size=int(self.symbol_size_spin.value()),
            font_size=int(self.font_size_spin.value()),
            spectrum_auto_range=self.spectrum_auto_range_check.isChecked(),
            spectrum_x_min=float(self.spectrum_x_min_spin.value()),
            spectrum_x_max=float(self.spectrum_x_max_spin.value()),
            spectrum_y_min=float(self.spectrum_y_min_spin.value()),
            spectrum_y_max=float(self.spectrum_y_max_spin.value()),
            monitor_auto_range=self.monitor_auto_range_check.isChecked(),
            power_auto_range=self.power_auto_range_check.isChecked(),
        )

        return file_settings, power_settings, warning_settings, plot_style_settings
