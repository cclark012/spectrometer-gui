# Changes: 2026-09-14 → 2026-10-06

## Power channels

Both monitors have independent Channel 1/2 selectors. Selecting only one spectrum-associated channel selects it in both monitors. With both channels associated, each monitor keeps its explicit display selection. Power statistics, normalization, power axes, field-power maps, 3D values, toolbar readout and exported monitor display values use the selected channel. A channel absent from a spectrum association is unknown, not a fallback to channel 1.

## Monitor timers

Reset timer sets zero to the current monotonic time and rebases retained history to negative elapsed times. New readings use that origin. Clear remains a separate action. UTC acquisition timestamps, scan clocks, and full-run power logging are unchanged.

## Smoothing display

The spectrum plot retains a colored unsmoothed trace alongside the final smoothed curve. Unsmoothed means after background subtraction but before smoothing. Both stages are saved and restored in new spectrum files. Older files with background subtraction cannot reconstruct this stage; no substitute curve is fabricated.

## Background controls

Save background exports capture counts, counts/s, exposure, averaging, correction flags, timestamp and strict compatibility signature. Plot background overlays the captured counts on its own wavelength grid and labels them as capture counts. Startup subtraction is disabled until an in-memory background exists. Loading a saved background for reuse is a future task.

## Andor details

Timing labels and integration-limit text refresh in the open dialog after Apply and verify succeeds. Values describe the applied configuration; editing a speed alone does not command the camera. Minimum frame period remains distinct from applied minimum exposure.

## Toolbar and layout

The acquisition toolbar contains the base filename and Autosave checkbox, synchronized with existing file settings. Suffixes, paths, enumeration and other options remain in Application Settings. The laser table is capped at 130 logical pixels, approximately two-thirds of its former default table height; scrolling retains access to all rows.

## Ordinary parameter scans

A new Parameter scans tab provides exposure, Kymera center, scheduled-time and temperature-target scans. Custom lists, linear/logarithmic point generation, repeats and settling are supported, with a maximum of 10000 acquisitions. Generated exposures are rounded to whole milliseconds. Existing power scans and gated coordinators remain separate.

## Temperature monitoring

A separate Temperature tab connects explicitly to a Lake Shore 335 USB virtual COM port or emulator. It displays channels A/B in kelvin, status and reading age, and exports a bounded history. Its independent worker sends identification and reading/status queries only; it never changes heaters, PID settings or temperature setpoints.

## Verification

Python compilation: All delivered Python sources compiled under CPython 3.12.14. The project target remains Python 3.14; target-interpreter validation is outstanding.

Hardware-independent suite: 155 invocations passed with the repository-specific offline compatibility runner. This includes all 145 original invocations plus 10 added checks. The runner supplies only the pytest constructs used here (raises, scalar approx, parametrization, temporary paths); it is not an installed pytest run.

Coordinator and worker logic: 9 checks passed with a synchronous signal/timer harness: eight scan state-machine tests and one real emulator-backed DeviceController processing test. This validates explicit transitions and arrays, not Qt queued delivery, QThread behavior or rendering.

Qt/UI checks: Four target-environment Qt tests were added for power channel/timer behavior, smoothing overlay, monitor map/export, and timing-label refresh. They were not executed here. Full MainWindow startup and interactive workflow checks remain required.

Environment limitation: PySide6, pyqtgraph, pytest and pyserial were unavailable. Attempts to install dependencies from the configured package source failed. No vendor DLL or physical instrument was available; hardware and graphical results are not claimed.
