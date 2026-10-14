import csv
import numpy as np
import pytest
from core.records import SpectrumRecord, PowerSnapshot, BackgroundSpectrum
from io_utils.spectrum_io import save_spectrum_record,load_spectrum_record
from io_utils.background_io import save_background


def test_three_processing_stages_and_parameter_metadata_round_trip(tmp_path):
    record=SpectrumRecord('now',10.,np.array([400.,500.,600.]),np.array([2.,2.,2.]),
        PowerSnapshot.missing(),PowerSnapshot.missing(),100,1,3,False,False,None,
        smoothing_window='boxcar',background_subtracted=True,
        raw_intensities_counts=np.array([11.,13.,11.]),unsmoothed_intensities_counts=np.array([1.,3.,1.]),
        parameter_scan={'kind':'time','target':10.,'request_lateness_s':.1})
    path=tmp_path/'record.csv'; save_spectrum_record(path,record); loaded=load_spectrum_record(path)
    np.testing.assert_array_equal(loaded.raw_intensities_counts,[11.,13.,11.])
    np.testing.assert_array_equal(loaded.unsmoothed_intensities_counts,[1.,3.,1.])
    np.testing.assert_array_equal(loaded.intensities_counts,[2.,2.,2.])
    assert loaded.parameter_scan==record.parameter_scan


def test_background_counts_rate_and_signature_are_explicit(tmp_path):
    bg=BackgroundSpectrum('now',np.array([400.,500.]),np.array([100.,200.]),50.,3,False,True,configuration_signature='exact')
    path=tmp_path/'background.csv';save_background(path,bg)
    rows=list(csv.reader(path.open()))
    assert ['# configuration_signature','exact'] in rows
    assert rows[-2] == ['400.0','5.0','100.0']
    with pytest.raises(ValueError): load_spectrum_record(path)
