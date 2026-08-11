from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from core.laser_models import LaserChannelInfo
from devices.emulated_obis import make_emulated_obis_boxes
from devices.obis_adapter import find_obis_boxes
from devices.protocols import LaserBoxAdapter


class LaserController(QObject):
    lasers_ready = Signal(object)  # list[LaserChannelInfo]
    status = Signal(str)
    error = Signal(str)
    power_set_complete = Signal(str, int, float)
    enabled_set_complete = Signal(str, int, bool)
    cdrh_set_complete = Signal(str, int, bool)
    power_set_failed = Signal(str, int, str)
    enabled_set_failed = Signal(str, int, str)
    cdrh_set_failed = Signal(str, int, str)

    def __init__(
        self,
        *,
        emulate: bool = False,
        fallback_emulator: bool = False,
        candidate_ports: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.emulate = bool(emulate)
        self.fallback_emulator = bool(fallback_emulator)
        self.candidate_ports = candidate_ports
        self.boxes: dict[str, LaserBoxAdapter] = {}

    @Slot()
    def refresh(self) -> None:
        try:
            self._close_boxes()

            if self.emulate:
                boxes = make_emulated_obis_boxes()
                mode_message = "Laser mode: emulated OBIS boxes."
            else:
                port_text = (
                    ", ".join(self.candidate_ports)
                    if self.candidate_ports
                    else "all available serial ports"
                )
                mode_message = f"Laser mode: real OBIS discovery on {port_text}."
                boxes = find_obis_boxes(
                    candidate_ports=self.candidate_ports,
                    timeout_s=2.5,
                )
                if not boxes and self.fallback_emulator:
                    boxes = make_emulated_obis_boxes()
                    mode_message = "No real OBIS boxes found; using laser emulators."

            self.status.emit(mode_message)
            self.boxes = {box.port: box for box in boxes}
            lasers = self._collect_lasers()
            self.lasers_ready.emit(lasers)

            box_summary = ", ".join(
                f"{port}: {box.box_id}" for port, box in self.boxes.items()
            )
            message = (
                f"Found {len(self.boxes)} OBIS box(es), "
                f"{len(lasers)} laser channel(s)."
            )
            if box_summary:
                message += f" {box_summary}"
            self.status.emit(message)
        except Exception:
            self.error.emit(traceback.format_exc())

    def _collect_lasers(self) -> list[LaserChannelInfo]:
        lasers: list[LaserChannelInfo] = []
        for box in self.boxes.values():
            try:
                lasers.extend(box.discover_channels())
            except Exception:
                self.error.emit(traceback.format_exc())
        return lasers

    def _box(self, port: str) -> LaserBoxAdapter:
        try:
            return self.boxes[str(port)]
        except KeyError as exc:
            raise RuntimeError(f"No connected OBIS box on {port}.") from exc

    @Slot(str, int, float)
    def set_power_w(self, port: str, channel: int, power_w: float) -> None:
        try:
            self._box(port).set_power_w(int(channel), float(power_w))
            self.status.emit(f"Set {port} ch{channel} to {float(power_w):.6e} W.")
            self.power_set_complete.emit(str(port), int(channel), float(power_w))
        except Exception:
            message = traceback.format_exc()
            self.power_set_failed.emit(str(port), int(channel), message)
            self.error.emit(message)

    @Slot(str, int, bool)
    def set_enabled(self, port: str, channel: int, enabled: bool) -> None:
        try:
            self._box(port).set_enabled(int(channel), bool(enabled))
            state = "enabled" if enabled else "disabled"
            self.status.emit(f"{port} ch{channel} {state}.")
            self.enabled_set_complete.emit(str(port), int(channel), bool(enabled))
        except Exception:
            message = traceback.format_exc()
            self.enabled_set_failed.emit(str(port), int(channel), message)
            self.error.emit(message)

    @Slot(str, int, bool)
    def set_cdrh_delay(self, port: str, channel: int, enabled: bool) -> None:
        try:
            self._box(port).set_cdrh_delay(int(channel), bool(enabled))
            state = "enabled" if enabled else "disabled"
            self.status.emit(f"CDRH delay {state} for {port} ch{channel}.")
            self.cdrh_set_complete.emit(str(port), int(channel), bool(enabled))
        except Exception:
            message = traceback.format_exc()
            self.cdrh_set_failed.emit(str(port), int(channel), message)
            self.error.emit(message)

    @Slot()
    def disable_all(self) -> None:
        errors: list[str] = []

        for port, box in self.boxes.items():
            try:
                box.disable_all()
            except Exception as exc:
                errors.append(f"{port}: {exc}")

        # Refresh the table even if one box reported an error.
        self.lasers_ready.emit(self._collect_lasers())

        if errors:
            message = (
                "Disable All completed with one or more errors:\n"
                + "\n".join(errors)
            )
            self.error.emit(message)
            return

        self.status.emit(
            "Disable-all command sent to all connected OBIS boxes."
        )

    @Slot()
    def shutdown(self) -> None:
        self._close_boxes()

    def _close_boxes(self) -> None:
        for box in self.boxes.values():
            try:
                box.close()
            except Exception:
                pass
        self.boxes = {}
