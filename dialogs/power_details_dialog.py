# dialogs/power_details_dialog.py

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.records import PowerTracePoint


class PowerDetailsDialog(QDialog):
    def __init__(self, points: list[PowerTracePoint], parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Power Trace Details")
        self.resize(900, 500)

        layout = QVBoxLayout(self)

        table = QTableWidget(self)
        table.setRowCount(len(points))
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels(
            [
                "timestamp_utc",
                "elapsed_s",
                "source",
                "ch1_power_W",
                "ch2_power_W",
                "ch1_status",
                "ch2_status",
                "command_status",
            ]
        )

        for row, point in enumerate(points):
            ch1 = point.powers_w[0] if len(point.powers_w) >= 1 else ""
            ch2 = point.powers_w[1] if len(point.powers_w) >= 2 else ""
            st1 = point.pm_status[0] if len(point.pm_status) >= 1 else ""
            st2 = point.pm_status[1] if len(point.pm_status) >= 2 else ""

            values = [
                point.timestamp_utc,
                f"{point.elapsed_s:.6f}",
                point.source,
                f"{ch1:.12e}" if ch1 != "" else "",
                f"{ch2:.12e}" if ch2 != "" else "",
                str(st1),
                str(st2),
                str(point.command_status),
            ]

            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))

        table.resizeColumnsToContents()
        layout.addWidget(table)
