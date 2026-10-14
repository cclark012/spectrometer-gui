# October 7 prototype update

Revision 2026-10-07.1. Requested October 7; work continued October 8, 2026.
Built on the October 6 candidate derived from the supplied September 14 archive.

## Fixes implemented first

- Removed the undefined `parameters_enabled` reference in smoothing updates. All smoothing and background availability controls now share the acquisition/sequence state implementation.
- Preserved the September 14 Andor connection path. Seven complete driver/runtime/configuration/entry files are byte-identical, and ten configuration/routing components match by syntax tree. No I2C connection changes were made. The reported physical connection failure remains unconfirmed without the lab error log and hardware.
- Moved parameter scans into the left-side **Scans** workspace. A parameter selector switches between the existing laser power/calibration panel and exposure, Kymera center, scheduled-time or temperature scans. It is locked during a sequence so its Abort control stays accessible.
- Replaced the fixed 130-pixel laser list with content-based height capped at 34% of available screen height. Eight emulator rows fit when screen space permits; scrolling remains available on smaller displays.
- Set explicit plot layers: background below unsmoothed data below the smoothed result. Application Plot style now provides separate colors, opacity and widths for these traces, custom colors, spectrum/monitor/power opacity, independent x/y auto-range margins, grid opacity/visibility and legend visibility. Plot-style and SNR settings scroll on smaller screens.
- Added range-independent SNR using 8/16-pixel locally detrended blocks, standard deviation or MAD, and a trimmed block average. The default estimator is 16-pixel standard deviation; SNR remains disabled until enabled. Manual windows remain selectable. Signal uses the maximum of a nine-pixel smoothed spectrum above its 10th percentile. Saved spectra retain the estimator description.

## Analysis prototypes

The new **Analysis and comparisons** tab processes copies of acquired/loaded spectra. Acquisition arrays, the main spectrum display, monitor calculations and autosave retain their existing acquisition semantics. Derived results have a separate export format.

- Multi-spectrum overlays, optional display normalization, reference selection and wavelength-aligned residuals. Residuals retain original units even when overlays are normalized.
- Constant offset, linear and quadratic baseline subtraction, with explicit baseline-only wavelength intervals for fits. Previously saved background files load as counts/s references; they can be subtracted in Analysis after matching units. This is not a live background bank.
- Imported response profiles, including the supplied grating 1 detector 1 file unchanged: 2046 coefficients, 136–1158.5 nm, maximum 1. Profiles require grating/detector identifiers. Correction follows baseline subtraction and interpolates onto the experimental grid; out-of-coverage values either fail validation or are explicitly masked.
- New response profiles from selected lamp acquisitions. Each lamp segment is baseline corrected and exposure normalized. The supplied lamp equation uses x in nm. Per-center lamp/measured coefficients are combined, resampled and normalized by one global maximum. Profiles preserve source records, coverage, formula, sampling, serial and known gain/binning constraints.
- Uncalibrated stitching with edge-weighted overlap blending and optional overlap scale matching. Calibrated stitching uses a common response profile and preserves its relative scale. Both preserve gaps and reject known conflicting detector/filter/gain metadata; unequal exposures require counts/s normalization.
- Peak detection, half-prominence widths, signed band integration and positive-signal centroid. Results export as JSON with source processing history. No curve fitting or ARPLS is implemented.

## Compatibility and evidence

Acquisition CSV remains schema v5; its optional SNR description is backward compatible. Analysis spectrum and response profile CSVs use separate file types with schema version 1. SciPy is added for peak analysis and is imported only when that operation is requested.

174 pure test invocations and 10 coordinator/worker checks pass using the documented offline runners. The 17 connection comparisons pass. Compilation passes on Python 3.12.14. The project target remains Python 3.14. The real Qt smoke test reports blocked because PySide6 is absent; package installation attempts failed. No hardware or rendered GUI validation is claimed.

Gated acquisition, laser command semantics and Lake Shore read-only communication are unchanged. Drift/randomization, recipes, ARPLS/fitting, reference banks, absorption/transmission and long-run temperature features remain deferred according to the revised priorities.
