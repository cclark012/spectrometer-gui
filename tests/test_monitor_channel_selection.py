import numpy as np
from core.records import PowerSnapshot, SpectrumRecord
from processing.monitor_metrics import MonitorCaptureConfig, build_monitor_point, monitor_x, monitor_y


def test_channel_two_changes_every_power_metric_and_missing_is_not_fallback():
    record=SpectrumRecord('now',10.,np.array([400.,500.,600.]),np.array([1.,3.,1.]),
        PowerSnapshot([2.,4.],[0,0]),PowerSnapshot([2.,4.],[0,0]),100,1,1,False,False,0.,power_channels=(0,1))
    point=build_monitor_point(record,MonitorCaptureConfig(500.,400.,600.,0.))
    for mode,expected in [('Intensity at captured wavelength / power ch1',.75),
                          ('Integrated captured range / power ch1',100.),
                          ('Total integrated intensity / power ch1',100.),('Power ch1',4.)]:
        value,label,units=monitor_y(point,mode,1)
        assert value==expected and 'ch2' in label
    assert monitor_x(point,'Power ch1',1)==(4.,'Power ch2','W')
    record.power_channels=(0,)
    point=build_monitor_point(record,MonitorCaptureConfig(500.,400.,600.,0.))
    assert np.isnan(monitor_y(point,'Power ch1',1)[0])
