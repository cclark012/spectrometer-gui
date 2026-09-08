from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from core.gated_acquisition import GatedFrameMetadata
from core.records import PowerSnapshot, SpectrumRecord
from core.snr_records import SNRMetrics
from io_utils.atomic import atomic_text_writer


def save_spectrum_record(path: Path, record: SpectrumRecord) -> None:
    """Save a compact, conditional schema-v2 spectrum CSV.

    The adapter result is canonical raw data.  GUI-processed values are written
    as a second column only when background subtraction or smoothing changed
    the array.  The loader below continues to accept the eager schema-v1 rows.
    """

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    processed = np.asarray(record.intensities_counts, dtype=float)
    raw = (
        np.asarray(record.raw_intensities_counts, dtype=float)
        if record.raw_intensities_counts is not None
        else processed
    )
    if raw.shape != processed.shape or raw.shape != np.asarray(record.wavelengths_nm).shape:
        raise ValueError("Raw, processed, and wavelength spectrum arrays must match.")
    has_processed = not np.array_equal(raw, processed, equal_nan=True)

    with atomic_text_writer(output) as file:
        writer = csv.writer(file)

        writer.writerow(["# file_type", "spectrum"])
        writer.writerow(["# schema_version", 2])
        writer.writerow(["# timestamp_utc", record.timestamp_utc])
        if record.spectrometer_backend:
            writer.writerow(["# spectrometer_backend", record.spectrometer_backend])
        if record.spectrometer_model:
            writer.writerow(["# spectrometer_model", record.spectrometer_model])
        if record.spectrometer_serial:
            writer.writerow(["# spectrometer_serial", record.spectrometer_serial])
        if record.spectrograph_serial:
            writer.writerow(["# spectrograph_serial", record.spectrograph_serial])
        writer.writerow(["# integration_ms", record.integration_ms])
        acquisition_duration_ms = 1000.0 * (
            float(record.acquisition_finished_s) - float(record.acquisition_started_s)
        )
        if np.isfinite(acquisition_duration_ms):
            writer.writerow(
                ["# acquisition_call_duration_ms", f"{acquisition_duration_ms:.9f}"]
            )
        writer.writerow(["# averages_requested", record.averages])
        writer.writerow(["# averaging_mode_requested", record.averaging_mode])
        writer.writerow(
            [
                "# averaging_mode_applied",
                "device" if record.device_averaging_used else "software",
            ]
        )
        writer.writerow(["# correct_dark_applied", int(record.correct_dark)])
        writer.writerow(
            ["# correct_nonlinearity_applied", int(record.correct_nonlinearity)]
        )
        if np.isfinite(record.spectrometer_max_intensity):
            writer.writerow(
                [
                    "# spectrometer_max_intensity",
                    f"{record.spectrometer_max_intensity:.12e}",
                ]
            )

        if record.field_value is not None:
            writer.writerow(["# field_value_mT", f"{record.field_value:.12e}"])

        if record.p_before.powers_w or record.p_after.powers_w:
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

        if record.run_identifier:
            writer.writerow(["# run_identifier", record.run_identifier])
        if record.notes:
            writer.writerow(["# notes", record.notes])

        if record.scan_active:
            writer.writerow(["# scan_index", record.scan_index])
            writer.writerow(["# scan_count", record.scan_count])
            writer.writerow(["# scan_basis", record.scan_basis])
            writer.writerow(["# scan_spacing", record.scan_spacing])
        if record.scan_active or record.gated is not None or record.laser_channel >= 0:
            if record.laser_port:
                writer.writerow(["# laser_port", record.laser_port])
            if record.laser_box_id:
                writer.writerow(["# laser_box_id", record.laser_box_id])
            if record.laser_channel >= 0:
                writer.writerow(["# laser_channel", record.laser_channel])
            if np.isfinite(record.laser_wavelength_nm):
                writer.writerow(
                    ["# laser_wavelength_nm", f"{record.laser_wavelength_nm:.12e}"]
                )
            if np.isfinite(record.laser_setpoint_w):
                writer.writerow(["# laser_setpoint_W", f"{record.laser_setpoint_w:.12e}"])
        if record.scan_active:
            if np.isfinite(record.requested_power_w):
                writer.writerow(["# requested_power_W", f"{record.requested_power_w:.12e}"])
            if np.isfinite(record.expected_actual_power_w):
                writer.writerow(
                    ["# expected_actual_power_W", f"{record.expected_actual_power_w:.12e}"]
                )
            if record.filter_state and record.filter_state != "none":
                writer.writerow(["# filter_state", record.filter_state])

        if record.background_subtracted:
            writer.writerow(["# background_timestamp_utc", record.background_timestamp_utc])
            writer.writerow(["# background_integration_ms", record.background_integration_ms])
        if record.boxcar_width > 1:
            writer.writerow(["# smoothing_method", "boxcar"])
            writer.writerow(["# smoothing_width", record.boxcar_width])

        if record.gated is not None:
            writer.writerow(["# gated_sequence_id", record.gated.sequence_id])
            writer.writerow(["# gated_mode", record.gated.mode])
            writer.writerow(["# gated_frame_index", record.gated.frame_index])
            writer.writerow(["# gated_frame_count", record.gated.frame_count])
            writer.writerow(["# gated_cycle_index", record.gated.cycle_index])
            writer.writerow(["# gated_label", record.gated.label])
            writer.writerow(["# gated_laser_state", record.gated.laser_state])
            writer.writerow(["# gated_requested_delay_ms", record.gated.requested_delay_ms])
            writer.writerow([
                "# gated_request_elapsed_since_transition_ms",
                f"{record.gated.request_elapsed_since_transition_ms:.9f}",
            ])
            writer.writerow([
                "# gated_acquisition_call_start_elapsed_ms",
                f"{record.gated.acquisition_call_start_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_acquisition_call_midpoint_elapsed_ms",
                f"{record.gated.acquisition_call_midpoint_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_acquisition_call_end_elapsed_ms",
                f"{record.gated.acquisition_call_end_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_exposure_window_start_elapsed_ms",
                f"{record.gated.exposure_window_start_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_exposure_window_end_elapsed_ms",
                f"{record.gated.exposure_window_end_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_exposure_midpoint_estimate_elapsed_ms",
                f"{record.gated.exposure_midpoint_estimate_elapsed_ms:.9f}",
            ])
            writer.writerow([
                "# gated_exposure_timing_uncertainty_ms",
                f"{record.gated.exposure_timing_uncertainty_ms:.9f}",
            ])
            writer.writerow(["# gated_exposure_timing_basis", record.gated.exposure_timing_basis])
            if record.gated.exposure_sample_windows_elapsed_ms:
                writer.writerow(
                    [
                        "# gated_exposure_sample_start_elapsed_ms",
                        *[
                            f"{window[0]:.9f}"
                            for window in record.gated.exposure_sample_windows_elapsed_ms
                        ],
                    ]
                )
                writer.writerow(
                    [
                        "# gated_exposure_sample_end_elapsed_ms",
                        *[
                            f"{window[1]:.9f}"
                            for window in record.gated.exposure_sample_windows_elapsed_ms
                        ],
                    ]
                )
            writer.writerow(["# gated_timing_error_ms", f"{record.gated.timing_error_ms:.9f}"])
            writer.writerow(["# gated_timing_quality", record.gated.timing_quality])
            if np.isfinite(record.gated.timing_center_ms):
                writer.writerow(
                    ["# gated_timing_center_ms", f"{record.gated.timing_center_ms:.9f}"]
                )
            if np.isfinite(record.gated.timing_robust_sigma_ms):
                writer.writerow(
                    [
                        "# gated_timing_robust_sigma_ms",
                        f"{record.gated.timing_robust_sigma_ms:.9f}",
                    ]
                )
            if np.isfinite(record.gated.timing_threshold_ms):
                writer.writerow(
                    [
                        "# gated_timing_threshold_ms",
                        f"{record.gated.timing_threshold_ms:.9f}",
                    ]
                )
            writer.writerow(["# gated_phase_index", record.gated.phase_index])
            writer.writerow(["# gated_repeat_index", record.gated.repeat_index])

        if record.snr is not None:
            writer.writerow(["# snr_status", "ok" if record.snr.valid else "invalid"])
            if not record.snr.valid:
                writer.writerow(["# snr_reason", record.snr.message])
            else:
                writer.writerow(["# snr_peak", f"{record.snr.peak_snr:.12e}"])
                writer.writerow(["# snr_integrated", f"{record.snr.integrated_snr:.12e}"])
                writer.writerow(
                    ["# snr_noise_sigma_counts", f"{record.snr.noise_sigma_counts:.12e}"]
                )
                writer.writerow(
                    [
                        "# snr_peak_fraction",
                        f"{record.snr.peak_fraction_of_full_scale:.12e}",
                    ]
                )
                writer.writerow(
                    ["# snr_peak_signal_counts", f"{record.snr.peak_signal_counts:.12e}"]
                )
                writer.writerow(
                    [
                        "# snr_integrated_signal_counts_nm",
                        f"{record.snr.integrated_signal_counts_nm:.12e}",
                    ]
                )
                writer.writerow(
                    [
                        "# snr_integrated_noise_counts_nm",
                        f"{record.snr.integrated_noise_counts_nm:.12e}",
                    ]
                )
                writer.writerow(
                    ["# snr_mean_signal_counts", f"{record.snr.mean_signal_counts:.12e}"]
                )
                writer.writerow(
                    [
                        "# snr_baseline_at_signal_center_counts",
                        f"{record.snr.baseline_at_signal_center_counts:.12e}",
                    ]
                )
                writer.writerow(["# snr_n_signal_pixels", record.snr.n_signal_pixels])
                writer.writerow(["# snr_n_noise_pixels", record.snr.n_noise_pixels])

        header = ["wavelength_nm", "intensity_counts_raw"]
        if has_processed:
            header.append("intensity_counts_processed")
        writer.writerow(header)
        for index, wavelength in enumerate(record.wavelengths_nm):
            row = [f"{float(wavelength):.12e}", f"{float(raw[index]):.12e}"]
            if has_processed:
                row.append(f"{float(processed[index]):.12e}")
            writer.writerow(row)


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
) -> tuple[dict[str, list[str]], np.ndarray, np.ndarray, np.ndarray]:
    metadata: dict[str, list[str]] = {}
    wavelengths: list[float] = []
    intensities: list[float] = []
    raw_intensities: list[float] = []
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
                metadata[key] = [
                    value if key == "notes" else value.strip()
                    for value in row[1:]
                ]
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

            if "intensity_counts_processed" in header:
                intensity_index = header.index("intensity_counts_processed")
            elif "intensity_counts" in header:
                intensity_index = header.index("intensity_counts")
            elif "intensity_counts_raw" in header:
                intensity_index = header.index("intensity_counts_raw")
            else:
                intensity_index = 1
            raw_index = (
                header.index("intensity_counts_raw")
                if "intensity_counts_raw" in header
                else intensity_index
            )

            if len(row) <= max(wavelength_index, intensity_index, raw_index):
                continue

            try:
                wavelengths.append(float(row[wavelength_index]))
                intensities.append(float(row[intensity_index]))
                raw_intensities.append(float(row[raw_index]))
            except ValueError as exc:
                raise ValueError(f"Invalid spectrum data row in {path}: {row!r}") from exc

    if not wavelengths:
        raise ValueError(f"No spectrum data found in {path}")
    if len(wavelengths) != len(intensities) or len(wavelengths) != len(raw_intensities):
        raise ValueError(f"Mismatched wavelength/intensity columns in {path}")

    return (
        metadata,
        np.asarray(wavelengths, dtype=float),
        np.asarray(intensities, dtype=float),
        np.asarray(raw_intensities, dtype=float),
    )


