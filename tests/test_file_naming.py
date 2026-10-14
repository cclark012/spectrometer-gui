from pathlib import Path

import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from core.settings import FileNameSettings
from io_utils.file_naming import build_gated_series_path, build_spectrum_path
from processing.gated_averaging import GatedSeriesRecord


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


def test_nonfinite_optional_tokens_are_omitted(tmp_path: Path) -> None:
    record = _record()
    record.field_value = float("nan")
    record.p_before = PowerSnapshot.missing()
    record.p_after = PowerSnapshot.missing()
    settings = FileNameSettings(
        save_directory=tmp_path,
        base_name="sample",
        include_date=False,
        include_time=False,
        include_power=True,
        include_field=True,
        include_run_identifier=False,
        include_enumeration=False,
    )

    assert build_spectrum_path(settings, record).name == "sample.csv"


def test_unrecorded_field_is_omitted_from_gated_series_name(tmp_path: Path) -> None:
    settings = FileNameSettings(
        save_directory=tmp_path,
        base_name="sample",
        include_date=False,
        include_time=False,
        include_field=True,
        include_run_identifier=False,
        include_enumeration=False,
    )
    series = GatedSeriesRecord(
        sequence_id="sequence",
        mode="delayed_after_off",
        timestamp_utc="2026-09-09T00:00:00+00:00",
        wavelengths_nm=np.asarray([500.0]),
        traces=(),
        integration_ms=10,
        detector_averages=1,
        field_value_mT=None,
        laser_port="",
        laser_box_id="",
        laser_channel=-1,
        laser_wavelength_nm=float("nan"),
    )

    assert build_gated_series_path(settings, series).name == (
        "sample_gated_delayed_after_off.csv"
    )
