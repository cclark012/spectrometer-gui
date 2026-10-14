from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from core.gated_acquisition import GatedFrameMetadata
from core.records import AcquisitionTiming, PowerSnapshot, SpectrumRecord
from core.snr_records import SNRMetrics
from io_utils.atomic import atomic_text_writer
from validation.power_status import newport_status_valid


def _float_text(value: float, *, digits: int = 12) -> str | None:
    number = float(value)
    return f"{number:.{digits}e}" if np.isfinite(number) else None


def _number_text(value: float, *, digits: int = 12) -> str | None:
    number = float(value)
    return f"{number:.{digits}g}" if np.isfinite(number) else None


def _write_group(writer, name: str, fields: list[tuple[str, object]]) -> None:
    """Write one compact metadata row containing named values."""

    row = [f"# {name}"]
    for key, value in fields:
        if value is None or (isinstance(value, str) and not value):
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        row.append(f"{key}={value}")
    if len(row) > 1:
        writer.writerow(row)


def _write_mapping_group(writer, name: str, values: dict[str, object]) -> None:
    fields: list[tuple[str, object]] = []
    for key, value in values.items():
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        fields.append((str(key), value))
    _write_group(writer, name, fields)


def _power_status_is_abnormal(snapshot: PowerSnapshot) -> bool:
    if int(snapshot.command_status) != 0:
        return True
    # Compact schemas intentionally omit normal status words. A loaded-and-resaved
    # file therefore has finite powers with an empty status list; absence is
    # not itself an abnormal reading. A partial, nonempty list is malformed.
    if snapshot.pm_status and len(snapshot.pm_status) != len(snapshot.powers_w):
        return True
    for value in snapshot.powers_w:
        if not np.isfinite(float(value)) or float(value) < 0.0:
            return True
    for status in snapshot.pm_status:
        try:
            valid, _reason = newport_status_valid(status)
        except (TypeError, ValueError):
            return True
        if not valid:
            return True
    return False


def _selected_power_snapshot(
    snapshot: PowerSnapshot,
    channels: tuple[int, ...],
) -> PowerSnapshot:
    """Return values in selected-channel order for compact persistence."""

    if not snapshot.powers_w:
        return PowerSnapshot(
            powers_w=[],
            pm_status=[],
            command_status=int(snapshot.command_status),
        )
    powers = [
        float(snapshot.powers_w[channel])
        if channel < len(snapshot.powers_w)
        else float("nan")
        for channel in channels
    ]
    statuses = (
        [
            int(snapshot.pm_status[channel])
            if channel < len(snapshot.pm_status)
            else -1
            for channel in channels
        ]
        if snapshot.pm_status
        else []
    )
    return PowerSnapshot(
        powers_w=powers,
        pm_status=statuses,
        command_status=int(snapshot.command_status),
    )


def _expand_selected_channel_values(
    values: list[float] | list[int],
    channels: tuple[int, ...],
    *,
    fill: float | int,
) -> list[float] | list[int]:
    """Restore compact values to physical channel indices after loading."""

    if not values or not channels or len(values) != len(channels):
        return list(values)
    expanded = [fill] * (max(channels) + 1)
    for channel, value in zip(channels, values, strict=True):
        expanded[channel] = value
    return expanded


