"""Deterministic state-machine checks; explicitly not a Qt event-loop test."""
import sys,types,importlib.util,traceback
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
class BoundSignal:
 def __init__(self):self.listeners=[]
 def connect(self,fn):self.listeners.append(fn)
 def emit(self,*args):
  for fn in list(self.listeners):fn(*args)
class Signal:
 def __init__(self,*args):pass
 def __set_name__(self,owner,name):self.name=name
 def __get__(self,obj,owner):
  if obj is None:return self
  return obj.__dict__.setdefault('_signal_'+self.name,BoundSignal())
class QObject:
 def __init__(self,*args):pass
class QTimer:
 def __init__(self,*args):self.timeout=BoundSignal();self.running=False
 def setInterval(self,*args):pass
 def start(self,*args):self.running=True
 def stop(self):self.running=False
qt=types.ModuleType('PySide6.QtCore');qt.QObject=QObject;qt.QTimer=QTimer;qt.Signal=Signal;qt.Slot=lambda *a,**k: (lambda fn:fn)
sys.modules['PySide6']=types.ModuleType('PySide6');sys.modules['PySide6.QtCore']=qt
path=root/'tests/test_parameter_coordinator.py';spec=importlib.util.spec_from_file_location('checks',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
passed=failed=0
for name,fn in list(vars(m).items()):
 if name.startswith('test_'):
  try:fn();passed+=1
  except Exception:failed+=1;print('FAIL',name);traceback.print_exc()
path=root/'tests/test_device_processing_changes.py'
spec=importlib.util.spec_from_file_location('worker_checks',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for name,fn in list(vars(m).items()):
 if name.startswith('test_'):
  try:fn();passed+=1
  except Exception:failed+=1;print('FAIL',name);traceback.print_exc()
print(f'COORDINATOR / WORKER SIGNAL-TIMER HARNESS: {passed} passed, {failed} failed')
raise SystemExit(bool(failed))
