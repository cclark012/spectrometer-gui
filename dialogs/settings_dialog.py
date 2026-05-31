# settings_dialog.py

from __future__ import annotations

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
    QGroupBox, # noqa
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

from core.settings import (
    FileNameSettings,
    PowerMonitorSettings,
    SignalWarningSettings,
    PlotStyleSettings
)
from core.records import SpectrometerInfo


class AppSettingsDialog(QDialog):
    def __init__(
        self,
        parent,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
        warning_settings: SignalWarningSettings,
        plot_style_settings: PlotStyleSettings,
        spectrometer_info: SpectrometerInfo,
    ):
        super().__init__(parent)

        self.setWindowTitle("Settings")
        self.resize(520, 420)

        self._file_settings = replace(file_settings)
        self._power_settings = replace(power_settings)
        self._warning_settings = replace(warning_settings)
        self._plot_style_settings = replace(plot_style_settings)
        self._spectrometer_info = spectrometer_info

        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_file_tab(), "File names")
        tabs.addTab(self._build_power_tab(), "Power monitor")
        tabs.addTab(self._build_warning_tab(), "Signal warning")
        tabs.addTab(self._build_plot_style_tab(), "Plot Style")

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _build_file_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        self.save_directory_edit = QLineEdit(str(self._file_settings.save_directory))

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_save_directory)

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.save_directory_edit)
        dir_layout.addWidget(browse_button)

        self.base_name_edit = QLineEdit(self._file_settings.base_name)

        self.include_date_check = QCheckBox()
        self.include_date_check.setChecked(self._file_settings.include_date)

        self.include_time_check = QCheckBox()
        self.include_time_check.setChecked(self._file_settings.include_time)

        self.include_power_check = QCheckBox()
        self.include_power_check.setChecked(self._file_settings.include_power)

        self.include_field_check = QCheckBox()
        self.include_field_check.setChecked(self._file_settings.include_field)

        self.include_enum_check = QCheckBox()
        self.include_enum_check.setChecked(self._file_settings.include_enumeration)

        self.autosave_check = QCheckBox()
        self.autosave_check.setChecked(self._file_settings.autosave_spectra)

        self.run_identifier_edit = QLineEdit(self._file_settings.run_identifier)

        self.include_run_identifier_check = QCheckBox()
        self.include_run_identifier_check.setChecked(self._file_settings.include_run_identifier)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlainText(self._file_settings.notes)
        self.notes_edit.setFixedHeight(90)

        layout.addRow("Save directory", dir_layout)
        layout.addRow("Base name", self.base_name_edit)
        layout.addRow("Run identifier", self.run_identifier_edit)
        layout.addRow("Include run identifier", self.include_run_identifier_check)
        layout.addRow("Include date", self.include_date_check)
        layout.addRow("Include time", self.include_time_check)
        layout.addRow("Include power", self.include_power_check)
        layout.addRow("Include magnetic field", self.include_field_check)
        layout.addRow("Automatic enumeration", self.include_enum_check)
        layout.addRow("Autosave spectra", self.autosave_check)
        layout.addRow("Notes", self.notes_edit)

        return tab

    def _build_power_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        self.power_polling_check = QCheckBox()
        self.power_polling_check.setChecked(self._power_settings.polling_enabled)

        self.append_spectrum_power_check = QCheckBox()
        self.append_spectrum_power_check.setChecked(self._power_settings.append_spectrum_power)

        self.max_power_points_spin = QSpinBox()
        self.max_power_points_spin.setRange(10, 1_000_000)
        self.max_power_points_spin.setValue(int(self._power_settings.max_points))

        self.power_interval_spin = QSpinBox()
        self.power_interval_spin.setRange(100, 3_600_000)
        self.power_interval_spin.setValue(int(self._power_settings.interval_ms))
        self.power_interval_spin.setSuffix(" ms")

        layout.addRow("Background polling", self.power_polling_check)
        layout.addRow("Add spectrum power readings to trace", self.append_spectrum_power_check)
        layout.addRow("Max displayed/saved points", self.max_power_points_spin)
        layout.addRow("Polling interval", self.power_interval_spin)

        note = QLabel(
            "Disabling background polling does not disable p_before/p_after readings "
            "during spectrum acquisition."
        )
        note.setWordWrap(True)
        layout.addRow(note)

        return tab

    def _build_warning_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        self.warning_enabled_check = QCheckBox()
        self.warning_enabled_check.setChecked(self._warning_settings.enabled)

        self.warning_use_max_check = QCheckBox()
        self.warning_use_max_check.setChecked(self._warning_settings.use_spectrometer_max)

        self.warning_fraction_spin = QDoubleSpinBox()
        self.warning_fraction_spin.setRange(0.1, 1.0)
        self.warning_fraction_spin.setDecimals(3)
        self.warning_fraction_spin.setSingleStep(0.01)
        self.warning_fraction_spin.setValue(float(self._warning_settings.fraction_of_spectrometer_max))

        self.warning_abs_spin = QDoubleSpinBox()
        self.warning_abs_spin.setRange(1.0, 1.0e9)
        self.warning_abs_spin.setDecimals(1)
        self.warning_abs_spin.setValue(float(self._warning_settings.absolute_threshold_counts))
        self.warning_abs_spin.setSuffix(" counts")

        self.warning_popup_check = QCheckBox()
        self.warning_popup_check.setChecked(self._warning_settings.popup_enabled)

        self.warning_cooldown_spin = QDoubleSpinBox()
        self.warning_cooldown_spin.setRange(0.0, 3600.0)
        self.warning_cooldown_spin.setDecimals(1)
        self.warning_cooldown_spin.setValue(float(self._warning_settings.popup_cooldown_s))
        self.warning_cooldown_spin.setSuffix(" s")

        max_text = (
            f"{self._spectrometer_info.max_intensity:.0f} counts"
            if self._spectrometer_info.max_intensity == self._spectrometer_info.max_intensity
            else "unknown"
        )

        spec_label = QLabel(
            f"Current spectrometer: {self._spectrometer_info.name}, "
            f"serial: {self._spectrometer_info.serial_number or '--'}, "
            f"max intensity: {max_text}"
        )
        spec_label.setWordWrap(True)

        layout.addRow(spec_label)
        layout.addRow("Enable warning", self.warning_enabled_check)
        layout.addRow("Use spectrometer max", self.warning_use_max_check)
        layout.addRow("Fraction of max", self.warning_fraction_spin)
        layout.addRow("Absolute threshold", self.warning_abs_spin)
        layout.addRow("Popup enabled", self.warning_popup_check)
        layout.addRow("Popup cooldown", self.warning_cooldown_spin)

        return tab

    def _build_plot_style_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        s = self._plot_style_settings

        self.spectrum_color_combo = self._color_combo(s.spectrum_color)
        self.monitor_color_combo = self._color_combo(s.monitor_color)
        self.power_color_combo = self._color_combo(s.power_color)

        self.spectrum_line_width_spin = QDoubleSpinBox()
        self.spectrum_line_width_spin.setRange(0.0, 20.0)
        self.spectrum_line_width_spin.setDecimals(1)
        self.spectrum_line_width_spin.setValue(float(s.spectrum_line_width))

        self.monitor_line_width_spin = QDoubleSpinBox()
        self.monitor_line_width_spin.setRange(0.0, 20.0)
        self.monitor_line_width_spin.setDecimals(1)
        self.monitor_line_width_spin.setValue(float(s.monitor_line_width))

        self.power_line_width_spin = QDoubleSpinBox()
        self.power_line_width_spin.setRange(0.0, 20.0)
        self.power_line_width_spin.setDecimals(1)
        self.power_line_width_spin.setValue(float(s.power_line_width))

        self.spectrum_show_line_check = QCheckBox()
        self.spectrum_show_line_check.setChecked(s.spectrum_show_line)

        self.monitor_show_line_check = QCheckBox()
        self.monitor_show_line_check.setChecked(s.monitor_show_line)

        self.power_show_line_check = QCheckBox()
        self.power_show_line_check.setChecked(s.power_show_line)

        self.spectrum_show_symbols_check = QCheckBox()
        self.spectrum_show_symbols_check.setChecked(s.spectrum_show_symbols)

        self.monitor_show_symbols_check = QCheckBox()
        self.monitor_show_symbols_check.setChecked(s.monitor_show_symbols)

        self.power_show_symbols_check = QCheckBox()
        self.power_show_symbols_check.setChecked(s.power_show_symbols)

        self.symbol_combo = QComboBox()
        for label, value in [
            ("Circle", "o"),
            ("Square", "s"),
            ("Triangle", "t"),
            ("Diamond", "d"),
            ("Plus", "+"),
            ("None", ""),
        ]:
            self.symbol_combo.addItem(label, value)

        symbol_index = self.symbol_combo.findData(s.symbol)
        if symbol_index >= 0:
            self.symbol_combo.setCurrentIndex(symbol_index)

        self.symbol_size_spin = QSpinBox()
        self.symbol_size_spin.setRange(1, 50)
        self.symbol_size_spin.setValue(int(s.symbol_size))

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 30)
        self.font_size_spin.setValue(int(s.font_size))

        self.spectrum_auto_range_check = QCheckBox()
        self.spectrum_auto_range_check.setChecked(s.spectrum_auto_range)

        self.spectrum_x_min_spin = QDoubleSpinBox()
        self.spectrum_x_min_spin.setRange(-1.0e9, 1.0e9)
        self.spectrum_x_min_spin.setDecimals(1)
        self.spectrum_x_min_spin.setValue(float(s.spectrum_x_min))

        self.spectrum_x_max_spin = QDoubleSpinBox()
        self.spectrum_x_max_spin.setRange(-1.0e9, 1.0e9)
        self.spectrum_x_max_spin.setDecimals(1)
        self.spectrum_x_max_spin.setValue(float(s.spectrum_x_max))

        self.spectrum_y_min_spin = QDoubleSpinBox()
        self.spectrum_y_min_spin.setRange(-1.0e12, 1.0e12)
        self.spectrum_y_min_spin.setDecimals(1)
        self.spectrum_y_min_spin.setValue(float(s.spectrum_y_min))

        self.spectrum_y_max_spin = QDoubleSpinBox()
        self.spectrum_y_max_spin.setRange(-1.0e12, 1.0e12)
        self.spectrum_y_max_spin.setDecimals(1)
        self.spectrum_y_max_spin.setValue(float(s.spectrum_y_max))

        layout.addRow("Spectrum color", self.spectrum_color_combo)
        layout.addRow("Monitor color", self.monitor_color_combo)
        layout.addRow("Power color", self.power_color_combo)

        layout.addRow("Spectrum line width", self.spectrum_line_width_spin)
        layout.addRow("Monitor line width", self.monitor_line_width_spin)
        layout.addRow("Power line width", self.power_line_width_spin)

        layout.addRow("Spectrum line", self.spectrum_show_line_check)
        layout.addRow("Monitor line", self.monitor_show_line_check)
        layout.addRow("Power line", self.power_show_line_check)

        layout.addRow("Spectrum markers", self.spectrum_show_symbols_check)
        layout.addRow("Monitor markers", self.monitor_show_symbols_check)
        layout.addRow("Power markers", self.power_show_symbols_check)

        layout.addRow("Marker symbol", self.symbol_combo)
        layout.addRow("Marker size", self.symbol_size_spin)
        layout.addRow("Font size", self.font_size_spin)

        layout.addRow("Spectrum auto range", self.spectrum_auto_range_check)
        layout.addRow("Spectrum x min", self.spectrum_x_min_spin)
        layout.addRow("Spectrum x max", self.spectrum_x_max_spin)
        layout.addRow("Spectrum y min", self.spectrum_y_min_spin)
        layout.addRow("Spectrum y max", self.spectrum_y_max_spin)

        return tab

    def _browse_save_directory(self) -> None:
        current = self.save_directory_edit.text().strip() or "."

        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose save directory",
            current,
        )

        if directory:
            self.save_directory_edit.setText(directory)

    def _color_combo(self, current: str) -> QComboBox:
        combo = QComboBox()

        items = [
            ("Yellow", "y"),
            ("Cyan", "c"),
            ("Green", "g"),
            ("Red", "r"),
            ("Blue", "b"),
            ("White", "w"),
            ("Black", "k"),
            ("Magenta", "m"),
        ]

        for label, value in items:
            combo.addItem(label, value)

        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)

        return combo

    def settings(
        self,
    ) -> tuple[
        FileNameSettings,
        PowerMonitorSettings,
        SignalWarningSettings,
        PlotStyleSettings,
    ]:
        file_settings = FileNameSettings(
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
            extension=".csv",
        )

        power_settings = PowerMonitorSettings(
            polling_enabled=self.power_polling_check.isChecked(),
            append_spectrum_power=self.append_spectrum_power_check.isChecked(),
            max_points=int(self.max_power_points_spin.value()),
            interval_ms=int(self.power_interval_spin.value()),
        )

        warning_settings = SignalWarningSettings(
            enabled=self.warning_enabled_check.isChecked(),
            use_spectrometer_max=self.warning_use_max_check.isChecked(),
            fraction_of_spectrometer_max=float(self.warning_fraction_spin.value()),
            absolute_threshold_counts=float(self.warning_abs_spin.value()),
            popup_enabled=self.warning_popup_check.isChecked(),
            popup_cooldown_s=float(self.warning_cooldown_spin.value()),
        )

        plot_style_settings = PlotStyleSettings(
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
        )

        return file_settings, power_settings, warning_settings, plot_style_settings