def load_spectrum_record(path: Path) -> SpectrumRecord:
    """Load a saved spectrum while preserving available acquisition metadata.

    Legacy two-column files are accepted; missing metadata receives explicit
    sentinel/default values rather than fabricated acquisition settings.
    """

    metadata, wavelengths, intensities, raw_intensities = _read_spectrum_file(Path(path))
    schema_version = _int_value(metadata, "schema_version", 1)
    notes = _first(metadata, "notes")
    if schema_version < 2:
        notes = notes.replace("\\n", "\n")
    notes_json = _first(metadata, "notes_json")
    if notes_json:
        try:
            decoded_notes = json.loads(notes_json)
        except (TypeError, ValueError):
            pass
        else:
            if isinstance(decoded_notes, str):
                notes = decoded_notes
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
    gated = None
    if _bool_value(metadata, "gated_active") or "gated_sequence_id" in metadata:
        sample_starts = _float_list(
            metadata,
            "gated_exposure_sample_start_elapsed_ms",
        )
        sample_ends = _float_list(
            metadata,
            "gated_exposure_sample_end_elapsed_ms",
        )
        gated = GatedFrameMetadata(
            sequence_id=_first(metadata, "gated_sequence_id"),
            mode=_first(metadata, "gated_mode"),
            frame_index=_int_value(metadata, "gated_frame_index", -1),
            frame_count=_int_value(metadata, "gated_frame_count", 0),
            cycle_index=_int_value(metadata, "gated_cycle_index", -1),
            label=_first(metadata, "gated_label"),
            laser_state=_first(metadata, "gated_laser_state"),
            requested_delay_ms=_int_value(metadata, "gated_requested_delay_ms", 0),
            request_elapsed_since_transition_ms=_float_value(
                metadata,
                "gated_request_elapsed_since_transition_ms",
            ),
            acquisition_call_start_elapsed_ms=_float_value(
                metadata,
                "gated_acquisition_call_start_elapsed_ms",
            ),
            acquisition_call_midpoint_elapsed_ms=_float_value(
                metadata,
                "gated_acquisition_call_midpoint_elapsed_ms",
            ),
            acquisition_call_end_elapsed_ms=_float_value(
                metadata,
                "gated_acquisition_call_end_elapsed_ms",
            ),
            exposure_window_start_elapsed_ms=_float_value(
                metadata,
                "gated_exposure_window_start_elapsed_ms",
            ),
            exposure_window_end_elapsed_ms=_float_value(
                metadata,
                "gated_exposure_window_end_elapsed_ms",
            ),
            exposure_midpoint_estimate_elapsed_ms=_float_value(
                metadata,
                "gated_exposure_midpoint_estimate_elapsed_ms",
            ),
            exposure_timing_uncertainty_ms=_float_value(
                metadata,
                "gated_exposure_timing_uncertainty_ms",
            ),
            exposure_timing_basis=_first(
                metadata,
                "gated_exposure_timing_basis",
            ),
            exposure_sample_windows_elapsed_ms=tuple(zip(sample_starts, sample_ends, strict=True)),
            timing_error_ms=_float_value(metadata, "gated_timing_error_ms"),
            timing_quality=_first(
                metadata,
                "gated_timing_quality",
                "not_evaluated",
            ),
            timing_center_ms=_float_value(metadata, "gated_timing_center_ms"),
            timing_robust_sigma_ms=_float_value(
                metadata,
                "gated_timing_robust_sigma_ms",
            ),
            timing_threshold_ms=_float_value(metadata, "gated_timing_threshold_ms"),
            phase_index=_int_value(metadata, "gated_phase_index", -1),
            repeat_index=_int_value(metadata, "gated_repeat_index", 0),
        )

    snr_metrics = None
    if "snr_valid" in metadata or "snr_status" in metadata:
        snr_valid = (
            _first(metadata, "snr_status").strip().lower() == "ok"
            if "snr_status" in metadata
            else _bool_value(metadata, "snr_valid")
        )
        snr_metrics = SNRMetrics(
            valid=snr_valid,
            message=(
                "ok"
                if snr_valid
                else _first(metadata, "snr_reason", _first(metadata, "snr_message"))
            ),
            peak_snr=_float_value(metadata, "snr_peak"),
            integrated_snr=_float_value(metadata, "snr_integrated"),
            noise_sigma_counts=_float_value(metadata, "snr_noise_sigma_counts"),
            peak_fraction_of_full_scale=_float_value(metadata, "snr_peak_fraction"),
            peak_signal_counts=_float_value(metadata, "snr_peak_signal_counts"),
            integrated_signal_counts_nm=_float_value(
                metadata,
                "snr_integrated_signal_counts_nm",
            ),
            integrated_noise_counts_nm=_float_value(
                metadata,
                "snr_integrated_noise_counts_nm",
            ),
            mean_signal_counts=_float_value(metadata, "snr_mean_signal_counts"),
            baseline_at_signal_center_counts=_float_value(
                metadata,
                "snr_baseline_at_signal_center_counts",
            ),
            n_signal_pixels=_int_value(metadata, "snr_n_signal_pixels", 0),
            n_noise_pixels=_int_value(metadata, "snr_n_noise_pixels", 0),
        )

    return SpectrumRecord(
        timestamp_utc=_first(metadata, "timestamp_utc"),
        # perf_counter-based timestamps are meaningful only within the original
        # process, so loaded records use a neutral value.
        timestamp_s=0.0,
        wavelengths_nm=wavelengths,
        intensities_counts=intensities,
        raw_intensities_counts=raw_intensities,
        p_before=p_before,
        p_after=p_after,
        integration_ms=_int_value(metadata, "integration_ms", 0),
        averages=_int_value(
            metadata,
            "averages_requested",
            _int_value(metadata, "averages", 0),
        ),
        boxcar_width=_int_value(
            metadata,
            "smoothing_width",
            _int_value(metadata, "boxcar_width", 0),
        ),
        correct_dark=_bool_value(
            metadata,
            "correct_dark_applied",
            _bool_value(metadata, "correct_dark"),
        ),
        correct_nonlinearity=_bool_value(
            metadata,
            "correct_nonlinearity_applied",
            _bool_value(metadata, "correct_nonlinearity"),
        ),
        field_value=_float_value(metadata, "field_value_mT", 0.0),
        snr=snr_metrics,
        signal_max_counts=signal_max,
        spectrometer_max_intensity=_float_value(
            metadata,
            "spectrometer_max_intensity",
        ),
        run_identifier=_first(metadata, "run_identifier"),
        notes=notes,
        spectrometer_backend=_first(metadata, "spectrometer_backend"),
        spectrometer_model=_first(metadata, "spectrometer_model"),
        spectrometer_serial=_first(metadata, "spectrometer_serial"),
        spectrograph_serial=_first(metadata, "spectrograph_serial"),
        scan_active=_bool_value(metadata, "scan_active") or "scan_index" in metadata,
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
        averaging_mode=_first(
            metadata,
            "averaging_mode_requested",
            _first(metadata, "averaging_mode", "software"),
        ),
        device_averaging_used=(
            _first(metadata, "averaging_mode_applied").strip().lower() == "device"
            if "averaging_mode_applied" in metadata
            else _bool_value(metadata, "device_averaging_used")
        ),
        gated=gated,
        background_subtracted=(
            _bool_value(metadata, "background_subtracted")
            or "background_timestamp_utc" in metadata
        ),
        background_timestamp_utc=_first(metadata, "background_timestamp_utc"),
        background_integration_ms=_int_value(
            metadata,
            "background_integration_ms",
            0,
        ),
    )


def load_spectrum_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility loader returning only wavelength and intensity arrays."""

    _metadata, wavelengths, intensities, _raw = _read_spectrum_file(Path(path))
    return wavelengths, intensities
