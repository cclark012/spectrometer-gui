# controllers/laser_controller.py

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from devices.obis_adapter import (
    ObisBox,
    find_obis_boxes,
    make_emulated_obis_boxes,
)


class LaserController(QObject):
    lasers_ready = Signal(object)   # list[LaserChannelInfo]
    status = Signal(str)
    error = Signal(str)
    power_set_complete = Signal(str, int, float)
    enabled_set_complete = Signal(str, int, bool)

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

        self.boxes: dict[str, ObisBox] = {}

    @Slot()
    def refresh(self) -> None:
        self.status.emit(
            f"LaserController: emulate={self.emulate}, "
            f"fallback_emulator={self.fallback_emulator}, "
            f"candidate_ports={self.candidate_ports}"
        )
        try:
            self._close_boxes()

            if self.emulate:
                boxes = make_emulated_obis_boxes()
                self.status.emit("Laser mode: emulated OBIS boxes.")

            else:
                port_text = (
                    ", ".join(self.candidate_ports)
                    if self.candidate_ports
                    else "all available serial ports"
                )
                self.status.emit(f"Laser mode: real OBIS discovery on {port_text}.")

                boxes = find_obis_boxes(
                    candidate_ports=self.candidate_ports,
                    timeout_s=2.5,
                )

                if not boxes and self.fallback_emulator:
                    boxes = make_emulated_obis_boxes()
                    self.status.emit("No real OBIS boxes found. Using laser emulators.")

            self.boxes = {box.port: box for box in boxes}

            lasers = self._collect_lasers()

            self.lasers_ready.emit(lasers)

            box_summary = ", ".join(
                f"{port}: {box.box_id}"
                for port, box in self.boxes.items()
            )

            self.status.emit(
                f"Found {len(self.boxes)} OBIS box(es), "
                f"{len(lasers)} laser channel(s). "
                f"{box_summary}"
            )

        except Exception:
            self.error.emit(traceback.format_exc())

    def _collect_lasers(self) -> list:
        lasers = []

        for box in self.boxes.values():
            try:
                lasers.extend(box.discover_channels())
            except Exception:
                self.error.emit(traceback.format_exc())

        return lasers

    @Slot(str, int, float)
    def set_power_w(self, port: str, channel: int, power_w: float) -> None:
        try:
            box = self.boxes[str(port)]
            box.set_power_w(int(channel), float(power_w))

            self.status.emit(
                f"Set {port} ch{channel} to {float(power_w):.6e} W."
            )

            self.power_set_complete.emit(str(port), int(channel), float(power_w))

        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot(str, int, bool)
    def set_enabled(self, port: str, channel: int, enabled: bool) -> None:
        try:
            box = self.boxes[str(port)]
            box.set_enabled(int(channel), bool(enabled))

            state = "enabled" if enabled else "disabled"
            self.status.emit(f"{port} ch{channel} {state}.")

            self.enabled_set_complete.emit(str(port), int(channel), bool(enabled))

        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot()
    def disable_all(self) -> None:
        try:
            for box in self.boxes.values():
                box.disable_all()

            self.status.emit("Disable-all command sent to all connected OBIS boxes.")
            self.lasers_ready.emit(self._collect_lasers())

        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot(str, int, bool)
    def set_cdrh_delay(self, port: str, channel: int, enabled: bool) -> None:
        try:
            box = self.boxes[str(port)]
            box.set_cdrh_delay(int(channel), bool(enabled))

            state = "enabled" if enabled else "disabled"
            self.status.emit(f"CDRH delay {state} for {port} ch{channel}.")

            self.lasers_ready.emit(self._collect_lasers())

        except Exception:
            self.error.emit(traceback.format_exc())

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
