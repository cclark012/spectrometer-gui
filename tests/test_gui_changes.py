"""Target-environment checks: QT_QPA_PLATFORM=offscreen python -m pytest."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtWidgets import QApplication
from core.records import PowerTracePoint, PowerSnapshot, SpectrumRecord, SpectrometerCapabilities
from panels.power_panel import PowerPanel
from panels.monitor_panel import MonitorPanel
from panels.spectrum_panel import SpectrumPanel
from dialogs.spectrometer_details_dialog import SpectrometerDetailsDialog

_APP = None

def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_power_channel_statistics_and_timer_rebase():
    app(); panel=PowerPanel(); panel.set_application_t0(0.)
    panel.append_point(PowerTracePoint('now',10.,'poll',[2.,4.],[0,0],0))
    panel.channel_combo.setCurrentIndex(1)
    assert list(panel.power_curve.getData()[1])==[4.]
    assert '4 W' in panel.power_stats_label.text()
    panel.reset_timer()
    assert panel.points()[0].elapsed_s < 0
    assert panel.points()[0].timestamp_utc=='now'
    panel.close()


def test_smoothing_overlay_and_clear():
    app(); panel=SpectrumPanel()
    record=SpectrumRecord('now',0.,np.array([1.,2.,3.]),np.array([2.,2.,2.]),
        PowerSnapshot.missing(),PowerSnapshot.missing(),10,1,3,False,False,None,
        smoothing_window='boxcar',background_subtracted=True,
        raw_intensities_counts=np.array([11.,13.,11.]),unsmoothed_intensities_counts=np.array([1.,3.,1.]))
    panel.queue_record(record);panel._redraw_if_dirty()
    np.testing.assert_array_equal(panel.unsmoothed_curve.getData()[1],[1.,3.,1.])
    assert panel.unsmoothed_curve.isVisible()
    panel.clear();assert not panel.unsmoothed_curve.isVisible()
    panel.close()


def test_monitor_channel_changes_map_and_export(tmp_path):
    app(); panel=MonitorPanel();panel.set_application_t0(0.)
    record=SpectrumRecord('now',1.,np.array([400.,500.,600.]),np.array([1.,3.,1.]),
        PowerSnapshot([2.,4.],[0,0]),PowerSnapshot([2.,4.],[0,0]),10,1,1,False,False,0.,power_channels=(0,1))
    panel.add_record(record)
    panel.track_quantity.setCurrentIndex(panel.track_quantity.findData('Power ch1'))
    panel.channel_combo.setCurrentIndex(1)
    assert list(panel.monitor_curve.getData()[1])==[4.]
    panel.monitor_map_view.set_data(panel.points(),'Power ch1',1)
    assert panel.monitor_map_view.scatter.points()[0].pos().y()==4.
    path=tmp_path/'monitor.csv';panel.save_csv(path)
    assert '# power_channel,2' in path.read_text()
    panel.reset_timer(); assert panel.points()[0].elapsed_s < 0
    panel.close()


def test_timing_labels_refresh_after_apply():
    app()
    # Exercise the update on a lightweight real dialog; control schema hardware list
    # construction is covered by the target emulator/full-window smoke check.
    from PySide6.QtWidgets import QLabel, QTextEdit
    dialog=SpectrometerDetailsDialog(SpectrometerCapabilities())
    dialog._timing_labels={'minimum_frame_period_ms':QLabel('100 ms')}
    dialog._configuration_pending=True
    caps=SpectrometerCapabilities(control_schema={'camera':{'minimum_frame_period_ms':15.}})
    dialog.configuration_applied(caps)
    assert dialog._timing_labels['minimum_frame_period_ms'].text()=='15 ms'
    dialog.close()


def test_acquisition_constructor_and_smoothing_sequence_state():
    from panels.acquisition_panel import AcquisitionPanel
    app();panel=AcquisitionPanel()
    panel.smoothing_window_combo.setCurrentIndex(panel.smoothing_window_combo.findData('boxcar'))
    assert panel.boxcar_width.isEnabled()
    panel.set_sequence_owner('parameter_scan')
    panel._update_smoothing_controls()
    assert not panel.boxcar_width.isEnabled()
    panel.close()


def test_spectrum_layer_order_and_custom_opacity():
    from core.settings import PlotStyleSettings
    app();panel=SpectrumPanel()
    settings=PlotStyleSettings(smoothed_color='#123456',smoothed_alpha=0.8,unsmoothed_alpha=0.2)
    panel._smoothed_visible=True;panel.apply_style(settings)
    assert panel.curve.zValue()>panel.unsmoothed_curve.zValue()>panel.background_curve.zValue()
    assert panel.curve.opts['pen'].color().name()=='#123456'
    assert abs(panel.unsmoothed_curve.opts['pen'].color().alphaF()-0.2)<0.01
    panel.close()


def test_analysis_panel_supplied_profile_and_overlay():
    from panels.analysis_panel import AnalysisPanel
    from processing.spectral_analysis import SpectralTrace
    app();panel=AnalysisPanel(lambda:None)
    assert len(panel.profiles)==1
    panel._add_traces([SpectralTrace(np.arange(4.),np.arange(4.)),SpectralTrace(np.arange(4.),np.arange(4.)+1)])
    panel.show_residuals.setChecked(True)
    assert len(panel.plot.listDataItems())==2
    assert len(panel.difference_plot.listDataItems())==1
    panel.close()
