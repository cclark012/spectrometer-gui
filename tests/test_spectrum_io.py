from __future__ import annotations

import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from io_utils.spectrum_io import (
    load_spectrum_csv,
    load_spectrum_record,
    save_spectrum_record,
)


def _record() -> SpectrumRecord:
    return SpectrumRecord(
        timestamp_utc="2026-01-01T00:00:00+00:00",
        timestamp_s=123.0,
        wavelengths_nm=np.asarray([500.0, 501.0]),
        intensities_counts=np.asarray([10.0, 20.0]),
        p_before=PowerSnapshot([1e-3], [0x118], 0),
        p_after=PowerSnapshot([1.1e-3], [0x118], 0),
        integration_ms=100,
        averages=2,
        boxcar_width=0,
        correct_dark=True,
        correct_nonlinearity=True,
        field_value=10.0,
        run_identifier="run-A",
        notes="line one\nline two",
        scan_active=True,
        scan_index=1,
        scan_count=3,
        laser_channel=2,
        laser_wavelength_nm=532.0,
        filter_state="W1:open",
    )


def test_spectrum_csv_round_trip(tmp_path):
    record = _record()
    path = tmp_path / "spectrum.csv"
    save_spectrum_record(path, record)

    wavelengths, intensities = load_spectrum_csv(path)
    loaded = load_spectrum_record(path)

    assert np.allclose(wavelengths, record.wavelengths_nm)
    assert np.allclose(intensities, record.intensities_counts)
    assert np.allclose(loaded.wavelengths_nm, record.wavelengths_nm)
    assert np.allclose(loaded.intensities_counts, record.intensities_counts)
    assert loaded.integration_ms == 100
    assert loaded.averages == 2
    assert loaded.run_identifier == "run-A"
    assert loaded.notes == "line one\nline two"
    assert loaded.scan_active is True
    assert loaded.laser_channel == 2
    assert loaded.filter_state == "W1:open"
    assert loaded.p_before.pm_status == [0x118]

    text = path.read_text(encoding="utf-8")
    assert "intensity_counts_per_s" in text
    assert "# p_before_command_status" in text


def test_legacy_spectrum_without_integration_has_nan_counts_per_s(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("wavelength_nm,intensity_counts\n500,10\n501,20\n", encoding="utf-8")

    record = load_spectrum_record(path)

    assert record.integration_ms == 0
    assert np.all(np.isnan(record.intensities_counts_per_s()))


def test_mean_power_snapshot_preserves_latest_range_and_combines_flags():
    record = _record()
    record.p_before = PowerSnapshot([1.0], [0x118 | 0x04], 0)
    record.p_after = PowerSnapshot([3.0], [0x128 | 0x02], 0)

    snapshot = record.mean_power_snapshot()

    assert snapshot.powers_w == [2.0]
    assert snapshot.pm_status == [(0x128 & ~0x0F) | 0x0E]
