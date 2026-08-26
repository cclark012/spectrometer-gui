# Spectrometer GUI

Windows/PySide6 acquisition software for magneto-photoluminescence and
power-dependent spectroscopy. The current hardware backends support:

- Ocean Optics / Ocean Insight QEPro through `python-seabreeze`
- Andor iDus SDK2 cameras with a Kymera 328i through the native Solis DLLs
  (opt-in prototype; lab validation is still required)
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
- Bounded SNR recommendations and automatic verification-based tuning
- Software-timed paired, delayed-after-off, transition, and interleaved-decay
  acquisition
- Incremental gated averaging into one mean/standard-deviation series CSV
- Optional per-spectrum Newport reads for maximum-throughput gated acquisition
- Capability-driven Andor grating, wavelength, filter, port, focus, readout,
  gain, and binning controls
- Live theme preview, built-in themes, and a semantic custom-theme editor
- Emulated devices for offline testing

## Requirements

- Windows
- Python 3.14
- Newport Power Meter Application/USB driver for a real 2936-R
- Ocean Insight USB support configured for `seabreeze`
- Coherent OBIS USB/serial driver for real laser boxes
- Andor Solis/SDK2 and ATSpectrograph drivers for the Andor backend

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
  "spectrometer_backend": "qepro",
  "andor_solis_dir": "C:/Program Files/Andor SOLIS",
  "andor_camera_index": 0,
  "andor_spectrograph_index": 0,
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

Opt-in Andor iDus/Kymera backend with the Newport and real OBIS boxes:

```bash
python gui.py --real --spectrometer-backend andor --andor-solis-dir "C:\Program Files\Andor SOLIS" --laser-mode real --obis-ports COM3 COM5
```

Camera and spectrograph indices default to zero. Select another enumerated iDus
at startup with `--andor-camera-index`; live switching between two camera heads
is intentionally deferred until both heads have been enumerated and tested.

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
  device_controller.py         spectrometer/Newport worker-thread operations
  laser_controller.py          OBIS worker-thread operations
  instrument_runtime.py        thread ownership and queued request routing
  scan_coordinator.py          power/calibration/filter scan state machine
  gated_acquisition_coordinator.py software-timed laser/spectrum state machine
  auto_acquisition_coordinator.py bounded SNR tuning state machine
  file_io_controller.py        dialogs and data export orchestration
  preferences_controller.py    QSettings persistence
core/                           records, settings, units, timing, sequence arbiter
processing/                     background, gated averaging, smoothing, monitors
devices/                        QEPro, Andor, Newport, OBIS, and emulated adapters
dialogs/                        modal configuration/details dialogs
panels/                         independent GUI panels and the top-level shell
planning/                       power-scan and ND-filter planning
io_utils/                       CSV, naming, logging, and atomic file writes
validation/                     Newport status and power validation
tests/                          hardware-independent unit tests
troubleshooting/                focused bench diagnostic scripts
benchmarks/                     optional backend benchmarks
```

`MainWindow` is the top-level UI coordinator. Blocking hardware calls are routed
to worker objects, while scan, gated, and automatic-tuning state stays in their
dedicated coordinators. `SequenceArbiter` grants exactly one workflow ownership
of acquisition controls at a time.

## Data files

Spectrum CSV files preserve:

- integration time, averages, correction settings, and boxcar width
- raw counts and counts per second
- Newport readings and status words before/after acquisition
- laser, scan, field, filter, and calibration metadata
- background and averaging metadata
- complete gated-frame and SNR metadata
- acquisition-call start/midpoint/end timing for software-gated frames
- run identifier and notes

When **Averaged series** is selected on the Gated tab, repeated frames are
combined incrementally and saved as one CSV containing a mean and sample
standard deviation for every state/delay. This avoids retaining or writing every
individual frame.

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
python -m troubleshooting.andor_ctypes_probe --output andor_report.json --verbose
python -m benchmarks.oceandirect_averaging
```

## Known limitations

- QEPro acquisition and Newport operations currently share one device worker
  thread. Live power polling is suppressed during a blocking spectrum
  acquisition to prevent stale queued requests. Power readings before and after
  the spectrum are still collected.
- Worker shutdown waits for an active blocking vendor call to return. A driver
  call that never returns cannot be cancelled cleanly by Qt alone.
- Gated acquisition is software timed, not hardware triggered. Requested and
  observed request and acquisition-call delays are recorded, but
  Windows/serial/readout latency must be characterized on the bench. A 1 ms
  delay grid does not imply 1 ms temporal resolution when the exposure/readout
  window is longer.
- The Andor adapter is an opt-in prototype. The supplied probe saw the Kymera but
  no SDK2 camera, so camera acquisition, native setter signatures, calibration,
  and cooler behavior still require a powered-camera bench test. Step-and-Glue
  remains deferred as a software scan/overlap/merge workflow.
- OBIS ON commands are acknowledged and read back. Only channels verified ON
  receive a one-second safety check; there is no idle polling while all channels
  are OFF. The physical-key test must confirm that the installed firmware's
  emission-state query reflects the interlock rather than only the latched
  command state.
- GUI boxcar processing is currently applied to the stored spectrum, not only to
  the display. Changing that behavior would alter existing data semantics.
- `QApplication` organization/application names are retained for compatibility
  with existing user settings. Changing them moves the QSettings location.
- OpenGL plotting is optional; the field-power map remains available without
  `PyOpenGL`.

See `AUDIT_REPORT.md` for the refactor scope, preserved design decisions, and
hardware validation checklist.
