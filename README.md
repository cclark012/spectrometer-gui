# Spectrometer GUI

PySide6-based acquisition GUI for magneto-photoluminescence and power-dependent spectroscopy.

## Current Hardware

Supported or partially supported:

- Ocean Optics / Ocean Insight QEPro spectrometer via `seabreeze`
- Newport 2936-R optical power meter via Newport `PowerMeterCommands.dll` and `pythonnet`
- Coherent OBIS Laser Box via virtual serial COM ports
- Emulated spectrometer, power meter, and laser boxes for development/testing

## Python Version

This project is currently developed for Python 3.14 on Windows.

## Install

From the repository root:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
seabreeze_os_setup
```

For optional OpenGL 3D plotting:
```bash
python -m pip install -e ".[opengl]"
```

For development tools:
```bash
python -m pip install -e ".[dev]"
```

## Newport 2936-R Setup
The Newport power meter uses the Newport Power Meter Application / USB driver stack and `PowerMeterCommands.dll`.

Common DLL path:
```bash
C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll
```
The GUI can still run if the Newport power meter is not connected; spectra will be acquired with empty power metadata.

## QEPro Setup
The QEPro is accessed through `seabreeze`.
After installing dependencies, run:
```bash
seabreeze_os_setup
```
The GUI can still run if the QEPro is not connected; power monitoring and laser controls can still be used.

## OBIS Laser Box Setup
The Coherent OBIS Laser Boxes are detected through virtual COM ports.
Typical lab ports:
```bash
COM3
COM5
```
The GUI supports real OBIS boxes, emulated boxes, or real-first fallback mode.

## Run Modes
Everything emulated
```bash
python gui.py --emulate --laser-mode emulated
```
Real devices, explicit OBIS ports:
```bash
python gui.py --real --laser-mode real --obis-ports COM3 COM5
```
Real devices with laser fallback:
```bash
python gui.py --real --laser-mode auto --obis-ports COM3 COM5
```
Emulated spectrometer/power meter with real lasers:
```bash
python gui.py --emulate --laser-mode real --obis-ports COM3 COM5
```

## Lab Startup
For lab users, run:
```bash
start-gui.bat
```
The batch file reads config/lab_defaults.json if present.

## Data Output
Saved spectrum CSV files include:

- acquisition settings
- power readings before/after acquisition
- laser metadata
- scan metadata
- filter state
- averaging/background metadata
- wavelength/intensity arrays

## Notes

- Newport power readings are validated using user-defined max-power thresholds and status-word checks.
- Scan planning is fail-fast for invalid points and warning-based for clipped setpoints.
- Filter-wheel planning assumes nominal ND filter transmission unless a calibration workflow is used.
