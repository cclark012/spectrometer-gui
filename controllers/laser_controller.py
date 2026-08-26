from __future__ import annotations

import traceback
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.laser_models import LaserChannelInfo, LaserEmissionState
from core.records import InstrumentConnectionState
from devices.emulated_obis import make_emulated_obis_boxes
from devices.obis_adapter import ObisInterlockError, find_obis_boxes
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
    connection_changed = Signal(object)

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
        self._laser_cache: dict[tuple[str, int], LaserChannelInfo] = {}
        self._verified_on: set[tuple[str, int]] = set()
        self._safety_timer = QTimer(self)
        self._safety_timer.setInterval(1000)
        self._safety_timer.timeout.connect(self._verify_enabled_channels)

    @Slot()
    def refresh(self) -> None:
        try:
            self._close_boxes()
            using_emulator = bool(self.emulate)

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
                    using_emulator = True
                    mode_message = "No real OBIS boxes found; using laser emulators."

            self.status.emit(mode_message)
            self.boxes = {box.port: box for box in boxes}
            lasers = self._collect_lasers()
            self._laser_cache = {
                (str(laser.port), int(laser.channel)): laser for laser in lasers
            }
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
            self.connection_changed.emit(
                InstrumentConnectionState(
                    key="lasers",
                    connected=bool(self.boxes),
                    emulated=bool(self.boxes) and using_emulator,
                    description=message,
                    error="",
                )
            )
        except Exception:
            message = traceback.format_exc()

            self.boxes = {}
            self._laser_cache.clear()
            self.lasers_ready.emit([])

            self.connection_changed.emit(
                InstrumentConnectionState(
                    key="lasers",
                    connected=False,
                    description="Laser connection failed.",
                    error=message,
                )
            )

            self.error.emit(message)

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

    def _update_safety_timer(self) -> None:
        if self._verified_on and self.boxes:
            if not self._safety_timer.isActive():
                self._safety_timer.start()
        else:
            self._safety_timer.stop()

    def _emit_cached_lasers(self) -> None:
        lasers = [
            laser
            for (port, _channel), laser in self._laser_cache.items()
            if port in self.boxes
        ]
        lasers.sort(key=lambda item: (str(item.port), int(item.channel)))
        self.lasers_ready.emit(lasers)

    def _update_cached_laser(self, port: str, channel: int, **values) -> None:
        key = (str(port), int(channel))
        laser = self._laser_cache.get(key)
        if laser is not None:
            self._laser_cache[key] = replace(laser, **values)

    def _clear_channel_off(
        self,
        box: LaserBoxAdapter,
        port: str,
        channel: int,
    ) -> tuple[LaserEmissionState, str]:
        """Best-effort OFF plus readback after a failed or unsafe ON state."""

        try:
            box.set_enabled(int(channel), False)
            actual = box.get_enabled(int(channel))
        except Exception as exc:
            self._update_cached_laser(
                port,
                channel,
                enabled=LaserEmissionState.UNKNOWN,
            )
            return LaserEmissionState.UNKNOWN, str(exc)
        self._update_cached_laser(port, channel, enabled=actual)
        return actual, ""

    def _remove_unresponsive_box(self, port: str, message: str) -> None:
        box = self.boxes.pop(str(port), None)
        if box is not None:
            try:
                box.close()
            except Exception:
                pass
        self._verified_on = {
            key for key in self._verified_on if key[0] != str(port)
        }
        self._laser_cache = {
            key: laser
            for key, laser in self._laser_cache.items()
            if key[0] != str(port)
        }
        self._update_safety_timer()
        self._emit_cached_lasers()
        self.connection_changed.emit(
            InstrumentConnectionState(
                key="lasers",
                connected=bool(self.boxes),
                description=(
                    f"OBIS box on {port} disconnected."
                    if not self.boxes
                    else f"OBIS box on {port} disconnected; other boxes remain available."
                ),
                error=str(message),
            )
        )

    def _box_still_responding(self, box: LaserBoxAdapter, channel: int) -> bool:
        try:
            box.get_enabled(int(channel))
            return True
        except Exception:
            return False

    def _handle_operation_failure(
        self,
        *,
        port: str,
        channel: int,
        message: str,
    ) -> None:
        box = self.boxes.get(str(port))
        if box is not None and not self._box_still_responding(box, channel):
            self._remove_unresponsive_box(port, message)

    @Slot(str, int, float)
    def set_power_w(self, port: str, channel: int, power_w: float) -> None:
        try:
            self._box(port).set_power_w(int(channel), float(power_w))
            self._update_cached_laser(
                str(port),
                int(channel),
                setpoint_w=float(power_w),
            )
            self.status.emit(f"Set {port} ch{channel} to {float(power_w):.6e} W.")
            self.power_set_complete.emit(str(port), int(channel), float(power_w))
        except Exception:
            message = traceback.format_exc()
            self._handle_operation_failure(
                port=str(port),
                channel=int(channel),
                message=message,
            )
            self.power_set_failed.emit(str(port), int(channel), message)
            self.error.emit(message)

    @Slot(str, int, bool)
    def set_enabled(self, port: str, channel: int, enabled: bool) -> None:
        port = str(port)
        channel = int(channel)
        requested = bool(enabled)
        try:
            box = self._box(port)
            try:
                box.set_enabled(channel, requested)
            except Exception:
                # An ON write with a lost acknowledgement may still have been
                # latched by the box. Best-effort OFF clears that unsafe state.
                if requested:
                    self._clear_channel_off(box, port, channel)
                raise

            actual = box.get_enabled(channel)
            desired = LaserEmissionState.ON if requested else LaserEmissionState.OFF
            if actual != desired:
                # The physical key/interlock can accept an ON command without
                # producing light, then apply that latched command when the key
                # is turned. Explicitly write OFF before reporting the mismatch.
                cleared_state, clear_detail = self._clear_channel_off(
                    box,
                    port,
                    channel,
                )
                clear_error = (
                    ""
                    if cleared_state == LaserEmissionState.OFF
                    else (
                        " OFF clear/readback also reported: "
                        f"{clear_detail or cleared_state.value}"
                    )
                )
                self._verified_on.discard((port, channel))
                self._update_safety_timer()
                self._emit_cached_lasers()
                raise ObisInterlockError(
                    f"{port} ch{channel} did not verify {desired.value}; "
                    f"readback was {actual.value}. "
                    + (
                        "The ON request was cleared. "
                        if not clear_error
                        else "An OFF clear was attempted but was not acknowledged. "
                    )
                    + "Check the Laser Box key/interlock and refresh before retrying."
                    + clear_error
                )

            if actual == LaserEmissionState.ON:
                self._verified_on.add((port, channel))
            else:
                self._verified_on.discard((port, channel))
            self._update_cached_laser(port, channel, enabled=actual)
            self._update_safety_timer()

            state = "enabled" if requested else "disabled"
            self.status.emit(f"{port} ch{channel} {state}.")
            self.enabled_set_complete.emit(port, channel, requested)
        except Exception as exc:
            message = traceback.format_exc()
            self._handle_operation_failure(
                port=port,
                channel=channel,
                message=message,
            )
            if port in self.boxes:
                self._emit_cached_lasers()
            self.enabled_set_failed.emit(port, channel, message)
            if isinstance(exc, ObisInterlockError):
                self.status.emit(str(exc))
            self.error.emit(message)

    @Slot()
    def _verify_enabled_channels(self) -> None:
        """Conditionally verify only channels that the GUI believes are ON."""

        refresh_needed = False
        for port, channel in tuple(self._verified_on):
            box = self.boxes.get(port)
            if box is None:
                self._verified_on.discard((port, channel))
                continue
            try:
                actual = box.get_enabled(channel)
            except Exception:
                message = traceback.format_exc()
                self._remove_unresponsive_box(port, message)
                self.enabled_set_failed.emit(port, channel, message)
                self.error.emit(message)
                continue

            if actual == LaserEmissionState.ON:
                continue

            # Key/interlock changed while emission was active. Clear any latched
            # ON request so restoring the key cannot unexpectedly re-enable it.
            cleared_state, _clear_detail = self._clear_channel_off(
                box,
                port,
                channel,
            )
            self._verified_on.discard((port, channel))
            refresh_needed = True
            message = (
                f"{port} ch{channel} emission changed to {actual.value}; "
                "the GUI issued OFF to clear the latched state "
                f"(readback: {cleared_state.value})."
            )
            self.enabled_set_failed.emit(port, channel, message)
            self.status.emit(message)

        self._update_safety_timer()
        if refresh_needed:
            self._emit_cached_lasers()

    @Slot(str, int, bool)
    def set_cdrh_delay(self, port: str, channel: int, enabled: bool) -> None:
        try:
            self._box(port).set_cdrh_delay(int(channel), bool(enabled))
            self._update_cached_laser(
                str(port),
                int(channel),
                cdrh_delay_enabled=bool(enabled),
            )
            state = "enabled" if enabled else "disabled"
            self.status.emit(f"CDRH delay {state} for {port} ch{channel}.")
            self.cdrh_set_complete.emit(str(port), int(channel), bool(enabled))
        except Exception:
            message = traceback.format_exc()
            self._handle_operation_failure(
                port=str(port),
                channel=int(channel),
                message=message,
            )
            self.cdrh_set_failed.emit(str(port), int(channel), message)
            self.error.emit(message)

    @Slot()
    def disable_all(self) -> None:
        errors: list[str] = []

        successful_ports: set[str] = set()
        for port, box in self.boxes.items():
            try:
                box.disable_all()
                successful_ports.add(str(port))
            except Exception as exc:
                errors.append(f"{port}: {exc}")

        self._verified_on.clear()
        self._update_safety_timer()

        for key, laser in tuple(self._laser_cache.items()):
            if key[0] in successful_ports:
                self._laser_cache[key] = replace(
                    laser,
                    enabled=LaserEmissionState.OFF,
                )
            elif key[0] in self.boxes:
                self._laser_cache[key] = replace(
                    laser,
                    enabled=LaserEmissionState.UNKNOWN,
                )
        self._emit_cached_lasers()

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
    def disconnect_all_boxes(self) -> None:
        self._close_boxes()
        self.lasers_ready.emit([])

        self.connection_changed.emit(
            InstrumentConnectionState(
                key="lasers",
                connected=False,
                description="Laser boxes disconnected.",
            )
        )

        self.status.emit("Laser boxes disconnected.")

    @Slot()
    def shutdown(self) -> None:
        self._close_boxes()

    def _close_boxes(self) -> None:
        self._safety_timer.stop()
        self._verified_on.clear()
        for box in self.boxes.values():
            try:
                box.close()
            except Exception:
                pass
        self.boxes = {}
        self._laser_cache.clear()
