import math
import pytest
from planning.parameter_scan import ParameterScanPlan, TemperatureGate, TemperatureReading


def reading(value, t, status=0):
    return TemperatureReading('ccd', value, 'degC', t, 'now', status)


def test_scan_bounds_and_order():
    assert ParameterScanPlan('exposure',(10.,20.),repeats=2).points == (10.,10.,20.,20.)
    for kind,values in [('time',(10.,5.)),('time',(-1.,)),('exposure',(0.,)),('exposure',(1.5,)),('wavelength',(math.nan,))]:
        with pytest.raises(ValueError): ParameterScanPlan(kind,values)
    with pytest.raises(ValueError): ParameterScanPlan('time',(0.,1.),repeats=2)
    with pytest.raises(ValueError): ParameterScanPlan('exposure',(1.,)*10001)
    with pytest.raises(ValueError): ParameterScanPlan('temperature',(-2.,),temperature_source='lakeshore_A')


def test_temperature_gate_dwell_needs_distinct_fresh_valid_samples():
    gate=TemperatureGate()
    def check(r,now):
        return gate.update(r,target=4.,now=now,tolerance=.25,dwell=2.,max_age=1.5)
    assert not check(reading(4.,0.),0.)
    assert not check(reading(4.,0.),1.)  # Re-reading one cached sample does not satisfy dwell.
    assert not check(reading(4.,1.),1.)
    assert check(reading(4.,2.),2.)
    assert not check(reading(4.,2.),4.)  # stale
    assert not check(reading(4.,5.,status=32),5.)
    assert not check(reading(5.,6.),6.)  # outside tolerance
    assert not check(reading(4.,7.),7.)
    assert not check(reading(4.,10.),10.)  # sampling gap resets dwell
    assert not check(reading(math.nan,11.),11.)


def test_temperature_reading_rejects_future_timestamp():
    assert not reading(4.,10.).fresh(9.,2.)


def test_linear_and_log_point_generation():
    from planning.parameter_scan import generate_scan_values
    assert generate_scan_values(0., 20., 3, 'linear') == (0., 10., 20.)
    assert generate_scan_values(1., 100., 3, 'log') == (1., 10., 100.)
    with pytest.raises(ValueError): generate_scan_values(0., 1., 3, 'log')
