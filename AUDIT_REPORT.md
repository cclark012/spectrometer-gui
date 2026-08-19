# Spectrometer GUI Engineering Audit — 2026-08-19

## Scope

The supplied project contains 112 Python files (about 20,500 lines). The review
covered application startup, Qt signal routing, worker-thread boundaries,
instrument adapters, acquisition coordinators, persistence, planning,
visualization settings, tests, and troubleshooting utilities.

This pass intentionally did not invent hardware behavior that could not be
verified without the instruments. Changes were limited to defects supported by
source analysis and hardware-independent regression tests.

## High-impact corrections

### One acquisition owner

`core/sequence_arbiter.py` now provides one lease shared by manual, live,
background, power-scan, calibration, gated, and automatic-tuning workflows.
Main-window requests validate that lease and the originating coordinator before
queuing a spectrum. This prevents overlapping workflows and drops stale timer
callbacks after an abort.

Panel availability is composed from connection state, current acquisition, and
the lease owner. Scan, gated, laser, power-meter, and acquisition controls can no
longer re-enable one another independently. `Disable All` remains available
during automated laser work. Scan/calibration abort and failure exits now issue a
best-effort laser-disable request before returning control to the user.
The always-available `Disable All` action first aborts the owning state machine,
then queues the box-wide disable command so a sequence cannot turn a laser back
on afterward.

### Deterministic spectrum routing and autosave

Completed spectra are routed only to the coordinator that requested them.
Manual/live autosave runs once through the global setting; power-scan and gated
autosave remain coordinator-owned. This fixes both duplicate manual/live files
and gated frames that previously never saved.

Power-scan spectrum handling now returns a consumed flag and scan coordinators
emit explicit owner-aware active-state transitions. Gated inter-frame delay is
applied in one place (the coordinator), eliminating doubled delays from the
planner and coordinator both waiting.

### QEPro connection loss

The QEPro adapter now normalizes SeaBreeze/USB disconnects from spectrum,
capability, hardware-averaging, integration-limit, and TEC operations as
`SpectrometerCommunicationError`. `DeviceController` centralizes the resulting
close/disconnected-state transition for acquisition, background, TEC,
temperature, and capability requests.

### Lossless CSV round trips

Spectrum CSV save/load now round-trips:

- all `SNRMetrics` fields, including invalid results as a real Boolean;
- all gated frame metadata;
- scan, background, power, and correction metadata already supported.

Power-trace CSV output now retains the point source, Newport command status,
and per-channel PM status words. Both writers remain atomic.

### Laser and diagnostics safety

- OBIS public laser operations reject channel 0 before constructing `SOURce`
  commands.
- Laser-emulator fallback reports the connection as emulated and no longer emits
  a transient disconnected state first.
- The duplicate Andor ctypes probe was consolidated into
  `troubleshooting/andor_ctypes_probe.py`. The corrected probe distinguishes
  successful native calls, does not expose undefined output buffers on errors,
  uses the SDK2 vendor return-code table, keeps JSON stdout clean, and supports
  `--help` off Windows.
- Optional Newport, OBIS, and serial-port diagnostics are import-safe and report
  missing bench dependencies from their command-line entry points.

### Themes and packaging

The startup path now creates one `ThemeManager`, preserving the true system
palette for later restoration. Display Settings contains a live theme preview
and a coupled Clone/Customize action. Custom-theme writes are atomic, separators
are more visible, the obsolete QSS file was removed, and the `ui` package plus
SVG assets are included in setuptools package discovery.

Gated preferences are now managed by `PreferencesController` with the other
panels rather than through special-case `QSettings` calls in `MainWindow`.

## Automated verification

The audit environment supplied Python 3.12, while the project targets Python
3.14, and did not include PySide6, pyqtgraph, pyserial, SeaBreeze, Python.NET,
or vendor SDKs. Within that limitation:

```text
115 Python files syntax-compiled successfully
56 hardware-independent tests passed
0 failed
98 non-test modules passed a stubbed optional-dependency import smoke test
AST duplicate-definition check: 0 issues
git diff --check: clean
```

The tests include acquisition arbitration, scan/gated planning, SNR and gated
CSV round trips, power-trace status retention, QEPro disconnect normalization,
Newport validation, configuration, filters, calibration, background processing,
monitor metrics, and filename generation.

## Required bench validation

Before replacing the lab tree:

1. Install the declared Python 3.14 environment and run `pytest -q`.
2. Launch fully emulated mode and exercise every menu/dialog, theme preview,
   live start/stop, background, auto tune, scans, and gated abort.
3. Disconnect/reconnect QEPro during an ordinary acquisition and during TEC and
   capability requests; verify one clean disconnected transition and recovery.
4. Verify Newport before/after power, continuous trace status columns, wavelength
   acknowledgement, and a short calibration.
5. Verify each real OBIS box, low-power setpoint, CDRH variant, enable/disable,
   emergency Disable All, power scan, and gated completion/abort.
6. Confirm gated frame timestamps and autosave count for paired, delayed, and
   transition modes. Software timing is not hardware gating.
7. Run `python -m troubleshooting.andor_ctypes_probe --output report.json
   --verbose` on the Andor computer with Solis closed.

## Remaining architectural limits

- `MainWindow` is still large (about 2,000 lines/109 methods), although hardware,
  sequence state, file I/O, preferences, actions, and processing are delegated.
  A further presenter/dialog split should follow GUI regression coverage rather
  than precede the first bench pass.
- QEPro and Newport share one device worker. A dedicated Newport worker is still
  recommended for exposure-synchronous sampling after confirming DLL thread
  behavior.
- A vendor call that never returns cannot be cancelled safely by Qt; shutdown
  can therefore wait on a blocked worker.
- Gated timing is software scheduled and includes serial, readout, Windows, and
  Qt event-loop latency. Requested and observed request delays are stored.
- The Andor files are diagnostics only. A production Kymera/iDus adapter,
  acquisition worker, capability model, and GUI integration remain future work.
- Real-device acknowledgement and reconnection behavior require the bench tests
  above; passing offline tests is not evidence of hardware validation.
