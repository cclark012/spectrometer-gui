# Spectrometer GUI Refactor and Audit Report

## Scope

The supplied archive was reviewed module by module. The refactor focused on:

- removing abandoned and duplicate implementations;
- separating hardware adapters, worker-thread routing, scan state, panel logic,
  preferences, and file I/O;
- correcting known scan, status-word, interpolation, threading, persistence,
  and plotting defects;
- reducing avoidable serial/GUI overhead;
- adding hardware-independent regression tests;
- preserving hardware-dependent behavior when changing it without bench
  confirmation would be risky.

## Structural changes

### Main window

`panels/main_window.py` was reduced from approximately 2,465 lines to about 950
lines. It now acts as the top-level UI shell rather than owning every workflow.

Extracted components include:

- `controllers/scan_coordinator.py`: power scans, calibration scans, filter
  prompts, warning handling, and scan timing;
- `controllers/instrument_runtime.py`: worker threads and queued request routing;
- `controllers/file_io_controller.py`: save/open/log operations;
- `controllers/preferences_controller.py`: dataclass/panel/window persistence;
- `panels/spectrum_panel.py`: throttled spectrum rendering and axis state;
- `panels/monitor_views.py`: field-power map and optional 3D view support;
- `panels/main_window_actions.py`: menus, toolbar, and actions.

### Device code

- Real and emulated OBIS implementations are separate.
- Newport is imported lazily so emulator-only startup does not require
  Python.NET or the Newport driver stack.
- Protocols define the common spectrometer, power-meter, and laser-box adapter
  interfaces.
- QEPro and Newport can connect independently.

### Processing and I/O

- Monitor metric calculations, smoothing, and background correction moved to
  `processing/`.
- Calibration, spectrum, power-trace, and filename logic are separated under
  `io_utils/`.
- Spectrum, calibration, and bounded power-trace writes are atomic.
- Legacy prototypes were removed; useful bench scripts were retained under
  `troubleshooting/` and `benchmarks/`.

## Correctness fixes

- Correct worker-thread startup order and queued GUI/worker communication.
- Independent QEPro and Newport connection/failure behavior.
- No stale live-power request queue during a blocking spectrum acquisition.
- Newport PM:PWS status-word validation and configurable retry handling.
- Correct handling of status range/unit fields when combining before/after
  snapshots.
- Laser table state updates without full serial rediscovery after every command.
- Scan plans are regenerated at Run, not reused from a stale Preview.
- Impossible/non-finite scan points fail; clipped setpoints produce visible
  warnings.
- Calibration inverse interpolation rejects out-of-range requests.
- Calibration identity is checked against the selected laser.
- Scan repeats avoid redundant OBIS setpoint writes and settling delays.
- Manual power-meter wavelength state is committed only after hardware
  acknowledgement.
- Integration limits and averaging modes are validated in adapters.
- QEPro feature lists ignore unavailable empty backend lists and detect
  top-level or feature-level averaging setters.
- Background interpolation handles descending grids, duplicate wavelengths,
  mismatched arrays, and invalid integration times.
- Monitor integration and interpolation are independent of wavelength-array
  order.
- Background capture cannot be queued concurrently with another acquisition.
- Axis limits and plot settings persist without being overwritten by new data.
- Power, monitor, and spectrum redraws use bounded-rate dirty-flag updates.
- Configuration JSON is validated before device startup.

## Performance changes

- Removed full OBIS rediscovery after normal setpoint and enable/disable calls.
- Avoided stale power-poll queues during long QEPro acquisition.
- Added plot redraw throttling while retaining every acquired monitor point.
- Cached background response on the active wavelength grid.
- Used online/running averaging rather than storing all spectra in memory.
- Cached QEPro capability probing and hardware-averaging method discovery.
- Kept hardware-only dependencies lazy where practical.
- Avoided repeating setpoint and settling operations for scan repeats at the
  same power.

## Automated validation

The cleaned tree passes:

```text
python -m compileall -q .
pytest -q
```

Result at packaging time:

```text
36 passed
```

The tests cover power scan planning, filter planning, calibration I/O, spectrum
I/O, Newport status decoding, background correction, monitor metrics,
configuration validation, and QEPro adapter edge cases.

## Environment limitation

The audit environment does not contain PySide6, pyqtgraph, pyserial,
python-seabreeze, Python.NET, OceanDirect, or physical instruments. Therefore:

- all Python files were syntax-compiled;
- hardware-independent tests were executed;
- actual Qt window construction, USB/serial behavior, and vendor-DLL calls were
  not executed here.

A bench validation run is still required.

## Deliberately preserved behavior

The following were not changed because they are hardware- or experiment-policy
choices and should be confirmed before altering semantics:

1. **OBIS write acknowledgement**: an empty non-error response is treated as
   success. Some firmware may not return `OK` consistently.
2. **Single QEPro/Newport worker**: this preserves the working connection model.
   Separate workers would permit exposure-synchronous Newport sampling but
   require careful validation of the Newport DLL's thread behavior.
3. **Stored boxcar data**: boxcar smoothing remains part of the saved spectrum.
   Making smoothing display-only would change historical data semantics.
4. **CDRH command fallback**: indexed and unindexed forms are retained because
   Laser Box firmware variants may differ.
5. **QSettings identity**: the existing organization/application names are
   retained so user preferences do not appear to disappear.
6. **Lab filter defaults and local ports**: the active lab config is retained;
   a generic example and local override mechanism were added instead.
7. **Project license**: no license was invented. The prior unsupported license
   declaration was removed from package metadata.

## Bench validation checklist

Before replacing the working lab tree, verify:

1. Start in fully emulated mode and exercise every menu/dialog.
2. Start with QEPro only; acquire and save a spectrum.
3. Start with Newport only; verify live/spectra-only modes and wavelength set.
4. Start with both; verify before/after power metadata and status validation.
5. Connect each OBIS box; refresh, set low power, enable/disable, and CDRH query.
6. Run a short emulated and real setpoint scan with repeats.
7. Run/load/save a calibration and preview expected-actual/filter-planned scans.
8. Confirm manual filter prompts occur only when the planned state changes.
9. Verify axis limits, plot style, file settings, and dock layout persist.
10. Interrupt a spectrum/calibration save and confirm no partial final file is
    left under the requested name.
11. Close during an ordinary idle state and after a completed acquisition.
12. Benchmark OceanDirect separately before enabling it as an acquisition
    backend.

## Recommended next work

After bench validation, the highest-value architectural improvement is an
optional dedicated Newport worker capable of collecting exposure-synchronous
power samples. That should be implemented only after confirming the Newport DLL
can be used safely from its own persistent worker thread.