def save_spectrum_record(path: Path, record: SpectrumRecord) -> None:
    """Save a compact, conditional schema-v5 spectrum CSV.

    The adapter result is canonical raw data. GUI-processed values are written
    as a second column only when background subtraction or smoothing changed
    the array. Closely related values share named-value rows, and normal Newport
    status words are omitted. The loader accepts schema v1 through v5.
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
    unsmoothed = record.unsmoothed_intensities_counts
    has_unsmoothed = unsmoothed is not None and record.smoothing_window != "none" and record.boxcar_width > 1
    if has_unsmoothed and np.asarray(unsmoothed).shape != raw.shape:
        raise ValueError("Unsmoothed spectrum shape must match raw data")

    with atomic_text_writer(output) as file:
        writer = csv.writer(file)

        writer.writerow(["# file_type", "spectrum"])
        writer.writerow(["# schema_version", 5])
        if record.parameter_scan:
            writer.writerow(["# parameter_scan_json", json.dumps(record.parameter_scan, allow_nan=False, sort_keys=True)])
        writer.writerow(["# timestamp_utc", record.timestamp_utc])
        _write_group(
            writer,
            "spectrometer",
            [
                ("backend", record.spectrometer_backend),
                ("model", record.spectrometer_model),
                ("serial", record.spectrometer_serial),
                ("spectrograph_serial", record.spectrograph_serial),
                ("max_intensity", _float_text(record.spectrometer_max_intensity)),
            ],
        )
        if record.software_provenance:
            _write_mapping_group(
                writer,
                "software_provenance",
                record.software_provenance,
            )
        if record.spectrometer_backend == "andor" and record.instrument_state:
            _write_mapping_group(writer, "andor_state", record.instrument_state)
        acquisition_duration_ms = 1000.0 * (
            float(record.acquisition_finished_s) - float(record.acquisition_started_s)
        )
        requested_averaging = str(record.averaging_mode)
        applied_averaging = "device" if record.device_averaging_used else "software"
        averaging_fields: list[tuple[str, object]]
        if requested_averaging == applied_averaging:
            averaging_fields = [("averaging", applied_averaging)]
        else:
            averaging_fields = [
                ("averaging_requested", requested_averaging),
                ("averaging_applied", applied_averaging),
            ]
        _write_group(
            writer,
            "acquisition",
            [
                ("integration_ms", _number_text(record.integration_ms)),
                (
                    "integration_requested_ms",
                    _number_text(record.requested_integration_ms)
                    if np.isfinite(float(record.requested_integration_ms))
                    and not np.isclose(
                        float(record.requested_integration_ms),
                        float(record.integration_ms),
                        rtol=0.0,
                        atol=1.0e-6,
                    )
                    else None,
                ),
                (
                    "call_duration_ms",
                    f"{acquisition_duration_ms:.9f}"
                    if np.isfinite(acquisition_duration_ms)
                    else None,
                ),
                ("averages", int(record.averages)),
                *averaging_fields,
            ],
        )
        timing = record.acquisition_timing
        if timing is not None:
            _write_group(
                writer,
                "detector_timing_ms",
                [
                    ("basis", timing.basis),
                    (
                        "exposure",
                        _number_text(1000.0 * timing.exposure_duration_s),
                    ),
                    (
                        "readout",
                        _number_text(1000.0 * timing.readout_duration_s),
                    ),
                    (
                        "keep_clean",
                        _number_text(1000.0 * timing.keep_clean_duration_s),
                    ),
                    (
                        "kinetic_cycle",
                        _number_text(1000.0 * timing.kinetic_cycle_s),
                    ),
                    ("mode", timing.acquisition_mode),
                    (
                        "driver_frame",
                        timing.driver_frame_index
                        if timing.driver_frame_index >= 0
                        else None,
                    ),
                    (
                        "discarded_before",
                        timing.discarded_frames_before
                        if timing.discarded_frames_before > 0
                        else None,
                    ),
                    (
                        "discarded_for_target",
                        timing.discarded_frames_for_target
                        if timing.discarded_frames_for_target > 0
                        else None,
                    ),
                    (
                        "pending_after_wait",
                        timing.pending_frames_after_wait
                        if timing.pending_frames_after_wait > 1
                        else None,
                    ),
                ],
            )
        _write_group(
            writer,
            "detector_corrections",
            [
                ("electric_dark", bool(record.correct_dark)),
                ("nonlinearity", bool(record.correct_nonlinearity)),
            ],
        )

        if record.field_value is not None and np.isfinite(float(record.field_value)):
            writer.writerow(["# field_value_mT", f"{record.field_value:.12e}"])

        selected_channels = tuple(
            dict.fromkeys(
                int(channel)
                for channel in record.power_channels
                if int(channel) >= 0
            )
        )
        if not selected_channels:
            selected_channels = tuple(
                range(max(len(record.p_before.powers_w), len(record.p_after.powers_w)))
            )
        selected_before = _selected_power_snapshot(
            record.p_before,
            selected_channels,
        )
        selected_after = _selected_power_snapshot(
            record.p_after,
            selected_channels,
        )
        if selected_before.powers_w or selected_after.powers_w:
            _write_group(
                writer,
                "power",
                [
                    (
                        "channels",
                        json.dumps(
                            [int(channel) + 1 for channel in selected_channels],
                            separators=(",", ":"),
                        ),
                    ),
                    (
                        "before_W",
                        json.dumps(selected_before.powers_w, separators=(",", ":")),
                    ),
                    (
                        "after_W",
                        json.dumps(selected_after.powers_w, separators=(",", ":")),
                    ),
                ],
            )
            if _power_status_is_abnormal(
                selected_before
            ) or _power_status_is_abnormal(
                selected_after
            ):
                _write_group(
                    writer,
                    "power_status_abnormal",
                    [
                        ("before_words", json.dumps(selected_before.pm_status)),
                        ("after_words", json.dumps(selected_after.pm_status)),
                        ("before_command", int(selected_before.command_status)),
                        ("after_command", int(selected_after.command_status)),
                    ],
                )

        if record.run_identifier:
            writer.writerow(["# run_identifier", record.run_identifier])
        if record.notes:
            writer.writerow(["# notes", record.notes])

        if record.scan_active:
            _write_group(
                writer,
                "scan",
                [
                    ("index", record.scan_index),
                    ("count", record.scan_count),
                    ("basis", record.scan_basis),
                    ("spacing", record.scan_spacing),
                ],
            )
        if record.scan_active or record.gated is not None or record.laser_channel >= 0:
            # The transient COM port is intentionally not spectrum provenance.
            _write_group(
                writer,
                "laser",
                [
                    ("box_id", record.laser_box_id),
                    ("channel", record.laser_channel if record.laser_channel >= 0 else None),
                    ("wavelength_nm", _float_text(record.laser_wavelength_nm)),
                    ("setpoint_W", _float_text(record.laser_setpoint_w)),
                ],
            )
        if record.scan_active:
            _write_group(
                writer,
                "power_scan",
                [
                    ("requested_W", _float_text(record.requested_power_w)),
                    ("expected_W", _float_text(record.expected_actual_power_w)),
                    (
                        "filter_state",
                        record.filter_state
                        if record.filter_state and record.filter_state != "none"
                        else None,
                    ),
                ],
            )

        if record.background_subtracted:
            _write_group(
                writer,
                "background",
                [
                    ("source_timestamp_utc", record.background_timestamp_utc),
                    ("signature_sha256", record.background_signature),
                ],
            )
        if record.smoothing_window != "none" and record.boxcar_width > 1:
            _write_group(
                writer,
                "smoothing",
                [
                    ("method", record.smoothing_window),
                    ("width", record.boxcar_width),
                    ("boundary", record.smoothing_boundary),
                ],
            )

        if record.gated is not None:
            gated = record.gated
            _write_group(
                writer,
                "gated_frame",
                [
                    ("sequence_id", gated.sequence_id),
                    ("mode", gated.mode),
                    ("index", gated.frame_index),
                    ("count", gated.frame_count),
                    ("cycle", gated.cycle_index),
                    ("label", gated.label),
                    ("laser_state", gated.laser_state),
                    ("requested_delay_ms", gated.requested_delay_ms),
                    ("phase", gated.phase_index),
                    ("repeat", gated.repeat_index),
                    ("repeats", gated.repeat_count),
                ],
            )
            _write_group(
                writer,
                "gated_plan_ms",
                [
                    ("pump", gated.pump_duration_ms),
                    ("frame_gap", gated.inter_frame_gap_ms),
                    ("repeat_gap", gated.inter_repeat_gap_ms),
                ],
            )
            _write_group(
                writer,
                "gated_call_timing_ms",
                [
                    ("request", _float_text(gated.request_elapsed_since_transition_ms, digits=9)),
                    ("start", _float_text(gated.acquisition_call_start_elapsed_ms, digits=9)),
                    ("midpoint", _float_text(gated.acquisition_call_midpoint_elapsed_ms, digits=9)),
                    ("end", _float_text(gated.acquisition_call_end_elapsed_ms, digits=9)),
                ],
            )
            _write_group(
                writer,
                "gated_exposure_timing_ms",
                [
                    ("start", _float_text(gated.exposure_window_start_elapsed_ms, digits=9)),
                    (
                        "midpoint",
                        _float_text(
                            gated.exposure_midpoint_estimate_elapsed_ms,
                            digits=9,
                        ),
                    ),
                    ("end", _float_text(gated.exposure_window_end_elapsed_ms, digits=9)),
                    ("uncertainty", _float_text(gated.exposure_timing_uncertainty_ms, digits=9)),
                    ("basis", gated.exposure_timing_basis),
                    ("transition_basis", gated.transition_timing_basis),
                ],
            )
            if record.gated.exposure_sample_windows_elapsed_ms:
                writer.writerow(
                    [
                        "# gated_sample_windows_ms",
                        json.dumps(
                            record.gated.exposure_sample_windows_elapsed_ms,
                            separators=(",", ":"),
                        ),
                    ]
                )
            _write_group(
                writer,
                "gated_quality",
                [
                    ("status", gated.timing_quality),
                    ("reference", gated.timing_reference),
                    ("error", _float_text(gated.timing_error_ms, digits=9)),
                    ("center", _float_text(gated.timing_center_ms, digits=9)),
                    ("robust_sigma", _float_text(gated.timing_robust_sigma_ms, digits=9)),
                    ("threshold", _float_text(gated.timing_threshold_ms, digits=9)),
                ],
            )

        if record.snr is not None:
            _write_group(
                writer,
                "snr",
                [
                    ("status", "ok" if record.snr.valid else "invalid"),
                    ("reason", record.snr.message if not record.snr.valid else None),
                    ("description", record.snr.message if record.snr.valid else None),
                ],
            )
            if record.snr.valid:
                _write_group(
                    writer,
                    "snr_metrics",
                    [
                        ("peak", _float_text(record.snr.peak_snr)),
                        ("integrated", _float_text(record.snr.integrated_snr)),
                        ("noise_sigma_counts", _float_text(record.snr.noise_sigma_counts)),
                        ("peak_fraction", _float_text(record.snr.peak_fraction_of_full_scale)),
                        ("peak_signal_counts", _float_text(record.snr.peak_signal_counts)),
                        (
                            "integrated_signal_counts_nm",
                            _float_text(record.snr.integrated_signal_counts_nm),
                        ),
                        (
                            "integrated_noise_counts_nm",
                            _float_text(record.snr.integrated_noise_counts_nm),
                        ),
                        ("mean_signal_counts", _float_text(record.snr.mean_signal_counts)),
                        (
                            "baseline_counts",
                            _float_text(
                                record.snr.baseline_at_signal_center_counts
                            ),
                        ),
                        ("signal_pixels", record.snr.n_signal_pixels),
                        ("noise_pixels", record.snr.n_noise_pixels),
                    ],
                )

        header = ["wavelength_nm", "intensity_counts_raw"]
        if has_processed:
            header.append("intensity_counts_processed")
        if has_unsmoothed:
            header.append("intensity_counts_unsmoothed")
        writer.writerow(header)
        for index, wavelength in enumerate(record.wavelengths_nm):
            row = [f"{float(wavelength):.12e}", f"{float(raw[index]):.12e}"]
            if has_processed:
                row.append(f"{float(processed[index]):.12e}")
            if has_unsmoothed:
                row.append(f"{float(unsmoothed[index]):.12e}")
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


_COMPACT_GROUP_ALIASES: dict[str, dict[str, str]] = {
    "spectrometer": {
        "backend": "spectrometer_backend",
        "model": "spectrometer_model",
        "serial": "spectrometer_serial",
        "spectrograph_serial": "spectrograph_serial",
        "max_intensity": "spectrometer_max_intensity",
    },
    "acquisition": {
        "integration_ms": "integration_ms",
        "integration_requested_ms": "integration_requested_ms",
        "call_duration_ms": "acquisition_call_duration_ms",
        "averages": "averages_requested",
        "averaging_requested": "averaging_mode_requested",
        "averaging_applied": "averaging_mode_applied",
    },
    "detector_timing_ms": {
        "basis": "acquisition_timing_basis",
        "exposure": "acquisition_exposure_duration_ms",
        "readout": "acquisition_readout_duration_ms",
        "keep_clean": "acquisition_keep_clean_duration_ms",
        "kinetic_cycle": "acquisition_kinetic_cycle_ms",
        "mode": "acquisition_mode",
        "driver_frame": "acquisition_driver_frame_index",
        "discarded_before": "acquisition_discarded_frames_before",
        "discarded_for_target": "acquisition_discarded_frames_for_target",
        "pending_after_wait": "acquisition_pending_frames_after_wait",
    },
    "detector_corrections": {
        "electric_dark": "correct_dark_applied",
        "nonlinearity": "correct_nonlinearity_applied",
    },
    "scan": {
        "index": "scan_index",
        "count": "scan_count",
        "basis": "scan_basis",
        "spacing": "scan_spacing",
    },
    "laser": {
        "box_id": "laser_box_id",
        "channel": "laser_channel",
        "wavelength_nm": "laser_wavelength_nm",
        "setpoint_W": "laser_setpoint_W",
    },
    "power_scan": {
        "requested_W": "requested_power_W",
        "expected_W": "expected_actual_power_W",
        "filter_state": "filter_state",
    },
    "background": {
        "source_timestamp_utc": "background_timestamp_utc",
        "signature_sha256": "background_signature_sha256",
    },
    "software_provenance": {
        "gui_version": "provenance_gui_version",
        "source_revision": "provenance_source_revision",
        "python": "provenance_python",
        "adapter": "provenance_adapter",
        "adapter_version": "provenance_adapter_version",
        "vendor_runtime": "provenance_vendor_runtime",
    },
    "andor_state": {
        "grating": "andor_grating",
        "center_wavelength_nm": "andor_center_wavelength_nm",
        "filter_position": "andor_filter_position",
        "focus_mirror_position": "andor_focus_mirror_position",
        "grating_offset": "andor_grating_offset",
        "output_amplifier": "andor_output_amplifier",
        "horizontal_readout_mhz": "andor_horizontal_readout_mhz",
        "vertical_shift_us": "andor_vertical_shift_us",
        "preamp_gain": "andor_preamp_gain",
        "horizontal_binning": "andor_horizontal_binning",
        "flipper_positions": "andor_flipper_positions",
        "detector_offsets": "andor_detector_offsets",
    },
    "smoothing": {
        "method": "smoothing_method",
        "width": "smoothing_width",
        "boundary": "smoothing_boundary",
    },
    "gated_frame": {
        "sequence_id": "gated_sequence_id",
        "mode": "gated_mode",
        "index": "gated_frame_index",
        "count": "gated_frame_count",
        "cycle": "gated_cycle_index",
        "label": "gated_label",
        "laser_state": "gated_laser_state",
        "requested_delay_ms": "gated_requested_delay_ms",
        "phase": "gated_phase_index",
        "repeat": "gated_repeat_index",
        "repeats": "gated_repeat_count",
    },
    "gated_plan_ms": {
        "pump": "gated_pump_duration_ms",
        "frame_gap": "gated_inter_frame_gap_ms",
        "repeat_gap": "gated_inter_repeat_gap_ms",
    },
    "gated_call_timing_ms": {
        "request": "gated_request_elapsed_since_transition_ms",
        "start": "gated_acquisition_call_start_elapsed_ms",
        "midpoint": "gated_acquisition_call_midpoint_elapsed_ms",
        "end": "gated_acquisition_call_end_elapsed_ms",
    },
    "gated_exposure_timing_ms": {
        "start": "gated_exposure_window_start_elapsed_ms",
        "midpoint": "gated_exposure_midpoint_estimate_elapsed_ms",
        "end": "gated_exposure_window_end_elapsed_ms",
        "uncertainty": "gated_exposure_timing_uncertainty_ms",
        "basis": "gated_exposure_timing_basis",
        "transition_basis": "gated_transition_timing_basis",
    },
    "gated_quality": {
        "status": "gated_timing_quality",
        "reference": "gated_timing_reference",
        "error": "gated_timing_error_ms",
        "center": "gated_timing_center_ms",
        "robust_sigma": "gated_timing_robust_sigma_ms",
        "threshold": "gated_timing_threshold_ms",
    },
    "snr": {"status": "snr_status", "reason": "snr_reason", "description": "snr_description"},
    "snr_metrics": {
        "peak": "snr_peak",
        "integrated": "snr_integrated",
        "noise_sigma_counts": "snr_noise_sigma_counts",
        "peak_fraction": "snr_peak_fraction",
        "peak_signal_counts": "snr_peak_signal_counts",
        "integrated_signal_counts_nm": "snr_integrated_signal_counts_nm",
        "integrated_noise_counts_nm": "snr_integrated_noise_counts_nm",
        "mean_signal_counts": "snr_mean_signal_counts",
        "baseline_counts": "snr_baseline_at_signal_center_counts",
        "signal_pixels": "snr_n_signal_pixels",
        "noise_pixels": "snr_n_noise_pixels",
    },
}


def _named_values(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if separator and key.strip():
            result[key.strip()] = content.strip()
    return result


def _json_metadata_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(value) for value in parsed]


def _typed_metadata_value(raw: str) -> object:
    text = str(raw).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _metadata_mapping(
    metadata: dict[str, list[str]],
    aliases: dict[str, str],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for output_key, metadata_key in aliases.items():
        if metadata_key in metadata:
            result[output_key] = _typed_metadata_value(
                _first(metadata, metadata_key)
            )
    return result


def _expand_compact_group(
    metadata: dict[str, list[str]],
    key: str,
    values: list[str],
) -> bool:
    """Expand compact schema-v3/v4 rows into the stable internal key model."""

    if key == "gated_sample_windows_ms":
        if not values:
            return True
        try:
            windows = json.loads(values[0])
        except (TypeError, ValueError):
            windows = []
        starts: list[str] = []
        ends: list[str] = []
        if isinstance(windows, list):
            for window in windows:
                if isinstance(window, (list, tuple)) and len(window) == 2:
                    starts.append(str(window[0]))
                    ends.append(str(window[1]))
        metadata["gated_exposure_sample_start_elapsed_ms"] = starts
        metadata["gated_exposure_sample_end_elapsed_ms"] = ends
        return True

    fields = _named_values(values)
    if key == "power":
        metadata["p_before_W"] = _json_metadata_list(fields.get("before_W", "[]"))
        metadata["p_after_W"] = _json_metadata_list(fields.get("after_W", "[]"))
        metadata["power_channels"] = _json_metadata_list(
            fields.get("channels", "[]")
        )
        return True
    if key == "power_status_abnormal":
        metadata["p_before_status"] = _json_metadata_list(
            fields.get("before_words", "[]")
        )
        metadata["p_after_status"] = _json_metadata_list(
            fields.get("after_words", "[]")
        )
        if "before_command" in fields:
            metadata["p_before_command_status"] = [fields["before_command"]]
        if "after_command" in fields:
            metadata["p_after_command_status"] = [fields["after_command"]]
        return True

    aliases = _COMPACT_GROUP_ALIASES.get(key)
    if aliases is None:
        return False
    for short_key, flat_key in aliases.items():
        if short_key in fields:
            metadata[flat_key] = [fields[short_key]]
    if key == "acquisition" and "averaging" in fields:
        metadata["averaging_mode_requested"] = [fields["averaging"]]
        metadata["averaging_mode_applied"] = [fields["averaging"]]
    return True


def _read_spectrum_file(
    path: Path,
) -> tuple[dict[str, list[str]], np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    metadata: dict[str, list[str]] = {}
    wavelengths: list[float] = []
    intensities: list[float] = []
    raw_intensities: list[float] = []
    unsmoothed_intensities: list[float] = []
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
                values = [
                    value if key == "notes" else value.strip()
                    for value in row[1:]
                ]
                if not _expand_compact_group(metadata, key, values):
                    metadata[key] = values
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

            required_index = max(wavelength_index, intensity_index, raw_index, header.index("intensity_counts_unsmoothed") if "intensity_counts_unsmoothed" in header else 0)
            if len(row) <= required_index:
                raise ValueError(f"Incomplete spectrum row in {path}: {row!r}")

            try:
                wavelengths.append(float(row[wavelength_index]))
                intensities.append(float(row[intensity_index]))
                raw_intensities.append(float(row[raw_index]))
                if "intensity_counts_unsmoothed" in header:
                    unsmoothed_intensities.append(float(row[header.index("intensity_counts_unsmoothed")]))
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
        np.asarray(unsmoothed_intensities, dtype=float) if unsmoothed_intensities else None,
    )


def load_spectrum_record(path: Path) -> SpectrumRecord:
    """Load a saved spectrum while preserving available acquisition metadata.

    Legacy two-column files are accepted; missing metadata receives explicit
    sentinel/default values rather than fabricated acquisition settings.
    """

    metadata, wavelengths, intensities, raw_intensities, unsmoothed = _read_spectrum_file(Path(path))
    file_type = _first(metadata, "file_type")
    if file_type in {"background", "analysis_spectrum", "response_profile"}:
        raise ValueError("Load this background, analysis spectrum or response profile in the Analysis tab.")
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

    saved_power_channels = tuple(
        max(0, value - 1) for value in _int_list(metadata, "power_channels")
    )
    before_powers = _float_list(metadata, "p_before_W")
    after_powers = _float_list(metadata, "p_after_W")
    before_statuses = _int_list(metadata, "p_before_status")
    after_statuses = _int_list(metadata, "p_after_status")
    if schema_version >= 4 and saved_power_channels:
        before_powers = list(
            _expand_selected_channel_values(
                before_powers,
                saved_power_channels,
                fill=float("nan"),
            )
        )
        after_powers = list(
            _expand_selected_channel_values(
                after_powers,
                saved_power_channels,
                fill=float("nan"),
            )
        )
        before_statuses = list(
            _expand_selected_channel_values(
                before_statuses,
                saved_power_channels,
                fill=-1,
            )
        )
        after_statuses = list(
            _expand_selected_channel_values(
                after_statuses,
                saved_power_channels,
                fill=-1,
            )
        )
    p_before = PowerSnapshot(
        powers_w=before_powers,
        pm_status=before_statuses,
        command_status=_int_value(
            metadata,
            "p_before_command_status",
            0 if before_powers else -1,
        ),
    )
    p_after = PowerSnapshot(
        powers_w=after_powers,
        pm_status=after_statuses,
        command_status=_int_value(
            metadata,
            "p_after_command_status",
            0 if after_powers else -1,
        ),
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
            transition_timing_basis=_first(
                metadata,
                "gated_transition_timing_basis",
            ),
            exposure_sample_windows_elapsed_ms=tuple(zip(sample_starts, sample_ends, strict=True)),
            timing_error_ms=_float_value(metadata, "gated_timing_error_ms"),
            timing_quality=_first(
                metadata,
                "gated_timing_quality",
                "not_evaluated",
            ),
            timing_reference=_first(
                metadata,
                "gated_timing_reference",
                "exposure_midpoint",
            ),
            timing_center_ms=_float_value(metadata, "gated_timing_center_ms"),
            timing_robust_sigma_ms=_float_value(
                metadata,
                "gated_timing_robust_sigma_ms",
            ),
            timing_threshold_ms=_float_value(metadata, "gated_timing_threshold_ms"),
            phase_index=_int_value(metadata, "gated_phase_index", -1),
            repeat_index=_int_value(metadata, "gated_repeat_index", 0),
            repeat_count=_int_value(metadata, "gated_repeat_count", 1),
            pump_duration_ms=(
                _int_value(metadata, "gated_pump_duration_ms")
                if "gated_pump_duration_ms" in metadata
                else None
            ),
            inter_frame_gap_ms=(
                _int_value(metadata, "gated_inter_frame_gap_ms")
                if "gated_inter_frame_gap_ms" in metadata
                else None
            ),
            inter_repeat_gap_ms=(
                _int_value(metadata, "gated_inter_repeat_gap_ms")
                if "gated_inter_repeat_gap_ms" in metadata
                else None
            ),
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
                _first(metadata, "snr_description", "ok")
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

    integration_ms = _float_value(metadata, "integration_ms", 0.0)
    background_present = (
        _bool_value(metadata, "background_subtracted")
        or "background_timestamp_utc" in metadata
    )

    return SpectrumRecord(
        timestamp_utc=_first(metadata, "timestamp_utc"),
        # perf_counter-based timestamps are meaningful only within the original
        # process, so loaded records use a neutral value.
        timestamp_s=0.0,
        wavelengths_nm=wavelengths,
        intensities_counts=intensities,
        raw_intensities_counts=raw_intensities,
        unsmoothed_intensities_counts=unsmoothed,
        parameter_scan=json.loads(_first(metadata, "parameter_scan_json", "{}")),
        p_before=p_before,
        p_after=p_after,
        integration_ms=integration_ms,
        requested_integration_ms=_float_value(
            metadata,
            "integration_requested_ms",
            integration_ms,
        ),
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
        # Missing means the field was intentionally not recorded.  Do not
        # fabricate 0 mT, which is itself a legitimate experimental value.
        field_value=(
            _float_value(metadata, "field_value_mT")
            if "field_value_mT" in metadata
            else None
        ),
        smoothing_window=_first(
            metadata,
            "smoothing_method",
            "boxcar" if _int_value(metadata, "boxcar_width", 0) > 1 else "none",
        ),
        smoothing_boundary=_first(metadata, "smoothing_boundary", "nearest"),
        power_channels=saved_power_channels
        or ((0,) if before_powers or after_powers else ()),
        acquisition_timing=(
            AcquisitionTiming(
                basis=_first(metadata, "acquisition_timing_basis"),
                exposure_duration_s=1.0e-3
                * _float_value(metadata, "acquisition_exposure_duration_ms"),
                readout_duration_s=1.0e-3
                * _float_value(metadata, "acquisition_readout_duration_ms"),
                keep_clean_duration_s=1.0e-3
                * _float_value(metadata, "acquisition_keep_clean_duration_ms"),
                kinetic_cycle_s=1.0e-3
                * _float_value(metadata, "acquisition_kinetic_cycle_ms"),
                acquisition_mode=_first(
                    metadata,
                    "acquisition_mode",
                    "single_scan",
                ),
                driver_frame_index=_int_value(
                    metadata,
                    "acquisition_driver_frame_index",
                    -1,
                ),
                discarded_frames_before=_int_value(
                    metadata,
                    "acquisition_discarded_frames_before",
                    0,
                ),
                discarded_frames_for_target=_int_value(
                    metadata,
                    "acquisition_discarded_frames_for_target",
                    0,
                ),
                pending_frames_after_wait=_int_value(
                    metadata,
                    "acquisition_pending_frames_after_wait",
                    0,
                ),
            )
            if "acquisition_timing_basis" in metadata
            or "acquisition_exposure_duration_ms" in metadata
            else None
        ),
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
        instrument_state=_metadata_mapping(
            metadata,
            _COMPACT_GROUP_ALIASES["andor_state"],
        ),
        software_provenance={
            key: str(value)
            for key, value in _metadata_mapping(
                metadata,
                _COMPACT_GROUP_ALIASES["software_provenance"],
            ).items()
        },
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
        background_subtracted=background_present,
        background_timestamp_utc=_first(metadata, "background_timestamp_utc"),
        background_integration_ms=_float_value(
            metadata,
            "background_integration_ms",
            integration_ms if background_present else 0.0,
        ),
        background_signature=_first(metadata, "background_signature_sha256"),
    )


def load_spectrum_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility loader returning only wavelength and intensity arrays."""

    _metadata, wavelengths, intensities, _raw, _unsmoothed = _read_spectrum_file(Path(path))
    return wavelengths, intensities
