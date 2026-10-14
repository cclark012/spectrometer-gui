"""Execute the real panel state methods without Qt; full widgets have separate Qt tests."""
import ast
from pathlib import Path
from types import SimpleNamespace


class Control:
    def __init__(self,value=False):self.data=value;self.enabled=True
    def setEnabled(self,value):self.enabled=value
    def setToolTip(self,value):self.tooltip=value
    def currentData(self):return self.data
    def isChecked(self):return bool(self.data)
    def value(self):return int(self.data)
    def set_kernel(self,*args):self.kernel=args


def make_panel():
    path=Path(__file__).resolve().parents[1]/'panels/acquisition_panel.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AcquisitionPanel')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in {'_apply_control_state','_update_smoothing_controls'}]
    scope={};exec(compile(ast.Module(body=methods,type_ignores=[]),str(path),'exec'),scope)
    panel=SimpleNamespace(_sequence_owner=None,_acquiring=False,_background_available=False,
                          _dark_correction_supported=True,_nonlinearity_correction_supported=True)
    for node in ast.walk(ast.Module(body=methods,type_ignores=[])):
        if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id=='self' and not node.attr.startswith('_'):
            setattr(panel,node.attr,Control())
    panel.smoothing_window_combo.data='boxcar';panel.boxcar_width.data=5
    panel._apply_control_state=lambda:scope['_apply_control_state'](panel)
    panel._update_smoothing_controls=lambda:scope['_update_smoothing_controls'](panel)
    return panel


def test_smoothing_state_has_no_unbound_names_and_respects_sequence():
    panel=make_panel();panel._update_smoothing_controls()
    assert panel.boxcar_width.enabled and not panel.subtract_background_check.enabled
    assert panel.smoothing_preview.kernel==('boxcar',5)
    panel._background_available=True;panel._update_smoothing_controls()
    assert panel.subtract_background_check.enabled
    panel._sequence_owner='parameter_scan';panel._update_smoothing_controls()
    assert not panel.boxcar_width.enabled and not panel.subtract_background_check.enabled
    panel._sequence_owner='live';panel._acquiring=True;panel._update_smoothing_controls()
    assert not panel.boxcar_width.enabled
    assert panel.live_check.enabled
