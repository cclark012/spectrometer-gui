"""Run with real Qt on the target; offline harness supplies signals/timer only."""
from types import SimpleNamespace
from controllers.parameter_scan_coordinator import ParameterScanCoordinator
from core.settings import AcquisitionSettings
from planning.parameter_scan import ParameterScanPlan, TemperatureReading


def setup(plan):
    clock=[0.]
    c=ParameterScanCoordinator(clock=lambda:clock[0])
    events={'acquire':[], 'config':[], 'save':[], 'finished':[], 'off':[], 'summary':[]}
    c.acquire_requested.connect(lambda:events['acquire'].append(clock[0]))
    c.configure_requested.connect(events['config'].append)
    c.save_requested.connect(events['save'].append)
    c.finished.connect(events['finished'].append)
    c.summary_ready.connect(events['summary'].append)
    c.disable_lasers_requested.connect(lambda:events['off'].append(True))
    settings=AcquisitionSettings(50,1,1,False,False,None,measure_power=False)
    c.start(plan,settings,500. if plan.kind=='wavelength' else None)
    return c,clock,events


def test_scheduled_deadlines_overrun_no_overlap_and_exact_save_count():
    c,t,e=setup(ParameterScanPlan('time',(0.,10.,20.)))
    c.tick(); assert e['acquire']==[0.]
    t[0]=12.;c.tick();assert len(e['acquire'])==1
    r=SimpleNamespace();c.spectrum_ready(r)
    c.tick();assert e['acquire']==[0.,12.]
    c.spectrum_ready(SimpleNamespace())
    assert e['save'][1].parameter_scan['request_lateness_s']==2.
    t[0]=19.;c.tick();assert len(e['acquire'])==2
    t[0]=20.;c.tick();c.spectrum_ready(SimpleNamespace())
    assert len(e['save'])==3 and not c.active and len(e['finished'])==1
    assert e['summary'][0]['completed_acquisitions']==3
    assert e['summary'][0]['scan_id']==e['save'][0].parameter_scan['scan_id']
    c.timer.stop()


def test_abort_during_acquisition_drains_result_and_prevents_next_request():
    c,t,e=setup(ParameterScanPlan('exposure',(10.,20.)))
    c.tick();assert c.acquisition_settings().integration_ms==10
    c.abort();assert c.active and not e['finished']
    c.tick();assert len(e['acquire'])==1
    c.spectrum_ready(SimpleNamespace())
    assert not c.active and len(e['save'])==1 and e['off']
    assert 'termination_requested' in e['save'][0].parameter_scan


def test_abort_during_motion_waits_for_ack_then_restores_before_release():
    c,t,e=setup(ParameterScanPlan('wavelength',(550.,600.)))
    assert c.phase=='configure' and e['config'][0]['spectrograph']['center_wavelength_nm']==550.
    c.abort();assert c.active and len(e['config'])==1
    c.configuration_applied(None)
    assert c.phase=='restore' and e['config'][-1]['spectrograph']['center_wavelength_nm']==500.
    assert not e['acquire'] and not e['finished']
    c.configuration_applied(None)
    assert not c.active and len(e['finished'])==1


def test_motion_readback_and_settle_precede_acquisition():
    c,t,e=setup(ParameterScanPlan('wavelength',(550.,),settle_s=2.))
    t[0]=100.;c.tick();assert not e['acquire']
    c.configuration_applied(None)
    t[0]=101.;c.tick();assert not e['acquire']
    t[0]=102.;c.tick();assert len(e['acquire'])==1
    c.spectrum_ready(SimpleNamespace()); assert c.phase=='restore' and c.active
    c.configuration_applied(None);assert not c.active


def test_temperature_dwell_timeout_and_trigger_metadata():
    c,t,e=setup(ParameterScanPlan('temperature',(4.,),dwell_s=2.,max_age_s=1.5))
    for second in (0.,1.,2.):
        t[0]=second;c.accept_temperature(TemperatureReading('ccd',4.,'degC',second,'now'));c.tick()
    assert len(e['acquire'])==1
    c.spectrum_ready(SimpleNamespace())
    assert e['save'][0].parameter_scan['temperature_trigger']['source']=='ccd'
    c,t,e=setup(ParameterScanPlan('temperature',(4.,),timeout_s=2.))
    t[0]=2.;c.tick();assert not c.active and not e['acquire'] and 'timed out' in e['finished'][0]


def test_wrong_source_stale_and_prepoint_temperature_cannot_trigger():
    c,t,e=setup(ParameterScanPlan('temperature',(4.,),dwell_s=0.,temperature_source='lakeshore_A'))
    c.accept_temperature(TemperatureReading('lakeshore_B',4.,'K',0.,'now'));c.tick();assert not e['acquire']
    c.accept_temperature(TemperatureReading('lakeshore_A',4.,'K',-1.,'now'));c.tick();assert not e['acquire']
    t[0]=10.;c.accept_temperature(TemperatureReading('lakeshore_A',4.,'K',0.,'now'));c.tick();assert not e['acquire']
    c.abort()


def test_failures_release_lease_only_after_restore_attempt():
    c,t,e=setup(ParameterScanPlan('wavelength',(550.,)))
    c.configuration_failed('bad move');assert c.active and c.phase=='restore'
    c.configuration_failed('restore error');assert not c.active and 'Restore failed' in e['finished'][0]
    c,t,e=setup(ParameterScanPlan('exposure',(10.,)))
    c.tick();c.acquisition_failed('camera error');assert not c.active and not e['save']


def test_disconnect_finishes_without_new_native_command():
    c,t,e=setup(ParameterScanPlan('wavelength',(550.,)))
    c.disconnected('camera disconnected')
    assert not c.active and len(e['config'])==1
    c.configuration_applied(None);assert len(e['config'])==1
