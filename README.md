# Spectrometer GUI

Windows/PySide6 acquisition software for magneto-photoluminescence and
power-dependent spectroscopy. The current hardware backends support:

- Ocean Optics / Ocean Insight QEPro through `python-seabreeze`
- Andor iDus SDK2 cameras with a Kymera 328i through the native Solis DLLs
  (the current DU401_BVF requires `atmcd64d_legacy.dll`)
- Newport 2936-R through Newport `PowerMeterCommands.dll` and `pythonnet`
- Coherent OBIS Laser Boxes through USB virtual COM ports
- Emulated spectrometer, power meter, and laser boxes for development

The spectrometer, Newport, and laser boxes have independent real, emulated, and
disconnected modes. They also own separate worker queues: a slow Newport call
does not block the QEPro/Andor worker, and no device adapter calls another
adapter directly. Cross-instrument sequencing is handled by the GUI runtime.

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
- Background capture and strict configuration-matched subtraction
- Bounded SNR recommendations and automatic verification-based tuning
- Software-timed paired, delayed-after-off, transition, and interleaved-decay
  acquisition
- Incremental gated averaging into one mean/standard-deviation series CSV
- Exposure-aware robust gated timing flag/discard/abort controls
- Optional per-spectrum Newport reads for maximum-throughput gated acquisition
- Capability-driven Andor grating, wavelength, filter, port, focus, readout,
  gain, and binning controls
- Live theme preview, built-in themes, and a semantic custom-theme editor
- Emulated devices for offline testing
- Session-time source selection for QEPro, Andor, Newport, OBIS, emulated, and
  disconnected combinations
- Bounded rotating application logs for connection, workflow, file, and error
  events
- Compact schema-v2 spectrum files with canonical adapter-output counts and
  conditional processed/provenance blocks

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
  "spectrometer_mode": "real",
  "spectrometer_backend": "auto",
  "qepro_serial_number": "QEP05831",
  "power_meter_mode": "real",
  "andor_solis_dir": "C:/Program Files/Andor SOLIS",
  "andor_camera_dll": "atmcd64d_legacy.dll",
  "andor_camera_index": 0,
  "andor_spectrograph_index": 0,
  "obis_ports": ["COM3", "COM5"],
  "laser_mode": "auto",
  "fallback_emulator": false,
  "spectrometer_fallback_emulator": false,
  "power_meter_fallback_emulator": false,
  "newport_process_isolation": true
}
```

Command-line values override the JSON file. The legacy `--real` and `--emulate`
flags remain presets for the spectrometer and power meter; explicit
`--spectrometer-mode` or `--power-meter-mode` values take precedence. Use
**Tools > Instrument Connections** to change any source for the current session.
When more than one SeaBreeze device is present, set `qepro_serial_number` (or
`--qepro-serial-number`) to bind the QEPro source to a specific instrument.
`auto` quietly tries that configured QEPro first and then the Andor pair. Use an
explicit backend when testing one driver or if both sources can ever be visible
at once.

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
python gui.py --real --spectrometer-backend andor --andor-solis-dir "C:\Program Files\Andor SOLIS" --andor-camera-dll atmcd64d_legacy.dll --laser-mode real --obis-ports COM3 COM5
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

Real Andor with an emulated power meter and no lasers:

```bash
python gui.py --spectrometer-mode real --spectrometer-backend andor --power-meter-mode emulated --laser-mode disconnected
```

Application events are written to an optional 5 MiB rotating log with five
backups. Open its location from **Tools > Open Application Log Folder**. Use
`--no-file-logging` to disable it. Handled/startup-absence messages remain in the
GUI and optional file; the terminal threshold is `ERROR`, so terminal output is
reserved for internal or uncaught failures. Use `--log-level DEBUG` only during
focused diagnosis because high-rate logging adds I/O.

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the component/communication diagram,
thread boundaries, signal contracts, and major-method index. `MainWindow` is the
top-level UI coordinator. `SequenceArbiter` grants one workflow ownership of
acquisition controls at a time, while `InstrumentRuntime` coordinates optional
before/after Newport samples across otherwise independent workers.

## Data files

Schema-v2 spectrum CSV files preserve:

- integration time, requested/applied averaging, and correction settings
- canonical adapter-output counts, plus a processed column only when needed
- Newport readings and status words only when power was measured
- laser, scan, field, filter, and calibration metadata
- background and averaging metadata
- complete gated-frame and SNR metadata
- controller-call and backend-specific timing bounds, midpoint estimate,
  uncertainty, robust quality decision, and per-average windows for gated frames
- run identifier and notes

When **One averaged series file** is selected on the Gated tab, repeated frames are
combined incrementally and saved as one CSV containing a mean and sample
standard deviation for every state/delay. The file also records guard counts and
mean/std/min/max/median/p95/p99 timing summaries. This avoids writing every
individual frame.

See `docs/SPECTRUM_METADATA_CATALOG_20260902.md` for every field, type,
write condition, plain-English meaning, usefulness, and legacy mapping.

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
python -m troubleshooting.andor_dll_matrix_probe --output andor_dll_matrix.json
python -m troubleshooting.newport_reconnect_probe --dll "C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll" --output newport_reconnect.json
python -m benchmarks.oceandirect_averaging
```

## Known limitations

- Spectrum, Newport, and OBIS operations use separate workers. When **Measure
  power** is enabled, the runtime deliberately waits for one Newport read before
  and one after each spectrum; that synchronization still limits the requesting
  acquisition's rate. Live Newport polls are coalesced so at most one is queued.
- Worker shutdown waits for an active blocking vendor call to return. A driver
  call that never returns cannot be cancelled cleanly by Qt alone.
- The real Newport driver is isolated in a restartable child process by default.
  This is designed to recover vendor/CLR discovery after a power cycle, but the
  exact off/on/re-enumeration sequence still needs lab validation. Use
  `--no-newport-process-isolation` only for comparison or diagnosis.
- Gated acquisition is software timed, not hardware triggered. Requested and
  observed request and acquisition-call delays are recorded, but
  Windows/serial/readout latency must be characterized on the bench. A 1 ms
  delay grid does not imply 1 ms temporal resolution when the exposure/readout
  window is longer.
- The 2026-09-02 lab probe enumerated DU401_BVF serial `26970` (1024 × 127,
  26 µm pixels) and Kymera `KY-4444` through `atmcd64d_legacy.dll`, and the GUI
  acquired spectra. A blocked-light/calibrated regression, cooler shutdown
  policy, readout-control matrix, port mapping, and DU490A InGaAs switching are
  still required. Step-and-Glue remains deferred as a software
  scan/overlap/merge workflow.
- OBIS ON commands are acknowledged and read back. Only channels verified ON
  receive a one-second safety check; there is no idle polling while all channels
  are OFF. The physical-key test must confirm that the installed firmware's
  emission-state query reflects the interlock rather than only the latched
  command state.
- GUI boxcar processing remains the displayed/result array, but schema v2 also
  preserves the pre-background/pre-smoothing adapter output as the canonical
  data column. Additional smoothing kernels and a kernel preview are deferred.
- `QApplication` organization/application names are retained for compatibility
  with existing user settings. Changing them moves the QSettings location.
- OpenGL plotting is optional; the field-power map remains available without
  `PyOpenGL`.

See [`REVIEW_20260902.md`](REVIEW_20260902.md) for the issue-by-issue change
summary, probe interpretation, architectural differences, and ordered hardware
checklist.
