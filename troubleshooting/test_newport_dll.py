from pathlib import Path
import os
import sys

from pythonnet import load

# Must be before import clr.
load("netfx")

import clr
from System.Reflection import Assembly


dll_path = Path(
    r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll"
)

dll_dir = str(dll_path.parent)

if dll_dir not in sys.path:
    sys.path.insert(0, dll_dir)

if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(dll_dir)

asm = Assembly.LoadFrom(str(dll_path))

print("Loaded:")
print(asm.FullName)

print("Types containing PowerMeter or Command:")
for t in asm.GetTypes():
    name = t.FullName
    if "PowerMeter" in name or "Command" in name:
        print(" ", name)
