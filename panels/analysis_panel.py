"""Interactive post-acquisition analysis; no instrument or laser commands."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QScrollArea,
    QListWidget, QAbstractItemView, QComboBox, QCheckBox, QLineEdit,
    QDoubleSpinBox, QPushButton, QLabel, QFileDialog, QMessageBox, QPlainTextEdit,
    QGroupBox,
)
from core.settings import PlotStyleSettings
from io_utils.analysis_io import load_trace, save_trace, load_response, save_response, write_json
from processing.spectral_analysis import (
    SpectralTrace, trace_from_record, subtract_baseline, subtract_reference, as_count_rate,
    apply_response, derive_response, stitch, residual, analyze_band,
)
from panels.plot_options import PaddedViewBox, apply_plot_options, color_with_alpha


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation=operation
        self.signals=_TaskSignals()

    def run(self):
        try:self.signals.succeeded.emit(self.operation())
        except Exception as exc:self.signals.failed.emit(f'{type(exc).__name__}: {exc}')


class AnalysisPanel(QWidget):
    def __init__(self, current_record, parent=None):
        super().__init__(parent)
        self._current_record=current_record
        self.traces=[]
        self.profiles=[]
        self._report=None
        self._style=PlotStyleSettings()
        self._task=None
        root=QVBoxLayout(self)
        note=QLabel('Analysis creates separate spectra. Select list entries to overlay, process or stitch. '
                    'Acquire samples and standard-lamp spectra with the normal acquisition controls or left-side Scans tab.')
        note.setWordWrap(True);root.addWidget(note)
        splitter=QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)
        self.controls=QWidget();controls=QVBoxLayout(self.controls)
        row=QHBoxLayout()
        self._button(row,'Add current',self.add_current)
        self._button(row,'Load spectra / baseline…',self.load_files)
        controls.addLayout(row)
        self.trace_list=QListWidget()
        self.trace_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.trace_list.setMinimumHeight(130)
        self.trace_list.itemSelectionChanged.connect(self._plot)
        controls.addWidget(self.trace_list)
        row=QHBoxLayout()
        self._button(row,'Remove selected',self.remove_selected)
        self._button(row,'Export selected…',self.export_selected)
        controls.addLayout(row)
        form=QFormLayout()
        self.reference=QComboBox()
        self.reference.currentIndexChanged.connect(self._plot)
        form.addRow('Reference spectrum',self.reference)
        self.show_residuals=QCheckBox('Show differences from reference')
        self.show_residuals.toggled.connect(self._plot)
        form.addRow(self.show_residuals)
        self.normalize_display=QCheckBox('Normalize overlays to maximum (display only)')
        self.normalize_display.toggled.connect(self._plot)
        form.addRow(self.normalize_display)
        controls.addLayout(form)
        baseline=QGroupBox('Baseline and response')
        form=QFormLayout(baseline)
        self.baseline=QComboBox()
        for label,key in [('None','none'),('Constant offset','constant'),('Linear fit','linear'),('Quadratic fit','quadratic'),('Subtract reference','reference')]:
            self.baseline.addItem(label,key)
        form.addRow('Baseline',self.baseline)
        self.offset=self._spin(-1e12,1e12,0)
        form.addRow('Offset (input units)',self.offset)
        self.intervals=QLineEdit('')
        self.intervals.setPlaceholderText('e.g. 400:450, 750:800')
        form.addRow('Baseline-only ranges (nm)',self.intervals)
        self.already_baseline=QCheckBox('Input already baseline corrected')
        self.already_baseline.setToolTip('Explicit provenance assertion for imported data. Captured background subtraction is recognized automatically.')
        form.addRow(self.already_baseline)
        self.to_rate=QCheckBox('Convert counts to counts/s')
        form.addRow(self.to_rate)
        self.grating=QLineEdit('1');self.detector=QLineEdit('1')
        self.detector.setToolTip('Laboratory detector identifier, not an automatically inferred camera serial.')
        form.addRow('Grating identifier',self.grating);form.addRow('Detector identifier',self.detector)
        self.profile_combo=QComboBox();self.profile_combo.addItem('No response correction',None)
        form.addRow('Response profile',self.profile_combo)
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        row=QHBoxLayout()
        self._button(row,'Load coefficients…',self.load_profile)
        self._button(row,'Save profile…',self.export_profile)
        form.addRow(row)
        self.profile_info=QLabel('No profile selected');self.profile_info.setWordWrap(True)
        form.addRow(self.profile_info)
        self.crop=QCheckBox('Mask wavelengths outside response coverage')
        form.addRow(self.crop)
        self._button(form,'Process selected',self.process_selected)
        controls.addWidget(baseline)
        calibration=QGroupBox('Acquire or derive a lamp response')
        form=QFormLayout(calibration)
        lamp_note=QLabel('Acquire a standard lamp at each center; add or load those spectra, baseline-correct them, '
                        'then select only the corrected lamp traces. Exposure is normalized before coefficients '
                        'are combined. Formula uses wavelength in nm; profiles are relative, with one global normalization.')
        lamp_note.setWordWrap(True);form.addRow(lamp_note)
        self.step=self._spin(0.001,100,0.5)
        form.addRow('Coefficient spacing (nm)',self.step)
        self.profile_name=QLineEdit('Standard lamp response')
        form.addRow('Profile name',self.profile_name)
        self._button(form,'Derive from selected lamp spectra',self.derive_profile)
        controls.addWidget(calibration)
        stitching=QGroupBox('Stitch selected spectra')
        form=QFormLayout(stitching)
        self.calibrated=QCheckBox('Calibrated (same response profile required)')
        self.match_overlap=QCheckBox('Match uncalibrated overlap scale')
        self.calibrated.toggled.connect(self._stitch_mode)
        form.addRow(self.calibrated);form.addRow(self.match_overlap)
        self._button(form,'Stitch selected',self.stitch_selected)
        controls.addWidget(stitching)
        analysis=QGroupBox('Peak and band analysis')
        form=QFormLayout(analysis)
        self.band_lo=self._spin(0,100000,400);self.band_hi=self._spin(0,100000,800)
        self.prominence=self._spin(0,1e12,0)
        form.addRow('Band start (nm)',self.band_lo);form.addRow('Band stop (nm)',self.band_hi)
        form.addRow('Minimum peak prominence',self.prominence)
        self._button(form,'Analyze selected',self.analyze_selected)
        self._button(form,'Export analysis report…',self.export_report)
        controls.addWidget(analysis)
        controls.addStretch()
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.controls)
        scroll.setMinimumWidth(300);splitter.addWidget(scroll)
        right=QWidget();right_layout=QVBoxLayout(right)
        self.plot=pg.PlotWidget(viewBox=PaddedViewBox());self.plot.addLegend()
        self.plot.setLabel('bottom','Wavelength (nm)')
        self.difference_plot=pg.PlotWidget(viewBox=PaddedViewBox())
        self.difference_plot.setLabel('bottom','Wavelength (nm)');self.difference_plot.setLabel('left','Difference')
        self.difference_plot.setXLink(self.plot)
        self.difference_plot.hide()
        right_layout.addWidget(self.plot,4);right_layout.addWidget(self.difference_plot,2)
        self.report=QPlainTextEdit();self.report.setReadOnly(True);self.report.setMaximumHeight(170)
        right_layout.addWidget(self.report,1)
        splitter.addWidget(right);splitter.setStretchFactor(0,0);splitter.setStretchFactor(1,1)
        splitter.setSizes([360,800])
        self.status=QLabel('Ready');self.status.setWordWrap(True);root.addWidget(self.status)
        bundled=Path(__file__).resolve().parents[1]/'processing'/'data'/'grating1_detector1_coefficients.csv'
        if bundled.exists():
            self._add_profile(load_response(bundled,grating='1',detector='1'),select=False)
        self.apply_style(self._style)

    @staticmethod
    def _button(layout,text,callback):
        button=QPushButton(text)
        button.clicked.connect(lambda _checked=False:callback())
        if isinstance(layout,QFormLayout):layout.addRow(button)
        else:layout.addWidget(button)
        return button

    @staticmethod
    def _spin(lo,hi,value):
        spin=QDoubleSpinBox();spin.setRange(lo,hi);spin.setDecimals(4);spin.setValue(value)
        return spin

    def selected_traces(self):
        return [self.traces[self.trace_list.row(item)] for item in self.trace_list.selectedItems()]

    def _require_selected(self):
        traces=self.selected_traces()
        if not traces:raise ValueError('Select at least one spectrum in the list')
        return traces

    def _reference_trace(self):
        index=self.reference.currentData()
        return self.traces[index] if index is not None else None

    def _start(self,kind,operation):
        if self._task is not None:return
        self.controls.setEnabled(False);self.status.setText('Working…')
        self._task=_Task(lambda:(kind,operation()))
        self._task.signals.succeeded.connect(self._completed,Qt.ConnectionType.QueuedConnection)
        self._task.signals.failed.connect(self._failed,Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self._task)

    @Slot(object)
    def _completed(self,result):
        self._task=None;self.controls.setEnabled(True)
        kind,value=result
        try:
            if kind=='traces':self._add_traces(value)
            elif kind=='profile':self._add_profile(value)
            elif kind=='report':
                self._report=value
                self.report.setPlainText(json.dumps(value,indent=2,default=str))
            self.status.setText('Complete' if kind!='export' else 'Saved')
        except Exception as exc:self._failed(str(exc))

    @Slot(str)
    def _failed(self,message):
        self._task=None;self.controls.setEnabled(True)
        self.status.setText(message)
        QMessageBox.warning(self,'Analysis',message)

    def _guard(self,operation):
        try:operation()
        except Exception as exc:self._failed(str(exc))

    def _add_traces(self,traces):
        # Bound retained analysis memory without affecting acquisition storage.
        if len(self.traces)+len(traces)>100:raise ValueError('Analysis retains at most 100 spectra; remove entries first')
        if sum(len(t.x) for t in self.traces)+sum(len(t.x) for t in traces)>2000000:
            raise ValueError('Analysis retains at most 2 million samples')
        self.trace_list.blockSignals(True);self.reference.blockSignals(True)
        self.trace_list.clearSelection()
        for trace in traces:
            index=len(self.traces);self.traces.append(trace)
            self.trace_list.addItem(f'{index+1}. {trace.label} [{trace.units}]')
            self.trace_list.item(index).setSelected(True)
            self.reference.addItem(f'{index+1}. {trace.label}',index)
        self.trace_list.blockSignals(False);self.reference.blockSignals(False)
        self._plot()

    def add_current(self):
        def op():
            record=self._current_record()
            if record is None:raise ValueError('Acquire or load a spectrum first')
            self._add_traces([trace_from_record(record,'Acquired '+record.timestamp_utc)])
        self._guard(op)

    def load_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Load spectra or saved baselines','','CSV (*.csv)')
        if paths:self._start('traces',lambda:[load_trace(path) for path in paths])

    def remove_selected(self):
        remove={self.trace_list.row(item) for item in self.trace_list.selectedItems()}
        keep=[t for i,t in enumerate(self.traces) if i not in remove]
        self.trace_list.blockSignals(True);self.reference.blockSignals(True)
        self.trace_list.clear();self.reference.clear();self.traces=[]
        self.trace_list.blockSignals(False);self.reference.blockSignals(False)
        self._add_traces(keep)

    def _add_profile(self,profile,select=True):
        for i,p in enumerate(self.profiles):
            if p.fingerprint==profile.fingerprint:
                if select:self.profile_combo.setCurrentIndex(i+1)
                return
        self.profiles.append(profile)
        self.profile_combo.addItem(f'{profile.label} (G{profile.grating}, D{profile.detector})',len(self.profiles)-1)
        if select:self.profile_combo.setCurrentIndex(self.profile_combo.count()-1)

    def _profile_selected(self):
        i=self.profile_combo.currentData()
        if i is None:self.profile_info.setText('No profile selected');return
        p=self.profiles[i]
        self.grating.setText(p.grating);self.detector.setText(p.detector)
        self.profile_info.setText(f'{p.x[0]:g}–{p.x[-1]:g} nm; {len(p.x)} coefficients. '
            'Select the correct laboratory detector ID; imported IDs are not automatically mapped to a camera serial.')

    def load_profile(self):
        path,_=QFileDialog.getOpenFileName(self,'Load response coefficients','','CSV (*.csv)')
        grating=self.grating.text().strip();detector=self.detector.text().strip()
        if path:self._start('profile',lambda:load_response(path,grating=grating,detector=detector))

    def export_profile(self):
        i=self.profile_combo.currentData()
        if i is None:self.status.setText('Select a response profile first');return
        profile=self.profiles[i]
        path,_=QFileDialog.getSaveFileName(self,'Save response profile','response.csv','CSV (*.csv)')
        if path:self._start('export',lambda:save_response(Path(path),profile))

    def process_selected(self):
        def op():
            traces=self._require_selected();method=self.baseline.currentData()
            offset=self.offset.value();reference=self._reference_trace()
            intervals=[]
            if method in ('linear','quadratic'):
                for part in self.intervals.text().split(','):
                    values=part.strip().split(':')
                    if len(values)!=2:raise ValueError('Use baseline ranges like 400:450, 750:800')
                    intervals.append(tuple(float(v) for v in values))
            confirmed=self.already_baseline.isChecked();to_rate=self.to_rate.isChecked()
            profile_index=self.profile_combo.currentData()
            profile=self.profiles[profile_index] if profile_index is not None else None
            grating=self.grating.text().strip();detector=self.detector.text().strip();crop=self.crop.isChecked()
            def calculate():
                outputs=[]
                for original in traces:
                    trace=original
                    # Reference baselines must share units. Rate normalization is explicit.
                    if method=='reference' and to_rate:trace=as_count_rate(trace)
                    if method=='reference':
                        if reference is None or reference.trace_id==original.trace_id:raise ValueError('Choose a separate baseline reference')
                        trace,_=subtract_reference(trace,reference)
                    elif method!='none':trace,_=subtract_baseline(trace,method,offset=offset,intervals=intervals)
                    elif confirmed:
                        trace=trace.derive(trace.y,{'type':'baseline_assertion','source':'user'},metadata={'baseline_applied':True})
                    if to_rate:trace=as_count_rate(trace)
                    if profile:trace=apply_response(trace,profile,grating=grating,detector=detector,crop=crop)
                    if trace is original:raise ValueError('Choose a processing operation')
                    outputs.append(trace)
                return outputs
            self._start('traces',calculate)
        self._guard(op)

    def derive_profile(self):
        def op():
            traces=self._require_selected();grating=self.grating.text().strip();detector=self.detector.text().strip()
            step=self.step.value();label=self.profile_name.text().strip() or 'Lamp response'
            self._start('profile',lambda:derive_response(traces,grating=grating,detector=detector,step_nm=step,label=label))
        self._guard(op)

    def _stitch_mode(self,checked):
        if checked:self.match_overlap.setChecked(False)
        self.match_overlap.setEnabled(not checked)

    def stitch_selected(self):
        def op():
            traces=self._require_selected();calibrated=self.calibrated.isChecked();match=self.match_overlap.isChecked()
            self._start('traces',lambda:[stitch(traces,calibrated=calibrated,match_overlap=match)])
        self._guard(op)

    def analyze_selected(self):
        def op():
            traces=self._require_selected();lo=self.band_lo.value();hi=self.band_hi.value();prominence=self.prominence.value()
            self._start('report',lambda:[analyze_band(t,lo,hi,prominence=prominence) for t in traces])
        self._guard(op)

    def export_selected(self):
        def op():
            traces=self._require_selected()
            if len(traces)==1:
                path,_=QFileDialog.getSaveFileName(self,'Save derived spectrum','analysis.csv','CSV (*.csv)')
                if path:self._start('export',lambda:save_trace(Path(path),traces[0]))
            else:
                directory=QFileDialog.getExistingDirectory(self,'Export selected spectra')
                if directory:
                    paths=[Path(directory)/f'analysis_{t.trace_id}.csv' for t in traces]
                    if any(p.exists() for p in paths):raise ValueError('An export file already exists; select a different folder')
                    self._start('export',lambda:[save_trace(p,t) for p,t in zip(paths,traces,strict=True)])
        self._guard(op)

    def export_report(self):
        if self._report is None:self.status.setText('Analyze spectra first');return
        report=self._report
        path,_=QFileDialog.getSaveFileName(self,'Save peak and band report','analysis_report.json','JSON (*.json)')
        if path:self._start('export',lambda:write_json(Path(path),report))

    def apply_style(self,settings):
        self._style=replace(settings)
        for plot in (self.plot,self.difference_plot):
            apply_plot_options(plot,settings,'spectrum')
            font=self.font();font.setPointSize(settings.font_size)
            for name in ('bottom','left'):plot.getAxis(name).setTickFont(font)
        self._plot()

    def _plot(self):
        if not hasattr(self,'plot'):return
        self.plot.clear();self.difference_plot.clear()
        self.difference_plot.setVisible(self.show_residuals.isChecked())
        reference=self._reference_trace();selected=self.selected_traces()
        normalized=self.normalize_display.isChecked()
        units={t.units for t in selected}
        self.plot.setLabel('left','Normalized intensity' if normalized else next(iter(units)) if len(units)==1 else 'Intensity (mixed units)')
        colors=[self._style.spectrum_color,self._style.smoothed_color,self._style.background_color,
                '#009e73','#d55e00','#999999','#cc79a7','#56b4e9']
        messages=[]
        for i,trace in enumerate(selected):
            color=color_with_alpha(colors[i%len(colors)],self._style.spectrum_alpha)
            values=trace.y
            if normalized:
                maximum=float(np.nanmax(np.abs(values)))
                values=values/maximum if maximum>0 else values
            pen=pg.mkPen(color,width=self._style.spectrum_line_width) if self._style.spectrum_show_line else None
            self.plot.plot(trace.x,values,name=trace.label,pen=pen,connect='finite',
                symbol=self._style.symbol if self._style.spectrum_show_symbols else None,
                symbolSize=self._style.symbol_size,symbolPen=color,symbolBrush=color)
            if self.show_residuals.isChecked() and reference is not None and reference.trace_id!=trace.trace_id:
                try:
                    difference=residual(trace,reference)
                    self.difference_plot.plot(difference.x,difference.y,pen=pg.mkPen(color),connect='finite')
                except ValueError as exc:messages.append(str(exc))
        self.difference_plot.setLabel('left','Difference (original units)')
        if messages:self.status.setText('; '.join(sorted(set(messages))))
        for plot in (self.plot,self.difference_plot):
            apply_plot_options(plot,self._style,'spectrum')
        if self._style.spectrum_auto_range:
            self.plot.enableAutoRange();self.difference_plot.enableAutoRange(axis='y')
