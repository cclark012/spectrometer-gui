"""Real Qt/emulator smoke test. Run from repository root on the target PC.

python verification/gui_smoke.py --output verification/gui_smoke_output
Uses isolated QSettings and emulated devices only; never commands real hardware.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='verification/gui_smoke_output')
    args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    report={'status':'not_run','checks':[],'python':sys.version,'exceptions':[]}
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication,QMessageBox
        from core.settings import DeviceConfig
        from ui.theme import ThemeManager
        from panels.main_window import MainWindow
    except ImportError as exc:
        report.update(status='blocked',reason=str(exc))
        (output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2));return 2
    app=QApplication.instance() or QApplication([])
    app.setOrganizationName('SpectrometerGuiIsolatedSmoke');app.setApplicationName('20261007')
    def hook(kind,value,tb):report['exceptions'].append(''.join(traceback.format_exception(kind,value,tb)))
    original_hook=sys.excepthook;sys.excepthook=hook
    original_warning=QMessageBox.warning
    QMessageBox.warning=lambda *args,**kwargs: (report['exceptions'].append(str(args[2])),QMessageBox.StandardButton.Ok)[1]
    window=None
    def wait_for(predicate,timeout=15):
        stop=time.monotonic()+timeout
        while time.monotonic()<stop:
            app.processEvents()
            if report['exceptions']:raise AssertionError(report['exceptions'][-1])
            if predicate():return
            time.sleep(0.01)
        raise AssertionError('Timed out waiting for Qt/emulator operation')
    try:
        with tempfile.TemporaryDirectory() as temp:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,temp)
            config=DeviceConfig(emulate=True,fallback_emulator=False,newport_dll=None,power_channel=0,
                laser_mode='emulated',power_meter_mode='emulated',spectrometer_mode='emulated')
            window=MainWindow(config,theme_manager=ThemeManager(app,custom_directory=Path(temp)/'themes'))
            window.file_name_settings.autosave_spectra=False
            window.file_name_settings.save_directory=Path(temp)/'data'
            window.show()
            wait_for(lambda:window._instrument_connected('spectrometer') and window.laser_panel.table.rowCount()==8)
            report['checks'].append('Full MainWindow construction and emulator connection')
            assert window.lower_tabs.tabText(window.scan_tab_index)=='Scans'
            window.scan_kind.setCurrentIndex(window.scan_kind.findData('exposure'))
            assert window.scan_pages.currentIndex()==1 and window.parameter_panel.kind.currentData()=='exposure'
            report['checks'].append('Unified left scan selection')
            acq=window.acquisition_panel
            acq.integration_ms.setValue(5);acq.averages.setValue(1)
            acq.smoothing_window_combo.setCurrentIndex(acq.smoothing_window_combo.findData('boxcar'))
            acq.measure_power_check.setChecked(False)
            window.take_spectrum()
            wait_for(lambda:window.current_record is not None and not window.acquiring)
            window.spectrum_panel._redraw_if_dirty()
            assert window.spectrum_panel.unsmoothed_curve.isVisible()
            assert window.spectrum_panel.curve.zValue()>window.spectrum_panel.unsmoothed_curve.zValue()
            report['checks'].append('Acquisition and smoothing overlay layer order')
            window.analysis_panel.add_current()
            panel=window.analysis_panel
            panel.baseline.setCurrentIndex(panel.baseline.findData('constant'))
            panel.offset.setValue(0)
            panel.process_selected()
            wait_for(lambda:panel._task is None)
            assert len(panel.traces)==2
            panel.analyze_selected()
            wait_for(lambda:panel._task is None)
            assert panel._report is not None
            report['checks'].append('Analysis background task, plotting and band report')
            window.tabs.setCurrentWidget(panel);app.processEvents()
            window.grab().save(str(output/'analysis.png'))
            window.tabs.setCurrentWidget(window.spectrum_panel)
            window.lower_tabs.setCurrentIndex(window.laser_tab_index);app.processEvents()
            window.grab().save(str(output/'spectrum.png'))
            report['laser_table_rows']=window.laser_panel.table.rowCount()
            report['laser_table_height']=window.laser_panel.table.height()
            window.close();app.processEvents();window=None
            report['checks'].append('Orderly emulator and worker shutdown')
            report['status']='passed'
    except Exception:
        report.update(status='failed',reason=traceback.format_exc())
    finally:
        if window is not None:window.close();app.processEvents()
        sys.excepthook=original_hook;QMessageBox.warning=original_warning
        (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
