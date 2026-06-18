from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from core.records import SpectrumRecord


def save_spectrum_record(path: Path, record: SpectrumRecord) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow(["# file_type", "spectrum"])
        writer.writerow(["# timestamp_utc", record.timestamp_utc])
        writer.writerow(["# integration_ms", record.integration_ms])
        writer.writerow(["# averages", record.averages])
        writer.writerow(["# boxcar_width", record.boxcar_width])
        writer.writerow(["# correct_dark", int(record.correct_dark)])
        writer.writerow(["# correct_nonlinearity", int(record.correct_nonlinearity)])
        writer.writerow(["# field_value_mT", f"{record.field_value:.12e}"])
        writer.writerow(["# p_before_W", *[f"{x:.12e}" for x in record.p_before.powers_w]])
        writer.writerow(["# p_after_W", *[f"{x:.12e}" for x in record.p_after.powers_w]])
        writer.writerow(["# p_before_status", *record.p_before.pm_status])
        writer.writerow(["# p_after_status", *record.p_after.pm_status])
        writer.writerow(["# run_identifier", record.run_identifier])
        writer.writerow(["# notes", record.notes.replace("\n", "\\n")])

        writer.writerow(["# scan_active", int(record.scan_active)])
        writer.writerow(["# scan_index", record.scan_index])
        writer.writerow(["# scan_count", record.scan_count])
        writer.writerow(["# scan_basis", record.scan_basis])
        writer.writerow(["# scan_spacing", record.scan_spacing])

        writer.writerow(["# laser_port", record.laser_port])
        writer.writerow(["# laser_box_id", record.laser_box_id])
        writer.writerow(["# laser_channel", record.laser_channel])
        writer.writerow(["# laser_wavelength_nm", f"{record.laser_wavelength_nm:.12e}"])
        writer.writerow(["# laser_setpoint_W", f"{record.laser_setpoint_w:.12e}"])
        writer.writerow(["# requested_power_W", f"{record.requested_power_w:.12e}"])
        writer.writerow(["# expected_actual_power_W", f"{record.expected_actual_power_w:.12e}"])
        writer.writerow(["# filter_state", record.filter_state])

        writer.writerow(["# averaging_mode", record.averaging_mode])
        writer.writerow(["# device_averaging_used", int(record.device_averaging_used)])

        writer.writerow(["# background_subtracted", int(record.background_subtracted)])
        writer.writerow(["# background_timestamp_utc", record.background_timestamp_utc])
        writer.writerow(["# background_integration_ms", record.background_integration_ms])

        writer.writerow(["wavelength_nm", "intensity_counts"])


        for wl, intensity in zip(record.wavelengths_nm, record.intensities_counts): # noqa
            writer.writerow([f"{float(wl):.12e}", f"{float(intensity):.12e}"])


def load_spectrum_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    wavelengths = []
    intensities = []

    with path.open("r", newline="") as f:
        reader = csv.reader(f)

        for row in reader:
            if not row:
                continue

            first = row[0].strip()

            if not first:
                continue

            if first.startswith("#"):
                continue

            if first.lower() in {"wavelength_nm", "wavelength", "wl"}:
                continue

            if len(row) < 2:
                continue

            wavelengths.append(float(row[0]))
            intensities.append(float(row[1]))

    if not wavelengths:
        raise ValueError(f"No spectrum data found in {path}")

    return np.asarray(wavelengths, dtype=float), np.asarray(intensities, dtype=float)

