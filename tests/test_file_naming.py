from pathlib import Path

import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from core.settings import FileNameSettings
from io_utils.file_naming import build_spectrum_path


def _record() -> SpectrumRecord:
    return SpectrumRecord(
        timestamp_utc="2026-08-07T12:34:56.123+00:00",
        timestamp_s=0.0,
        wavelengths_nm=np.array([500.0]),
        intensities_counts=np.array([1.0]),
        p_before=PowerSnapshot([1e-3], [0x118]),
        p_after=PowerSnapshot([1e-3], [0x118]),
        integration_ms=100,
        averages=1,
        boxcar_width=0,
        correct_dark=False,
        correct_nonlinearity=False,
        field_value=10.0,
    )


def test_build_spectrum_path_enumerates_existing_files(tmp_path: Path) -> None:
    settings = FileNameSettings(
        save_directory=tmp_path,
        base_name="sample",
        include_date=False,
        include_time=False,
        include_power=False,
        include_field=False,
        include_run_identifier=False,
        include_enumeration=True,
    )
    first = build_spectrum_path(settings, _record())
    first.touch()
    second = build_spectrum_path(settings, _record())
    assert first.name == "sample_0001.csv"
    assert second.name == "sample_0002.csv"
