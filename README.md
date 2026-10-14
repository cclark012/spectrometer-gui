# Spectrometer GUI

Windows/PySide6 acquisition software for magneto-photoluminescence and
power-dependent spectroscopy. The current hardware backends support:

- Ocean Optics / Ocean Insight QEPro through `python-seabreeze`
- Andor iDus SDK2 cameras with a Kymera 328i through the native Solis DLLs
  (the current DU401_BVF requires `atmcd64d_legacy.dll`)
- Newport 2936-R through Newport `PowerMeterCommands.dll` and `pythonnet`
- Coherent OBIS Laser Boxes through USB virtual COM ports
- Optional read-only Lake Shore 335 temperature monitoring through its USB COM port
- Emulated spectrometer, power meter, and laser boxes for development

The spectrometer, Newport, and laser boxes have independent real, emulated, and
disconnected modes. They also own separate worker queues: a slow Newport call
does not block the QEPro/Andor worker, and no device adapter calls another
adapter directly. Cross-instrument sequencing is handled by the GUI runtime.

## October 7 candidate

Start with [the current feature and architecture guide](docs/GUI_FEATURES_DESIGN_AND_ARCHITECTURE_20261007.docx),
[verification and lab steps](REVIEW_20261007.md), and [the changelog](CHANGELOG_20261007.md).
Current memory: `ChatGPTMemory100726_1.json`. Earlier dated files are history.
Release 2026-10-07.1 was continued on October 8.

This candidate fixes smoothing initialization, groups scans on the left, sizes the
laser list dynamically, puts smoothed data on top, expands plot styling and adds
benchmarked automatic SNR. The Andor connection path matches September 14;
physical connectivity remains unverified and no I2C changes were made.

Use the left **Scans** selector for laser power/calibration, exposure, Kymera center,
scheduled time or temperature conditions. A 10 s series is Scheduled times, Linear,
Start 0, Stop 100, Count 11. Every point autosaves. Temperature targets are observed,
not commanded. The existing restoration/laser-off policies still apply.

Use **Analysis and comparisons** to add current/load spectra, compare references,
subtract constant/linear/quadratic or saved baselines, apply response profiles,
derive new lamp coefficients, analyze peaks/bands and stitch segments. The supplied
grating 1 detector 1 coefficients are bundled but not enabled automatically.
Analysis preserves source arrays and exports separate derived files. Acquisition
and monitor values are not response-corrected automatically. Saved backgrounds
load in Analysis as counts/s references; the live background path is unchanged.

SciPy is added for peak analysis. Update the lab environment with
`python -m pip install -e ".[dev]"`. Target Python remains 3.14. Verification here:
174 pure test invocations, 10 coordinator/worker checks, 17 baseline comparisons,
and source compilation. Qt/GUI/hardware acceptance remains open; the real Qt
smoke attempt was blocked by unavailable PySide6. Run
`python verification/gui_smoke.py` on the target machine.

Use **Temperature** for explicit Lake Shore COM or emulator monitoring. Commands
remain read-only, and full-run temperature logging remains deferred. Gated timing
algorithms remain at the September 14 baseline.

## Current capabilities

- Single and live spectrum acquisition, including an armed SDK2
  Run-Till-Abort/circular-buffer path for Andor rapid workflows
- Software averaging and optional backend/device averaging
- Status-bar progress for long exposures and many-average acquisitions
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
- Bounded retain-until-valid timing mode for fixed-delay and interleaved-decay
  averages, with selectable exposure-start or exposure-midpoint reference
- Persistent timing baselines separated by spectrometer backend and gated mode,
  with partial accepted averages retained on abort/failure
- Optional per-spectrum Newport reads for maximum-throughput gated acquisition
- Independent spectrum-associated Newport channel 1/channel 2 selection and a
  configurable non-blocking other-channel warning
- Capability-driven Andor grating, wavelength, filter, port, readout, and gain
  controls; focus is currently read-only and verified FVB binning is fixed at 1
