from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from core.snr_records import SNRMetrics
from io_utils.atomic import atomic_text_writer


def save_spectrum_record(path: Path, record: SpectrumRecord) -> None:
    """Save spectrum data and acquisition metadata to CSV."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts_per_s = record.intensities_counts_per_s()

    with atomic_text_writer(output) as file:
        writer = csv.writer(file)

        writer.writerow(["# file_type", "spectrum"])
        writer.writerow(["# timestamp_utc", record.timestamp_utc])
        writer.writerow(["# integration_ms", record.integration_ms])
        writer.writerow(["# averages", record.averages])
        writer.writerow(["# boxcar_width", record.boxcar_width])
        writer.writerow(["# correct_dark", int(record.correct_dark)])
        writer.writerow(["# correct_nonlinearity", int(record.correct_nonlinearity)])
        writer.writerow(["# signal_max_counts", f"{record.signal_max_counts:.12e}"])
        writer.writerow(
            [
                "# spectrometer_max_intensity",
                f"{record.spectrometer_max_intensity:.12e}",
            ]
        )
        writer.writerow(["# field_value_mT", f"{record.field_value:.12e}"])
        writer.writerow(
            ["# p_before_W", *[f"{value:.12e}" for value in record.p_before.powers_w]]
        )
        writer.writerow(
            ["# p_after_W", *[f"{value:.12e}" for value in record.p_after.powers_w]]
        )
        writer.writerow(["# p_before_status", *record.p_before.pm_status])
        writer.writerow(["# p_after_status", *record.p_after.pm_status])
        writer.writerow(["# p_before_command_status", record.p_before.command_status])
        writer.writerow(["# p_after_command_status", record.p_after.command_status])
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
        writer.writerow(
            ["# expected_actual_power_W", f"{record.expected_actual_power_w:.12e}"]
        )
        writer.writerow(["# filter_state", record.filter_state])

        writer.writerow(["# averaging_mode", record.averaging_mode])
        writer.writerow(["# device_averaging_used", int(record.device_averaging_used)])
        writer.writerow(["# background_subtracted", int(record.background_subtracted)])
        writer.writerow(["# background_timestamp_utc", record.background_timestamp_utc])
        writer.writerow(["# background_integration_ms", record.background_integration_ms])

        if record.snr is not None:
            writer.writerow(["# snr_valid", int(record.snr.valid)])
            writer.writerow(["# snr_message", record.snr.message])
            writer.writerow(["# snr_peak", f"{record.snr.peak_snr:.12e}"])
            writer.writerow(["# snr_integrated", f"{record.snr.integrated_snr:.12e}"])
            writer.writerow(
                ["# snr_noise_sigma_counts", f"{record.snr.noise_sigma_counts:.12e}"]
            )
            writer.writerow(
                ["# snr_peak_fraction", f"{record.snr.peak_fraction_of_full_scale:.12e}"]
            )

        writer.writerow(
            [
                "wavelength_nm",
                "intensity_counts",
                "intensity_counts_per_s",
            ]
        )
        for wavelength, intensity, normalized in zip(
            record.wavelengths_nm,
            record.intensities_counts,
            counts_per_s,
            strict=True,
        ):
            writer.writerow(
                [
                    f"{float(wavelength):.12e}",
                    f"{float(intensity):.12e}",
                    f"{float(normalized):.12e}",
                ]
            )


def _first(metadata: dict[str, list[str]], key: str, default: str = "") -> str:
    values = metadata.get(key, [])
    return values[0] if values else default


def _float_value(
    metadata: dict[str, list[str]],
    key: str,
    default: float = float("nan"),
) -> float:
    try:
        return float(_first(metadata, key))
    except (TypeError, ValueError):
        return float(default)


def _int_value(
    metadata: dict[str, list[str]],
    key: str,
    default: int = 0,
) -> int:
    try:
        return int(float(_first(metadata, key)))
    except (TypeError, ValueError):
        return int(default)


def _bool_value(
    metadata: dict[str, list[str]],
    key: str,
    default: bool = False,
) -> bool:
    value = _first(metadata, key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _float_list(metadata: dict[str, list[str]], key: str) -> list[float]:
    values: list[float] = []
    for raw in metadata.get(key, []):
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return values


def _int_list(metadata: dict[str, list[str]], key: str) -> list[int]:
    values: list[int] = []
    for raw in metadata.get(key, []):
        try:
            values.append(int(float(raw)))
        except (TypeError, ValueError):
            continue
    return values


def _read_spectrum_file(
    path: Path,
) -> tuple[dict[str, list[str]], np.ndarray, np.ndarray]:
    metadata: dict[str, list[str]] = {}
    wavelengths: list[float] = []
    intensities: list[float] = []
    header: list[str] | None = None

    with Path(path).open("r", newline="", encoding="utf-8") as file:
        reader = csv.reader(file)
        for row in reader:
            if not row:
                continue

            first = row[0].strip()
            if not first:
                continue

            if first.startswith("#"):
                key = first.lstrip("#").strip()
                metadata[key] = [value.strip() for value in row[1:]]
                continue

            if header is None:
                lowered = [value.strip().lower() for value in row]
                if "wavelength_nm" in lowered or "wavelength" in lowered:
                    header = lowered
                    continue
                # Legacy two-column files may omit a header.
                header = ["wavelength_nm", "intensity_counts"]

            try:
                wavelength_index = header.index("wavelength_nm")
            except ValueError:
                try:
                    wavelength_index = header.index("wavelength")
                except ValueError:
                    wavelength_index = 0

            try:
                intensity_index = header.index("intensity_counts")
            except ValueError:
                intensity_index = 1

            if len(row) <= max(wavelength_index, intensity_index):
                continue

            try:
                wavelengths.append(float(row[wavelength_index]))
                intensities.append(float(row[intensity_index]))
            except ValueError as exc:
                raise ValueError(f"Invalid spectrum data row in {path}: {row!r}") from exc

    if not wavelengths:
        raise ValueError(f"No spectrum data found in {path}")
    if len(wavelengths) != len(intensities):
        raise ValueError(f"Mismatched wavelength/intensity columns in {path}")

    return (
        metadata,
        np.asarray(wavelengths, dtype=float),
        np.asarray(intensities, dtype=float),
    )


def load_spectrum_record(path: Path) -> SpectrumRecord:
    """Load a saved spectrum while preserving available acquisition metadata.

    Legacy two-column files are accepted; missing metadata receives explicit
    sentinel/default values rather than fabricated acquisition settings.
    """

    metadata, wavelengths, intensities = _read_spectrum_file(Path(path))
    signal_max = _float_value(metadata, "signal_max_counts")
    if not np.isfinite(signal_max):
        signal_max = float(np.nanmax(intensities))

    p_before = PowerSnapshot(
        powers_w=_float_list(metadata, "p_before_W"),
        pm_status=_int_list(metadata, "p_before_status"),
        command_status=_int_value(metadata, "p_before_command_status", -1),
    )
    p_after = PowerSnapshot(
        powers_w=_float_list(metadata, "p_after_W"),
        pm_status=_int_list(metadata, "p_after_status"),
        command_status=_int_value(metadata, "p_after_command_status", -1),
    )
    snr_metrics = None
    valid = _first(metadata, "snr_valid", False)
    if valid:
        snr_metrics = SNRMetrics(
            valid=valid,
            message=_first(metadata, "snr_message"),
            peak_snr=_float_value(metadata, "snr_peak"),
            integrated_snr=_float_value(metadata, "snr_integrated"),
            noise_sigma_counts=_float_value(metadata, "snr_noise_sigma_counts"),
            peak_fraction_of_full_scale=_float_value(metadata, "snr_peak_fraction"),
            # TODO - Finish this 
        )

    return SpectrumRecord(
        timestamp_utc=_first(metadata, "timestamp_utc"),
        # perf_counter-based timestamps are meaningful only within the original
        # process, so loaded records use a neutral value.
        timestamp_s=0.0,
        wavelengths_nm=wavelengths,
        intensities_counts=intensities,
        p_before=p_before,
        p_after=p_after,
        integration_ms=_int_value(metadata, "integration_ms", 0),
        averages=_int_value(metadata, "averages", 0),
        boxcar_width=_int_value(metadata, "boxcar_width", 0),
        correct_dark=_bool_value(metadata, "correct_dark"),
        correct_nonlinearity=_bool_value(metadata, "correct_nonlinearity"),
        field_value=_float_value(metadata, "field_value_mT", 0.0),
        snr=snr_metrics,
        signal_max_counts=signal_max,
        spectrometer_max_intensity=_float_value(
            metadata,
            "spectrometer_max_intensity",
        ),
        run_identifier=_first(metadata, "run_identifier"),
        notes=_first(metadata, "notes").replace("\\n", "\n"),
        scan_active=_bool_value(metadata, "scan_active"),
        scan_index=_int_value(metadata, "scan_index", -1),
        scan_count=_int_value(metadata, "scan_count", 0),
        scan_basis=_first(metadata, "scan_basis"),
        scan_spacing=_first(metadata, "scan_spacing"),
        laser_port=_first(metadata, "laser_port"),
        laser_box_id=_first(metadata, "laser_box_id"),
        laser_channel=_int_value(metadata, "laser_channel", -1),
        laser_wavelength_nm=_float_value(metadata, "laser_wavelength_nm"),
        laser_setpoint_w=_float_value(metadata, "laser_setpoint_W"),
        requested_power_w=_float_value(metadata, "requested_power_W"),
        expected_actual_power_w=_float_value(metadata, "expected_actual_power_W"),
        filter_state=_first(metadata, "filter_state", "none"),
        averaging_mode=_first(metadata, "averaging_mode", "software"),
        device_averaging_used=_bool_value(metadata, "device_averaging_used"),
        background_subtracted=_bool_value(metadata, "background_subtracted"),
        background_timestamp_utc=_first(metadata, "background_timestamp_utc"),
        background_integration_ms=_int_value(
            metadata,
            "background_integration_ms",
            0,
        ),
    )


def load_spectrum_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility loader returning only wavelength and intensity arrays."""

    _metadata, wavelengths, intensities = _read_spectrum_file(Path(path))
    return wavelengths, intensities
