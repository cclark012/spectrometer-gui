# file_naming.py

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.settings import FileNameSettings
from core.records import SpectrumRecord
from core.units import field_token, power_token, sanitize_component


def parse_timestamp(timestamp_utc: str) -> datetime:
    try:
        return datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def next_available_path(directory: Path, stem: str, extension: str, width: int = 4) -> Path:
    extension = extension if extension.startswith(".") else "." + extension

    for index in range(1, 1_000_000):
        path = directory / f"{stem}_{index:0{width}d}{extension}"

        if not path.exists():
            return path

    raise RuntimeError(f"Could not find available file name for {directory / stem}")


def build_spectrum_path(
        settings: FileNameSettings,
        record: SpectrumRecord,
        *,
        protect_existing: bool = True,
    ) -> Path:
    directory = Path(settings.save_directory)
    directory.mkdir(parents=True, exist_ok=True)

    extension = settings.extension if settings.extension.startswith(".") else "." + settings.extension

    dt = parse_timestamp(record.timestamp_utc)

    parts = [sanitize_component(settings.base_name)]
    
    if settings.include_run_identifier and settings.run_identifier.strip():
        parts.append(sanitize_component(settings.run_identifier))

    if settings.include_date:
        parts.append(dt.strftime("%Y%m%d"))

    if settings.include_time:
        # Includes milliseconds to reduce collisions.
        parts.append(dt.strftime("%H%M%S") + f"{dt.microsecond // 1000:03d}")

    if settings.include_field:
        parts.append(field_token(float(record.field_value)))

    if settings.include_power:
        parts.append(power_token(float(record.mean_power_w(0))))

    stem = sanitize_component("_".join(parts))

    if settings.include_enumeration:
        return next_available_path(directory, stem, extension)

    path = directory / f"{stem}{extension}"

    if protect_existing and path.exists():
        return next_available_path(directory, stem, extension)

    return path


def build_power_trace_path(
        settings: FileNameSettings,
        *,
        protect_existing: bool = True,
    ) -> Path:
    directory = Path(settings.save_directory)
    directory.mkdir(parents=True, exist_ok=True)

    extension = settings.extension if settings.extension.startswith(".") else "." + settings.extension

    now = datetime.now(timezone.utc)

    parts = [sanitize_component(settings.base_name), "power_trace"]

    if settings.include_date:
        parts.append(now.strftime("%Y%m%d"))

    if settings.include_time:
        parts.append(now.strftime("%H%M%S") + f"{now.microsecond // 1000:03d}")

    stem = sanitize_component("_".join(parts))

    if settings.include_enumeration:
        return next_available_path(directory, stem, extension)

    path = directory / f"{stem}{extension}"

    if protect_existing and path.exists():
        return next_available_path(directory, stem, extension)

    return path