- Guided iDus cooler shutdown with a persistent incomplete-warm-up marker
- Nine smoothing choices, selectable edge behavior, inline kernel preview, and
  detailed weight/Fourier-response view
- Live theme preview, built-in themes, and a semantic custom-theme editor
- Emulated devices for offline testing
- Session-time source selection for QEPro, Andor, Newport, OBIS, emulated, and
  disconnected combinations
- Bounded rotating application logs for connection, workflow, file, and error
  events
- Compact schema-v5 spectrum files with canonical adapter-output counts and
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

The tracked `config/lab_defaults.json` is intentionally hardware-neutral and
starts disconnected. Copy `config/lab_defaults.example.json` to the ignored
`config/lab_defaults.local.json`, then fill in that workstation's paths,
serial number, modes, and COM ports.

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

The current non-code product records are:

- `docs/GUI_FEATURES_DESIGN_AND_ARCHITECTURE_20260914.docx`
- `docs/CHANGELOG_20260909_TO_20260914.docx`
- `docs/OUTSTANDING_TASKS_AND_KNOWN_ISSUES_20260914.docx`
- `docs/GATED_TIMING_AND_LOW_LIGHT_STRATEGIES_20260914.docx`

## Data files

Schema-v4 spectrum CSV files preserve:

- integration time, requested/applied averaging, and correction settings
- canonical adapter-output counts, plus a processed column only when needed
- only the selected Newport channel readings when power was measured, plus
  abnormal status evidence when present
- laser, scan, field, filter, and calibration metadata
- background and averaging metadata
- complete gated-frame and SNR metadata
- controller-call and backend-specific timing bounds, midpoint estimate,
  uncertainty, robust quality decision, and per-average windows for gated frames
- requested and SDK-applied exposure values when they differ, plus available
  detector exposure/readout/keep-clean/frame-period timing
- Run-Till-Abort mode, driver frame index, pre-request discarded backlog, and
  pending-buffer depth when applicable
- compact software/adapter/vendor-runtime provenance and readback-confirmed
  Andor grating, wavelength, filter/focus/offset, amplifier, speed, gain, binning,
  and optional port state
- strict background compatibility signature without duplicating all background
  acquisition settings
- gated pump duration, frame gap, repeat count, and repeat gap
- run identifier and notes

When **One averaged series file** is selected on the Gated tab, repeated frames are
combined incrementally and saved as one schema-v5 CSV containing a mean and
sample standard deviation for every state/delay. The file carries the same
instrument/acquisition/background provenance, explicit selected-power-channel
columns, guard counts, and mean/std/min/max/median/p95/p99 timing summaries. This
avoids writing every individual frame.

The **Retain N inside fixed window** guard is available for averaged fixed-delay
and interleaved-decay runs. **Repeats** is the required accepted count per trace.
The coordinator discards attempts outside the selected exposure-start or
exposure-midpoint window and repeats complete pump/OFF cycles until every trace
reaches that count or the finite attempt multiplier is exhausted. Per-frame
Newport measurement is disabled in this mode because it would add queued I/O
before the detector request. A partial average and explicit failure reason are
saved if the ceiling is reached.

