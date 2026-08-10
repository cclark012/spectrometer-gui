# Spectrometer GUI

Windows/PySide6 acquisition software for magneto-photoluminescence and
power-dependent spectroscopy. The current hardware backends support:

- Ocean Optics / Ocean Insight QEPro through `python-seabreeze`
- Newport 2936-R through Newport `PowerMeterCommands.dll` and `pythonnet`
- Coherent OBIS Laser Boxes through USB virtual COM ports
- Emulated spectrometer, power meter, and laser boxes for development

The GUI is designed so the QEPro and Newport can connect independently. A
missing spectrometer does not disable the power monitor, and a missing power
meter does not prevent spectra from being acquired.

## Current capabilities

- Single and live spectrum acquisition
- Software averaging and optional backend/device averaging
- Newport power validation using status words and power bounds
- Manual and laser-linked Newport wavelength selection
- OBIS discovery, enable/disable, setpoint control, and CDRH setting control
- Linear, logarithmic, and custom power scans
- Calibration-based setpoint interpolation
- Manual ND-filter-wheel planning with minimal filter changes
- Persistent GUI/acquisition/file/plot preferences
- Bounded power and scalar spectrum-monitor traces
- Spectrum, power-trace, monitor, and calibration CSV export
- Background capture and optional integration-time-scaled subtraction
- Emulated devices for offline testing

## Requirements

- Windows
- Python 3.14
- Newport Power Meter Application/USB driver for a real 2936-R
- Ocean Insight USB support configured for `seabreeze`
- Coherent OBIS USB/serial driver for real laser boxes

Install from the repository root:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
seabreeze_os_setup
```

Optional OpenGL 3D monitor support:

```bash
python -m pip install -e ".[opengl]"
```

Development tools:

```bash
python -m pip install -e ".[dev]"
```

For an offline experiment computer, download wheels on an internet-connected
computer using the same Python version and architecture:

```bash
python -m pip download -d wheels -e ".[opengl,dev]"
```

Then copy the repository and `wheels/` directory to the offline computer:

```bash
python -m pip install --no-index --find-links wheels -e .
```

`OceanDirect` is vendor-distributed and is not included as a normal project
dependency. The benchmark in `benchmarks/oceandirect_averaging.py` can be run
after installing the OceanDirect SDK separately.

## Configuration

Lab-specific defaults are read from `config/lab_defaults.json`. A generic
starting point is provided in `config/lab_defaults.example.json`.

The Windows launcher prefers the untracked local override
`config/lab_defaults.local.json` when that file exists. This allows each lab
computer to keep its own COM ports and DLL path without changing committed
files.

Example:

```json
{
  "newport_dll": "C:/Program Files/Newport/Newport Power Meter Application/Samples/PowerMeterCommands.dll",
  "power_channel": 1,
  "obis_ports": ["COM3", "COM5"],
  "laser_mode": "auto",
  "fallback_emulator": false
}
```

Command-line values override the JSON file.

## Running

One-click lab launcher:

```text
start-gui.bat
```

Everything emulated:

```bash
python gui.py --emulate --laser-mode emulated
```

Real QEPro/Newport and real OBIS boxes:

```bash
python gui.py --real --laser-mode real --obis-ports COM3 COM5
```

Real instruments with laser-emulator fallback:

```bash
python gui.py --real --laser-mode auto --obis-ports COM3 COM5
```

Emulated QEPro/Newport with real lasers:

```bash
python gui.py --emulate --laser-mode real --obis-ports COM3 COM5
```

## Architecture

```text
gui.py                         application entry point and config resolution
controllers/
  device_controller.py         QEPro/Newport worker-thread operations
  laser_controller.py          OBIS worker-thread operations
  instrument_runtime.py        thread ownership and queued request routing
  scan_coordinator.py          power/calibration/filter scan state machine
  file_io_controller.py        dialogs and data export orchestration
  preferences_controller.py    QSettings persistence
core/                           records, settings, units, colors, timing
processing/                     background, smoothing, monitor calculations
devices/                        real and emulated hardware adapters
dialogs/                        modal configuration/details dialogs
panels/                         independent GUI panels and the top-level shell
planning/                       power-scan and ND-filter planning
io_utils/                       CSV, naming, logging, and atomic file writes
validation/                     Newport status and power validation
tests/                          hardware-independent unit tests
troubleshooting/                focused bench diagnostic scripts
benchmarks/                     optional backend benchmarks
```

`MainWindow` is intentionally limited to top-level UI coordination. Blocking
hardware calls are routed to worker objects. Scan and calibration state is owned
by `ScanCoordinator`, not by the window.

## Data files

Spectrum CSV files preserve:

- integration time, averages, correction settings, and boxcar width
- raw counts and counts per second
- Newport readings and status words before/after acquisition
- laser, scan, field, filter, and calibration metadata
- background and averaging metadata
- run identifier and notes

Spectrum, calibration, and bounded power-trace exports use same-directory
atomic temporary files so interrupted writes do not replace a valid target with
a partial file. The continuously streamed full-power log is intentionally not
atomic because it is written throughout the run.

## Tests

Hardware-independent checks:

```bash
python -m compileall -q .
pytest -q
```

Diagnostic tools should be run from the repository root as modules, for
example:

```bash
python -m troubleshooting.obis_probe --list
python -m troubleshooting.test_newport_clean
python -m benchmarks.oceandirect_averaging
```

## Known limitations

- QEPro acquisition and Newport operations currently share one device worker
  thread. Live power polling is suppressed during a blocking spectrum
  acquisition to prevent stale queued requests. Power readings before and after
  the spectrum are still collected.
- Worker shutdown waits for an active blocking vendor call to return. A driver
  call that never returns cannot be cancelled cleanly by Qt alone.
- The OBIS command adapter treats a write with no explicit error response as
  successful. Confirm command acknowledgement behavior on the installed
  firmware before making it stricter.
- GUI boxcar processing is currently applied to the stored spectrum, not only to
  the display. Changing that behavior would alter existing data semantics.
- `QApplication` organization/application names are retained for compatibility
  with existing user settings. Changing them moves the QSettings location.
- OpenGL plotting is optional; the field-power map remains available without
  `PyOpenGL`.

See `AUDIT_REPORT.md` for the refactor scope, preserved design decisions, and
hardware validation checklist.
