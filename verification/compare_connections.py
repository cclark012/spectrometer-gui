"""Compare connection implementation against the supplied September 14 source ZIP."""
import ast
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def extract(source, class_name, member=None):
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
    node=cls if member is None else next(n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==member)
    return ast.dump(node,include_attributes=False)


def run(baseline,root):
    archive=zipfile.ZipFile(baseline)
    prefix=next(name[:-len('devices/andor_adapter.py')] for name in archive.namelist() if name.endswith('devices/andor_adapter.py'))
    files=['devices/andor_adapter.py','devices/andor_sdk2.py','devices/andor_spectrograph.py',
           'controllers/instrument_runtime.py','core/configuration.py','config/lab_defaults.json','gui.py']
    checks=[]
    for name in files:
        old=archive.read(prefix+name);new=(root/name).read_bytes()
        checks.append(dict(file=name,check='byte equality',passed=old==new,
                           baseline_sha256=hashlib.sha256(old).hexdigest(),current_sha256=hashlib.sha256(new).hexdigest()))
    for name,cls,methods in [
        ('core/settings.py','DeviceConfig',[None]),
        ('controllers/device_controller.py','DeviceController',['_connect_spectrometer','_connect_spectrometer_now',
            'connect_spectrometer','connect_spectrometer_selection','disconnect_spectrometer','_close_spectrometer']),
        ('panels/main_window.py','MainWindow',['_connect_instrument','_disconnect_instrument','_reconnect_all_instruments'])]:
        old=archive.read(prefix+name).decode();new=(root/name).read_text()
        for method in methods:
            a=extract(old,cls,method);b=extract(new,cls,method)
            checks.append(dict(file=name,member=cls+('.'+method if method else ''),check='AST equality',passed=a==b))
    return {'baseline':Path(baseline).name,'checks':checks,'passed':all(c['passed'] for c in checks),
            'scope':'Source equality, not hardware connectivity or SDK validation. No I2C implementation added.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('baseline');parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run(args.baseline,Path(__file__).resolve().parents[1])
    Path(args.output).write_text(json.dumps(result,indent=2))
    print(f"Connection baseline: {sum(c['passed'] for c in result['checks'])}/{len(result['checks'])} checks match")
    raise SystemExit(0 if result['passed'] else 1)