See `docs/SPECTRUM_METADATA_SCHEMA_20260909.docx` for every field, type,
write condition, plain-English meaning, usefulness, consolidation decision, and
legacy mapping.

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
python -m troubleshooting.andor_timing_probe --camera-dll atmcd64d_legacy.dll --vertical-speed-index 0 --vertical-speed-index 1 --frames 50 --output andor_timing.json
python -m troubleshooting.andor_stream_gap_probe --camera-dll atmcd64d_legacy.dll --exposure-ms 15 --frames 100 --gap-ms 100 --output andor_stream_gap.json
python -m troubleshooting.andor_stream_gap_probe --camera-dll atmcd64d_legacy.dll --exposure-ms 15 --frames 100 --gap-ms 100 --align-midpoint --output andor_stream_gap_aligned.json
python -m troubleshooting.andor_controls_probe --camera-dll atmcd64d_legacy.dll --output andor_controls_readback.json
python -m troubleshooting.newport_reconnect_probe --dll "C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll" --output newport_reconnect.json
python -m benchmarks.oceandirect_averaging
```

`andor_stream_gap_probe` exercises the same session-scoped adapter path as the
GUI while deliberately allowing frames and SDK notifications to accumulate
between calls. Its JSON records discarded frames, stale wait notifications,
driver indices, target-alignment discards, estimated exposure position, and the
first failure without retaining spectral arrays. Add `--align-midpoint` to
exercise the gated rule that rejects a stream frame whose estimated exposure
midpoint precedes the request target.

`andor_controls_probe` uses the same composite adapter as the GUI. It is
read-only by default. Supplying one or more `--set-*` options still does not move
hardware unless `--apply` is also present, and applied test changes are restored
before shutdown unless `--keep-changes` is explicitly supplied.

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
  window is longer. For Andor streams, the requested delay is an exposure-
  midpoint target. Frames whose estimated midpoint precedes that target are
  discarded. At a zero-millisecond target, the exposure start may still be
  negative because a finite exposure can straddle laser OFF while its midpoint
  occurs afterward. The laser transition origin is when the GUI receives a
  successful command/readback completion, not an optical edge from the laser.
  A fixed-window retention result therefore constrains recorded software timing,
  not physical photon timing; use a photodiode/DAQ or hardware trigger to measure
  that relationship before claiming an absolute tolerance.
- SDK2 reports horizontal readout speeds in MHz: `0.1 MHz` means `100 kHz`, not
  `100 MHz`. Exposure duration and minimum full-frame period are separate. The
  Andor details dialog reports both. Positive whole-millisecond requests remain
  available; saved spectra use the SDK-applied exposure and retain a differing
  request, so a clamped short exposure is never mislabeled.
- The 2026-09-02 lab probe enumerated DU401_BVF serial `26970` (1024 × 127,
  26 µm pixels) and Kymera `KY-4444` through `atmcd64d_legacy.dll`, and the GUI
  acquired spectra. The supplied 2026-09-10 timing runs show Run-Till-Abort at
  about 60 Hz for 15 ms and 19.95 Hz for 50 ms, while re-armed calls take roughly
  117-200 ms. The 2026-09-11 gap probe completed 100/100 requests, recovered 99
  stale notifications, and advanced driver indices monotonically from 1 to 694;
  GUI Live and gated multi-frame acquisition were also confirmed. The 2026-09-14
  midpoint-aligned gap probe completed 100/100 requests with every selected
  exposure midpoint after the request target. Reversible grating, wavelength,
  filter, flipper, horizontal/vertical speed, and available gain changes passed
  and restored. FVB binning 4 returned `DRV_NOT_SUPPORTED`; only binning 1 is
  advertised. A focus request of 400 from readback 423 returned
  `ATSPECTROGRAPH_P2INVALID`, so focus is visible but read-only until its native
  contract/range is resolved. Short requested exposures, cooler shutdown,
  calibrated spectra, port mapping, and DU490A InGaAs switching still require
  bench validation. Step-and-Glue remains deferred as a software scan/overlap/
  merge workflow.
- OBIS ON commands are acknowledged and read back. Only channels verified ON
  receive a one-second safety check; there is no idle polling while all channels
  are OFF. The physical-key test must confirm that the installed firmware's
  emission-state query reflects the interlock rather than only the latched
  command state.
- GUI smoothing remains the displayed/result array, while schema v4 preserves
  the pre-background/pre-smoothing adapter output as canonical data. Supported
  kernels are None, Boxcar, Hann, Hamming, Gaussian, Triangle, Blackman, Cosine,
  and Sine; Savitzky-Golay remains deferred until its parameter model is chosen.
- `QApplication` organization/application names are retained for compatibility
  with existing user settings. Changing them moves the QSettings location.
- OpenGL plotting is optional; the field-power map remains available without
  `PyOpenGL`.

See [`REVIEW_20260909.md`](REVIEW_20260909.md) for the issue-by-issue change
summary, probe interpretation, architectural differences, and ordered hardware
checklist.
