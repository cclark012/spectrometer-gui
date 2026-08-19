from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.records import MonitorTracePoint
from processing.monitor_metrics import monitor_y

try:
    import pyqtgraph.opengl as gl

    HAS_GL_3D = True
except Exception:
    gl = None
    HAS_GL_3D = False


def normalize_01(values: list[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return array

    finite = np.isfinite(array)
    if not np.any(finite):
        return np.zeros_like(array)

    minimum = float(np.min(array[finite]))
    maximum = float(np.max(array[finite]))
    result = np.zeros_like(array)
    if maximum == minimum:
        result[finite] = 0.5
    else:
        result[finite] = (array[finite] - minimum) / (maximum - minimum)
    return result


def finite_min_max(values: list[float]) -> tuple[float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return float("nan"), float("nan")
    return float(np.min(array)), float(np.max(array))


def format_range_value(value: float, units: str) -> str:
    if not math.isfinite(float(value)):
        return "--"

    value = float(value)
    if units == "W":
        if abs(value) >= 1e-3:
            return f"{value * 1e3:.3g} mW"
        if abs(value) >= 1e-6:
            return f"{value * 1e6:.3g} μW"
        if abs(value) >= 1e-9:
            return f"{value * 1e9:.3g} nW"
        return f"{value:.3e} W"
    return f"{value:.3g} {units}".strip()


def collect_field_power_quantity(
    points: list[MonitorTracePoint],
    quantity_mode: str,
) -> tuple[list[float], list[float], list[float], str, str]:
    fields: list[float] = []
    powers: list[float] = []
    quantities: list[float] = []
    label = "Signal"
    units = ""

    for point in points:
        quantity, label, units = monitor_y(point, quantity_mode)
        field = float(point.field_mT)
        power = float(point.power_ch1_W)
        if all(math.isfinite(value) for value in (field, power, quantity)):
            fields.append(field)
            powers.append(power)
            quantities.append(float(quantity))

    return fields, powers, quantities, label, units


class FieldPowerMapView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel(
            "Field-power map: x = magnetic field, y = power ch1, "
            "color = selected quantity"
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Magnetic field (mT)")
        self.plot.setLabel("left", "Power ch1 (W)")
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        self.scatter = pg.ScatterPlotItem()
        self.plot.addItem(self.scatter)
        layout.addWidget(self.plot, stretch=1)

    def clear(self) -> None:
        self.scatter.setData([])
        self.info_label.setText("Field-power map: no finite monitor points")

    def set_data(self, points: list[MonitorTracePoint], quantity_mode: str) -> None:
        fields, powers, quantities, label, units = collect_field_power_quantity(
            points,
            quantity_mode,
        )
        if not fields:
            self.clear()
            return

        colors = normalize_01(quantities)
        spots = [
            {
                "pos": (field, power),
                "brush": pg.mkBrush(
                    int(60 + 180 * normalized),
                    int(170 - 70 * normalized),
                    int(255 - 120 * normalized),
                    210,
                ),
                "pen": pg.mkPen(40, 40, 40, 100),
                "size": 8,
                "data": quantity,
            }
            for field, power, quantity, normalized in zip( # noqa
                fields, powers, quantities, colors,
            ) 
        ]
        self.scatter.setData(spots)

        field_min, field_max = finite_min_max(fields)
        power_min, power_max = finite_min_max(powers)
        quantity_min, quantity_max = finite_min_max(quantities)
        self.info_label.setText(
            "Field-power map: "
            f"x = field [{field_min:.4g} to {field_max:.4g} mT], "
            f"y = power [{format_range_value(power_min, 'W')} to "
            f"{format_range_value(power_max, 'W')}], "
            f"color = {label} [{format_range_value(quantity_min, units)} to "
            f"{format_range_value(quantity_max, units)}]"
        )

    def apply_font(self, font: QFont) -> None:
        for axis_name in ("bottom", "left"):
            self.plot.getAxis(axis_name).setTickFont(font)


if HAS_GL_3D:

    class Monitor3DView(QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            self.info_label = QLabel(
                "3D monitor: x = magnetic field, y = power, z = selected quantity"
            )
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            self.view = gl.GLViewWidget()
            self.view.setCameraPosition(distance=2.6, elevation=24, azimuth=42)
            layout.addWidget(self.view, stretch=1)

            self.scatter = gl.GLScatterPlotItem()
            self.view.addItem(self.scatter)
            self._axis_items: list[object] = []
            self._static_labels: list[object] = []
            self._dynamic_labels: list[object] = []
            self._build_axes()

        def _add_line(self, points, *, color, width: float):
            item = gl.GLLinePlotItem(
                pos=np.asarray(points, dtype=float),
                color=color,
                width=float(width),
                antialias=True,
                mode="line_strip",
            )
            self.view.addItem(item)
            return item

        def _add_text(self, *, pos, text: str, color, size: int):
            item = gl.GLTextItem(
                pos=pos,
                text=str(text),
                color=color,
                font=QFont("Arial", int(size)),
            )
            self.view.addItem(item)
            return item

        def _remove_items(self, items: list[object]) -> None:
            for item in items:
                try:
                    self.view.removeItem(item)
                except Exception:
                    pass
            items.clear()

        def _build_axes(self) -> None:
            self._remove_items(self._axis_items)
            self._remove_items(self._static_labels)
            grid = (0.35, 0.35, 0.35, 0.35)

            self._axis_items.extend(
                [
                    self._add_line([(0, 0, 0), (1, 0, 0)], color=(0.9, 0.45, 0.45, 1), width=2.5),
                    self._add_line([(0, 0, 0), (0, 1, 0)], color=(0.45, 0.9, 0.45, 1), width=2.5),
                    self._add_line([(0, 0, 0), (0, 0, 1)], color=(0.45, 0.65, 1.0, 1), width=2.5),
                ]
            )

            cube_edges = [
                [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)],
                [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 1)],
                [(0, 0, 0), (0, 0, 1)],
                [(1, 0, 0), (1, 0, 1)],
                [(1, 1, 0), (1, 1, 1)],
                [(0, 1, 0), (0, 1, 1)],
            ]
            self._axis_items.extend(
                self._add_line(edge, color=grid, width=1.0) for edge in cube_edges
            )
            for value in np.linspace(0.25, 0.75, 3):
                self._axis_items.append(
                    self._add_line([(value, 0, 0), (value, 1, 0)], color=grid, width=0.75)
                )
                self._axis_items.append(
                    self._add_line([(0, value, 0), (1, value, 0)], color=grid, width=0.75)
                )

            self._static_labels.extend(
                [
                    self._add_text(
                        pos=(1.08, -0.04, 0),
                        text="Field",
                        color=(255, 150, 150, 255),
                        size=11,
                    ),
                    self._add_text(
                        pos=(-0.08, 1.08, 0),
                        text="Power",
                        color=(150, 255, 150, 255),
                        size=11,
                    ),
                    self._add_text(
                        pos=(-0.08, -0.05, 1.08),
                        text="Signal",
                        color=(150, 190, 255, 255),
                        size=11,
                    ),
                ]
            )

        def clear(self) -> None:
            self.scatter.setData(pos=np.empty((0, 3)))
            self._remove_items(self._dynamic_labels)
            self.info_label.setText("3D monitor: no finite monitor points")

        def _update_labels(
            self,
            fields: list[float],
            powers: list[float],
            quantities: list[float],
            label: str,
            units: str,
        ) -> None:
            self._remove_items(self._dynamic_labels)
            field_min, field_max = finite_min_max(fields)
            power_min, power_max = finite_min_max(powers)
            quantity_min, quantity_max = finite_min_max(quantities)
            labels = [
                ((0.00, -0.08, -0.02), format_range_value(field_min, "mT")),
                ((1.00, -0.08, -0.02), format_range_value(field_max, "mT")),
                ((-0.12, 0.00, -0.02), format_range_value(power_min, "W")),
                ((-0.12, 1.00, -0.02), format_range_value(power_max, "W")),
                ((-0.12, -0.08, 0.00), format_range_value(quantity_min, units)),
                ((-0.12, -0.08, 1.00), format_range_value(quantity_max, units)),
            ]
            for position, text in labels:
                self._dynamic_labels.append(
                    self._add_text(
                        pos=position,
                        text=text,
                        color=(230, 230, 230, 230),
                        size=9,
                    )
                )

            self.info_label.setText(
                "3D monitor: "
                f"x = field [{format_range_value(field_min, 'mT')} to "
                f"{format_range_value(field_max, 'mT')}], "
                f"y = power [{format_range_value(power_min, 'W')} to "
                f"{format_range_value(power_max, 'W')}], "
                f"z = {label} [{format_range_value(quantity_min, units)} to "
                f"{format_range_value(quantity_max, units)}]"
            )

        def set_data(self, points: list[MonitorTracePoint], quantity_mode: str) -> None:
            fields, powers, quantities, label, units = collect_field_power_quantity(
                points,
                quantity_mode,
            )
            if not fields:
                self.clear()
                return

            x = normalize_01(fields)
            y = normalize_01(powers)
            z = normalize_01(quantities)
            positions = np.column_stack([x, y, z])
            colors = np.zeros((positions.shape[0], 4), dtype=float)
            colors[:, 0] = 0.25 + 0.60 * z
            colors[:, 1] = 0.70
            colors[:, 2] = 1.00 - 0.45 * z
            colors[:, 3] = 0.88
            self.scatter.setData(pos=positions, color=colors, size=8, pxMode=True)
            self._update_labels(fields, powers, quantities, label, units)

else:
    Monitor3DView = None
