from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from core.records import SpectrumRecord
from core.settings import FileNameSettings
from core.units import field_token, power_token, sanitize_component


def parse_timestamp(timestamp_utc: str) -> datetime:
    try:
        return datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(UTC)


def next_available_path(
    directory: Path,
    stem: str,
    extension: str,
    width: int = 4,
) -> Path:
    suffix = extension if extension.startswith(".") else f".{extension}"
    for index in range(1, 1_000_000):
        path = directory / f"{stem}_{index:0{width}d}{suffix}"
        if not path.exists():
            return path
    raise RuntimeError(f"Could not find available file name for {directory / stem}")


def _extension(settings: FileNameSettings) -> str:
    return (
        settings.extension
        if settings.extension.startswith(".")
        else f".{settings.extension}"
    )


def _finalize_path(
    *,
    directory: Path,
    stem: str,
    extension: str,
    enumerate_names: bool,
    protect_existing: bool,
) -> Path:
    if enumerate_names:
        return next_available_path(directory, stem, extension)

    path = directory / f"{stem}{extension}"
    if protect_existing and path.exists():
        return next_available_path(directory, stem, extension)
    return path


def build_spectrum_path(
    settings: FileNameSettings,
    record: SpectrumRecord,
    *,
    protect_existing: bool = True,
) -> Path:
    directory = Path(settings.save_directory)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = parse_timestamp(record.timestamp_utc)
    parts = [sanitize_component(settings.base_name)]

    if settings.include_run_identifier and settings.run_identifier.strip():
        parts.append(sanitize_component(settings.run_identifier))
    if settings.include_date:
        parts.append(timestamp.strftime("%Y%m%d"))
    if settings.include_time:
        parts.append(
            timestamp.strftime("%H%M%S") + f"{timestamp.microsecond // 1000:03d}"
        )
    if settings.include_field and math.isfinite(float(record.field_value)):
        parts.append(field_token(float(record.field_value)))
    mean_power_w = float(record.mean_power_w(0))
    if settings.include_power and math.isfinite(mean_power_w):
        parts.append(power_token(mean_power_w))

    return _finalize_path(
        directory=directory,
        stem=sanitize_component("_".join(parts)),
        extension=_extension(settings),
        enumerate_names=settings.include_enumeration,
        protect_existing=protect_existing,
    )


def build_power_trace_path(
    settings: FileNameSettings,
    *,
    protect_existing: bool = True,
) -> Path:
    directory = Path(settings.save_directory)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC)
    parts = [sanitize_component(settings.base_name), "power_trace"]
    if settings.include_date:
        parts.append(timestamp.strftime("%Y%m%d"))
    if settings.include_time:
        parts.append(
            timestamp.strftime("%H%M%S") + f"{timestamp.microsecond // 1000:03d}"
        )

    return _finalize_path(
        directory=directory,
        stem=sanitize_component("_".join(parts)),
        extension=_extension(settings),
        enumerate_names=settings.include_enumeration,
        protect_existing=protect_existing,
    )


def build_gated_series_path(
    settings: FileNameSettings,
    series,
    *,
    protect_existing: bool = True,
) -> Path:
    directory = Path(settings.save_directory)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = parse_timestamp(str(series.timestamp_utc))
    parts = [sanitize_component(settings.base_name), "gated", sanitize_component(series.mode)]
    if settings.include_run_identifier and settings.run_identifier.strip():
        parts.append(sanitize_component(settings.run_identifier))
    if settings.include_date:
        parts.append(timestamp.strftime("%Y%m%d"))
    if settings.include_time:
        parts.append(timestamp.strftime("%H%M%S") + f"{timestamp.microsecond // 1000:03d}")
    if settings.include_field and math.isfinite(float(series.field_value_mT)):
        parts.append(field_token(float(series.field_value_mT)))

    return _finalize_path(
        directory=directory,
        stem=sanitize_component("_".join(parts)),
        extension=_extension(settings),
        enumerate_names=settings.include_enumeration,
        protect_existing=protect_existing,
    )
