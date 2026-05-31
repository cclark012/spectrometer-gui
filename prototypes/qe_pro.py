from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import csv
import json

import numpy as np


UTC = timezone.utc


@dataclass(slots=True)
class Spectrum:
    timestamp_utc: str
    model: str
    serial_number: str
    integration_time_ms: float
    averages: int
    wavelengths_nm: np.ndarray
    intensities: np.ndarray


class QEProSpectrometer:
    def __init__(self, serial_number: Optional[str] = None, backend: Optional[str] = None) -> None:
        self._requested_serial = serial_number
        self._backend = backend
        self._spec = None

    def open(self) -> None:
        if self._backend:
            import seabreeze  # type: ignore

            seabreeze.use(self._backend)

        from seabreeze.spectrometers import Spectrometer  # type: ignore

        if self._requested_serial:
            self._spec = Spectrometer.from_serial_number(self._requested_serial)
        else:
            self._spec = Spectrometer.from_first_available()

    def close(self) -> None:
        if self._spec is not None:
            try:
                self._spec.close()
            finally:
                self._spec = None

    def __enter__(self) -> "QEProSpectrometer":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def model(self) -> str:
        if self._spec is None:
            raise RuntimeError("Spectrometer is not open")
        return str(self._spec.model)

    @property
    def serial_number(self) -> str:
        if self._spec is None:
            raise RuntimeError("Spectrometer is not open")
        return str(self._spec.serial_number)

    def set_integration_time_ms(self, integration_time_ms: float) -> None:
        if self._spec is None:
            raise RuntimeError("Spectrometer is not open")
        micros = int(round(integration_time_ms * 1000.0))
        if micros <= 0:
            raise ValueError("integration_time_ms must be > 0")
        self._spec.integration_time_micros(micros)

    def acquire_spectrum(self, integration_time_ms: float, averages: int = 1) -> Spectrum:
        if self._spec is None:
            raise RuntimeError("Spectrometer is not open")
        if averages < 1:
            raise ValueError("averages must be >= 1")

        self.set_integration_time_ms(integration_time_ms)
        wavelengths = np.asarray(self._spec.wavelengths(), dtype=np.float64)
        stack = []
        for _ in range(averages):
            stack.append(np.asarray(self._spec.intensities(), dtype=np.float64))
        intensities = np.mean(np.stack(stack, axis=0), axis=0)

        return Spectrum(
            timestamp_utc=datetime.now(UTC).isoformat(timespec="milliseconds"),
            model=self.model,
            serial_number=self.serial_number,
            integration_time_ms=float(integration_time_ms),
            averages=int(averages),
            wavelengths_nm=wavelengths,
            intensities=intensities,
        )

    @staticmethod
    def save_csv(path: str | Path, spectrum: Spectrum) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            handle.write(f"# timestamp_utc: {spectrum.timestamp_utc}\n")
            handle.write(f"# model: {spectrum.model}\n")
            handle.write(f"# serial_number: {spectrum.serial_number}\n")
            handle.write(f"# integration_time_ms: {spectrum.integration_time_ms}\n")
            handle.write(f"# averages: {spectrum.averages}\n")
            writer = csv.writer(handle)
            writer.writerow(["wavelength_nm", "intensity_au"])
            for wl, inten in zip(spectrum.wavelengths_nm, spectrum.intensities, strict=True):
                writer.writerow([f"{wl:.8f}", f"{inten:.8f}"])
        return path

    @staticmethod
    def save_metadata_json(path: str | Path, spectrum: Spectrum, extra: Optional[dict] = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": spectrum.timestamp_utc,
            "model": spectrum.model,
            "serial_number": spectrum.serial_number,
            "integration_time_ms": spectrum.integration_time_ms,
            "averages": spectrum.averages,
            "points": int(spectrum.wavelengths_nm.size),
        }
        if extra:
            payload.update(extra)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


def list_spectrometers(backend: Optional[str] = None) -> list[dict[str, str]]:
    if backend:
        import seabreeze  # type: ignore

        seabreeze.use(backend)

    from seabreeze.spectrometers import list_devices  # type: ignore

    devices = []
    for dev in list_devices():
        model = getattr(dev, "model", None)
        serial_number = getattr(dev, "serial_number", None)
        devices.append(
            {
                "model": str(model) if model is not None else "unknown",
                "serial_number": str(serial_number) if serial_number is not None else "unknown",
            }
        )
    return devices
