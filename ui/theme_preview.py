from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ThemePreviewWidget(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setObjectName("themePreviewRoot")
        self.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground,
            True,
        )

        layout = QVBoxLayout(self)

        group = QGroupBox("Controls")
        form = QFormLayout(group)

        form.addRow("Text input", QLineEdit("Example"))
        form.addRow(
            "Combo box",
            QComboBox(),
        )

        combo = form.itemAt(
            form.rowCount() - 1,
            QFormLayout.ItemRole.FieldRole,
        ).widget()
        combo.addItems(["Option A", "Option B"])

        form.addRow("Spin box", QSpinBox())

        checked = QCheckBox("Checked")
        checked.setChecked(True)

        unchecked = QCheckBox("Unchecked")

        checks = QWidget()
        checks_layout = QHBoxLayout(checks)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.addWidget(checked)
        checks_layout.addWidget(unchecked)

        form.addRow("Check boxes", checks)

        form.addRow("Radio", QRadioButton("Option"))

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setValue(60)
        form.addRow("Slider", slider)

        progress = QProgressBar()
        progress.setValue(65)
        form.addRow("Progress", progress)

        layout.addWidget(group)

        button_row = QHBoxLayout()

        normal_button = QPushButton("Normal")
        accent_button = QPushButton("Primary")
        accent_button.setObjectName("primaryButton")

        disabled_button = QPushButton("Disabled")
        disabled_button.setEnabled(False)

        button_row.addWidget(normal_button)
        button_row.addWidget(accent_button)
        button_row.addWidget(disabled_button)

        layout.addLayout(button_row)

        table = QTableWidget(2, 2)
        table.setHorizontalHeaderLabels(["Name", "Value"])
        table.setItem(0, 0, QTableWidgetItem("Signal"))
        table.setItem(0, 1, QTableWidgetItem("123.4"))
        table.setItem(1, 0, QTableWidgetItem("Power"))
        table.setItem(1, 1, QTableWidgetItem("4.2 mW"))
        table.selectRow(0)
        layout.addWidget(table)

        self.plot = pg.PlotWidget()
        x = np.linspace(400.0, 800.0, 300)
        y = np.exp(-0.5 * ((x - 600.0) / 45.0) ** 2)
        self.plot.plot(x, y)
        self.plot.setMaximumHeight(150)

        layout.addWidget(self.plot)

    def apply_preview(
        self,
        *,
        palette,
        stylesheet: str,
        plot_background: str,
        plot_foreground: str,
    ) -> None:
        self.setPalette(palette)
        self.setStyleSheet(stylesheet)

        self.plot.setBackground(plot_background)

        for axis_name in ["bottom", "left"]:
            axis = self.plot.getAxis(axis_name)
            axis.setTextPen(plot_foreground)
            axis.setPen(plot_foreground)
