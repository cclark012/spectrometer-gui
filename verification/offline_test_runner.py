"""Minimal runner for this repository's pytest-style pure tests; not pytest itself."""
import sys,types,math,re,inspect,importlib.util,tempfile,traceback
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
class Raises:
 def __init__(self,kind,match=None):self.kind,self.match=kind,match
 def __enter__(self):return self
 def __exit__(self,kind,value,tb):
  if kind is None:raise AssertionError('Expected exception '+str(self.kind))
  if not issubclass(kind,self.kind):return False
  if self.match and not re.search(self.match,str(value)):raise AssertionError(f'{value!s} does not match {self.match}')
  self.value=value;return True
class Approx:
 def __init__(self,value):self.value=value
 def __eq__(self,other):return math.isclose(other,self.value,rel_tol=1e-6,abs_tol=1e-12)
def parametrize(names,values):
 def wrap(fn):fn.params=(names.split(','),values);return fn
 return wrap
pytest=types.ModuleType('pytest');pytest.raises=Raises;pytest.approx=Approx;pytest.mark=types.SimpleNamespace(parametrize=parametrize);sys.modules['pytest']=pytest
passed=failed=0
for path in sorted((root/'tests').glob('test_*.py')):
 if path.stem in {'test_gui_changes','test_parameter_coordinator','test_device_processing_changes'}:continue
 spec=importlib.util.spec_from_file_location(path.stem,path);module=importlib.util.module_from_spec(spec);sys.modules[path.stem]=module
 try:spec.loader.exec_module(module)
 except Exception:
  failed+=1;print('IMPORT FAIL',path.name);traceback.print_exc();continue
 for name,fn in list(vars(module).items()):
  if not name.startswith('test_') or not callable(fn):continue
  names,values=getattr(fn,'params',([], [()]))
  for value in values:
   args=dict(zip(names,[value] if len(names)==1 else value))
   with tempfile.TemporaryDirectory() as tmp:
    if 'tmp_path' in inspect.signature(fn).parameters:args['tmp_path']=Path(tmp)
    try:fn(**args);passed+=1
    except Exception:
     failed+=1;print('FAIL',path.name,name,args);traceback.print_exc()
print(f'OFFLINE PURE TESTS: {passed} passed, {failed} failed')
raise SystemExit(bool(failed))
